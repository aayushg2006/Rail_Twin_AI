"""Train + evaluate the ETA / delay / conflict models (Phase 4).

Run:  python -m app.prediction.train  [n_episodes]

Generates STOCHASTIC twin episodes, splits by episode, trains XGBoost models and
compares each against the deterministic baseline the twin already computes - so
"why AI?" has an answer. Writes artifacts + metrics.json + registry.json under
app/prediction/artifacts/.

Expect the ETA error to be far larger than the 1.95 s the previous circular
setup reported. That is the point: these targets are what actually happened.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

import numpy as np
import pandas as pd
from sklearn.metrics import (f1_score, mean_absolute_error, precision_score,
                             recall_score, roc_auc_score)
from xgboost import XGBClassifier, XGBRegressor

from ..config import settings
from .dataset import generate_dataset, split_by_episode
from .features import FEATURE_NAMES
from .registry import ModelRecord, ModelRegistry


def _rmse(y, p) -> float:
    return float(np.sqrt(np.mean((np.asarray(y) - np.asarray(p)) ** 2)))


def _reg_metrics(y, pred, base) -> dict:
    return {
        "mae": round(float(mean_absolute_error(y, pred)), 2),
        "rmse": round(_rmse(y, pred), 2),
        "baseline_mae": round(float(mean_absolute_error(y, base)), 2),
        "baseline_rmse": round(_rmse(y, base), 2),
        "n_test": int(len(y)),
    }


def _fit_error_model(tr, te, model, X, target, name, art, records, metrics,
                     dataset_version, now) -> None:
    """A second model predicting how wrong the first one is on THIS state.

    Confidence used to be `1 - mae/600`, a per-model constant dressed up as a
    per-prediction number. Here the interval is estimated from the state itself:
    a train sitting behind a contended junction genuinely has a wider ETA
    distribution than one running clear, and this says so.
    """
    residual_tr = np.abs(np.asarray(tr[target]) - model.predict(tr[X]))
    err = XGBRegressor(n_estimators=180, max_depth=4, learning_rate=0.08,
                       subsample=0.9, colsample_bytree=0.9,
                       random_state=settings.seed, n_jobs=0)
    err.fit(tr[X], residual_tr)
    residual_te = np.abs(np.asarray(te[target]) - model.predict(te[X]))
    predicted = err.predict(te[X])
    metrics[name] = {
        "mae": round(float(mean_absolute_error(residual_te, predicted)), 2),
        "mean_actual_error": round(float(residual_te.mean()), 2),
        "correlation": round(float(np.corrcoef(residual_te, predicted)[0, 1]), 3),
        "n_test": int(len(residual_te)),
    }
    err.save_model(os.path.join(art, f"{name}.ubj"))
    records[name] = ModelRecord(name, "2.0.0", now, dataset_version, X,
                                metrics[name], f"{name}.ubj")


def train_all(n_episodes: int = 40) -> dict:
    print(f"[data] generating {n_episodes} twin episodes …")
    df = generate_dataset(n_episodes=n_episodes)
    tr, va, te = split_by_episode(df)
    dataset_version = f"twin-{n_episodes}ep-{len(df)}rows"
    print(f"[data] rows: train={len(tr)} val={len(va)} test={len(te)}  ({dataset_version})")

    art = settings.artifacts_dir
    os.makedirs(art, exist_ok=True)
    reg = ModelRegistry(art)
    records: dict[str, ModelRecord] = {}
    metrics: dict = {}
    now = dt.datetime.utcnow().isoformat()
    X = FEATURE_NAMES

    # ---- ETA (remaining travel time) ----
    tr_e = tr.dropna(subset=["target_eta"]); te_e = te.dropna(subset=["target_eta"])
    eta = XGBRegressor(n_estimators=250, max_depth=5, learning_rate=0.08, subsample=0.9,
                       colsample_bytree=0.9, random_state=settings.seed, n_jobs=0)
    eta.fit(tr_e[X], tr_e["target_eta"])
    p = eta.predict(te_e[X])
    # Baseline: the deterministic projection the twin already computes.
    base = te_e["baseline_remaining"].values
    metrics["eta"] = _reg_metrics(te_e["target_eta"], p, base)
    eta.save_model(os.path.join(art, "eta.ubj"))
    records["eta"] = ModelRecord("eta", "2.0.0", now, dataset_version, X, metrics["eta"], "eta.ubj")
    _fit_error_model(tr_e, te_e, eta, X, "target_eta", "eta_err", art, records,
                     metrics, dataset_version, now)

    # ---- Delay (future delay) ----
    delay = XGBRegressor(n_estimators=250, max_depth=5, learning_rate=0.08, subsample=0.9,
                         colsample_bytree=0.9, random_state=settings.seed, n_jobs=0)
    delay.fit(tr[X], tr["target_lateness"])
    p = delay.predict(te[X])
    # Baseline: assume the current lateness simply persists.
    base = te["current_lateness_seconds"].values
    metrics["delay"] = _reg_metrics(te["target_lateness"], p, base)
    delay.save_model(os.path.join(art, "delay.ubj"))
    records["delay"] = ModelRecord("delay", "2.0.0", now, dataset_version, X, metrics["delay"], "delay.ubj")
    _fit_error_model(tr, te, delay, X, "target_lateness", "delay_err", art, records,
                     metrics, dataset_version, now)

    # ---- Conflict (within horizon) ----
    conf = XGBClassifier(n_estimators=300, max_depth=5, learning_rate=0.08, subsample=0.9,
                         colsample_bytree=0.9, random_state=settings.seed, n_jobs=0,
                         eval_metric="logloss")
    conf.fit(tr[X], tr["target_conflict"])
    prob = conf.predict_proba(te[X])[:, 1]
    pred_bin = (prob >= 0.5).astype(int)
    # baseline rule: congested junction OR tight headway
    # Baseline rule a controller could apply unaided: contended junction
    # ahead, or a train close behind on the same road.
    base_bin = ((te["junction_congestion"] > 0) | (te["headway_seconds"] < 120)).astype(int)
    y = te["target_conflict"].astype(int)
    cm = {
        "precision": round(float(precision_score(y, pred_bin, zero_division=0)), 3),
        "recall": round(float(recall_score(y, pred_bin, zero_division=0)), 3),
        "f1": round(float(f1_score(y, pred_bin, zero_division=0)), 3),
        "baseline_precision": round(float(precision_score(y, base_bin, zero_division=0)), 3),
        "baseline_recall": round(float(recall_score(y, base_bin, zero_division=0)), 3),
        "baseline_f1": round(float(f1_score(y, base_bin, zero_division=0)), 3),
        "positives": int(y.sum()), "n_test": int(len(y)),
    }
    if y.nunique() > 1:
        cm["roc_auc"] = round(float(roc_auc_score(y, prob)), 3)
        cm["baseline_roc_auc"] = round(float(roc_auc_score(y, base_bin)), 3)
    metrics["conflict"] = cm
    conf.save_model(os.path.join(art, "conflict.ubj"))
    records["conflict"] = ModelRecord("conflict", "2.0.0", now, dataset_version, X, cm, "conflict.ubj")

    # feature training ranges (for out-of-distribution detection at inference)
    ranges = {c: [float(tr[c].min()), float(tr[c].max())] for c in X}
    with open(os.path.join(art, "feature_ranges.json"), "w") as fh:
        json.dump(ranges, fh, indent=2)
    with open(os.path.join(art, "metrics.json"), "w") as fh:
        json.dump(metrics, fh, indent=2)
    reg.save(records)

    print("\n=== ETA (remaining travel time, seconds) ===")
    print(f"  XGBoost  MAE {metrics['eta']['mae']:.1f}  RMSE {metrics['eta']['rmse']:.1f}")
    print(f"  baseline MAE {metrics['eta']['baseline_mae']:.1f}  RMSE {metrics['eta']['baseline_rmse']:.1f}")
    print("=== DELAY (future delay, seconds) ===")
    print(f"  XGBoost  MAE {metrics['delay']['mae']:.1f}  RMSE {metrics['delay']['rmse']:.1f}")
    print(f"  baseline MAE {metrics['delay']['baseline_mae']:.1f}  RMSE {metrics['delay']['baseline_rmse']:.1f}")
    print("=== CONFLICT (within horizon) ===")
    print(f"  XGBoost  P {cm['precision']} R {cm['recall']} F1 {cm['f1']} AUC {cm.get('roc_auc','-')}")
    print(f"  baseline P {cm['baseline_precision']} R {cm['baseline_recall']} F1 {cm['baseline_f1']}")
    print(f"\n[done] artifacts -> {art}")
    return metrics


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    train_all(n)
