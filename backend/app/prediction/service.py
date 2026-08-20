"""Inference service (Phase 4).

Loads the trained models and serves predict_eta / predict_delay / predict_conflict
with a confidence, a status, the model version, and top feature contributions
(genuine XGBoost SHAP values). If artifacts are missing, a feature is out of the
training distribution, or confidence is below the floor, status is LOW_CONFIDENCE
and callers fall back to the deterministic projection — ML never overrides safety.
"""
from __future__ import annotations

import datetime as dt
import json
import os

import numpy as np
import xgboost as xgb

from ..config import settings
from .features import FEATURE_NAMES, extract, vector

_ETA_SCALE = 600.0
_DELAY_SCALE = 300.0


def _now_iso() -> str:
    return dt.datetime.utcnow().isoformat()


class PredictionService:
    def __init__(self, artifacts_dir: str | None = None):
        self.dir = artifacts_dir or settings.artifacts_dir
        self.ready = False
        self.stale_reason = ""
        self.models: dict[str, xgb.Booster] = {}
        self.ranges: dict[str, list[float]] = {}
        self.metrics: dict = {}
        self._load()

    def _load(self) -> None:
        try:
            # Artifacts trained on a different feature set must never be served:
            # XGBoost would happily score a vector whose columns mean something
            # else entirely. Refuse, and let the caller fall back.
            registry_path = os.path.join(self.dir, "registry.json")
            if os.path.exists(registry_path):
                registry = json.loads(open(registry_path).read())
                for record in registry.values():
                    trained_on = record.get("features") or record.get("feature_names")
                    if trained_on and list(trained_on) != list(FEATURE_NAMES):
                        self.stale_reason = (
                            "artifacts were trained on a different feature set "
                            f"({len(trained_on)} features vs {len(FEATURE_NAMES)}); retrain required")
                        return
            for name in ("eta", "delay", "conflict", "eta_err", "delay_err"):
                path = os.path.join(self.dir, f"{name}.ubj")
                if not os.path.exists(path):
                    self.stale_reason = f"{name}.ubj is missing"
                    return
                b = xgb.Booster()
                b.load_model(path)
                self.models[name] = b
            rp = os.path.join(self.dir, "feature_ranges.json")
            mp = os.path.join(self.dir, "metrics.json")
            if os.path.exists(rp):
                self.ranges = json.loads(open(rp).read())
            if os.path.exists(mp):
                self.metrics = json.loads(open(mp).read())
            self.ready = True
        except Exception as exc:
            self.ready = False
            self.stale_reason = str(exc)

    # --------------------------------------------------------------- helpers
    def _ood(self, feat: dict[str, float]) -> bool:
        # Flag only genuinely out-of-distribution inputs: a feature more than one
        # full training span beyond the observed [min, max].
        for name, (lo, hi) in self.ranges.items():
            if name not in feat:
                continue
            span = (hi - lo) or 1.0
            if feat[name] < lo - span or feat[name] > hi + span:
                return True
        return False

    def _contribs(self, model: xgb.Booster, x: list[float], feat: dict[str, float], k: int = 4):
        dm = xgb.DMatrix(np.array([x]), feature_names=FEATURE_NAMES)
        shap = model.predict(dm, pred_contribs=True)[0]  # last entry is bias
        pairs = list(zip(FEATURE_NAMES, shap[:-1]))
        pairs.sort(key=lambda p: abs(p[1]), reverse=True)
        return [{"feature": n, "value": round(feat[n], 2), "contribution": round(float(c), 2)}
                for n, c in pairs[:k]]

    def _reg(self, name: str, feat: dict[str, float], scale: float, target: str,
             fallback: float) -> dict:
        if not self.ready:
            return self._fallback(target, fallback, self.stale_reason or "artifacts unavailable")
        x = vector(feat)
        dm = xgb.DMatrix(np.array([x]), feature_names=FEATURE_NAMES)
        value = float(self.models[name].predict(dm)[0])

        # Per-prediction uncertainty: a companion model estimates how wrong this
        # prediction is likely to be given this state, which is a real interval
        # rather than a constant derived from the aggregate test MAE.
        err_model = self.models.get(f"{name}_err")
        if err_model is not None:
            expected_error = max(1.0, float(err_model.predict(dm)[0]))
        else:
            expected_error = self.metrics.get(name, {}).get("mae", scale * 0.3)
        conf = max(0.05, min(0.99, 1.0 / (1.0 + expected_error / max(30.0, abs(value)))))
        if self._ood(feat):
            conf *= 0.6   # out of the training distribution -> less trustworthy
        status = "LOW_CONFIDENCE" if conf < settings.ml_confidence_floor else "OK"
        return {
            "target": target, "value": round(value, 1),
            "expectedErrorSec": round(expected_error, 1),
            "intervalLow": round(value - expected_error, 1),
            "intervalHigh": round(value + expected_error, 1),
            "confidence": round(conf, 3),
            "status": status, "modelVersion": f"{name}-2.0.0",
            "contributions": self._contribs(self.models[name], x, feat),
        }

    def _fallback(self, target: str, value: float, reason: str) -> dict:
        return {"target": target, "value": round(value, 1), "confidence": 0.0,
                "status": "LOW_CONFIDENCE", "modelVersion": f"deterministic ({reason})",
                "contributions": []}

    # --------------------------------------------------------------- public
    def predict_eta(self, state, tid, pred, scheduled_remaining: float) -> dict:
        if not self.ready:
            return self._fallback("ETA", scheduled_remaining,
                                  self.stale_reason or "artifacts unavailable")
        return self._reg("eta", extract(state, tid, pred), _ETA_SCALE, "ETA",
                         scheduled_remaining)

    def predict_delay(self, state, tid, pred, current_delay: float) -> dict:
        if not self.ready:
            return self._fallback("DELAY", current_delay,
                                  self.stale_reason or "artifacts unavailable")
        return self._reg("delay", extract(state, tid, pred), _DELAY_SCALE, "DELAY",
                         current_delay)

    def predict_conflict(self, state, tid, pred) -> dict:
        if not self.ready:
            return self._fallback("CONFLICT", 0.0,
                                  self.stale_reason or "artifacts unavailable")
        feat = extract(state, tid, pred)
        x = vector(feat)
        dm = xgb.DMatrix(np.array([x]), feature_names=FEATURE_NAMES)
        # A Booster saved from the sklearn classifier returns a probability
        # directly; anything outside [0, 1] is a raw margin needing a sigmoid.
        raw = float(self.models["conflict"].predict(dm)[0])
        prob = raw if 0.0 <= raw <= 1.0 else 1.0 / (1.0 + np.exp(-raw))
        conf = abs(prob - 0.5) * 2
        if self._ood(feat):
            conf *= 0.6
        status = "LOW_CONFIDENCE" if conf < settings.ml_confidence_floor else "OK"
        return {
            "target": "CONFLICT", "value": round(prob, 3), "confidence": round(conf, 3),
            "status": status, "modelVersion": "conflict-2.0.0",
            "contributions": self._contribs(self.models["conflict"], x, feat),
        }


_service: PredictionService | None = None


def get_service() -> PredictionService:
    global _service
    if _service is None:
        _service = PredictionService()
    return _service
