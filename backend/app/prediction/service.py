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
        self.models: dict[str, xgb.Booster] = {}
        self.ranges: dict[str, list[float]] = {}
        self.metrics: dict = {}
        self._load()

    def _load(self) -> None:
        try:
            for name in ("eta", "delay", "conflict"):
                path = os.path.join(self.dir, f"{name}.ubj")
                if not os.path.exists(path):
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
        except Exception:
            self.ready = False

    # --------------------------------------------------------------- helpers
    def _ood(self, feat: dict[str, float]) -> bool:
        # Flag only genuinely out-of-distribution inputs: a feature more than one
        # full training span beyond the observed [min, max].
        for name, (lo, hi) in self.ranges.items():
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

    def _reg(self, name: str, feat: dict[str, float], scale: float, target: str, fallback: float) -> dict:
        if not self.ready:
            return self._fallback(target, fallback, "artifacts unavailable")
        x = vector(feat)
        dm = xgb.DMatrix(np.array([x]), feature_names=FEATURE_NAMES)
        value = float(self.models[name].predict(dm)[0])
        mae = self.metrics.get(name, {}).get("mae", scale * 0.3)
        conf = max(0.05, min(0.99, 1.0 - mae / scale))
        if self._ood(feat):
            conf *= 0.6   # out-of-distribution -> less trustworthy
        status = "LOW_CONFIDENCE" if conf < settings.ml_confidence_floor else "OK"
        return {
            "target": target, "value": round(value, 1), "confidence": round(conf, 3),
            "status": status, "modelVersion": f"{name}-1.0.0",
            "contributions": self._contribs(self.models[name], x, feat),
        }

    def _fallback(self, target: str, value: float, reason: str) -> dict:
        return {"target": target, "value": round(value, 1), "confidence": 0.0,
                "status": "LOW_CONFIDENCE", "modelVersion": f"deterministic ({reason})",
                "contributions": []}

    # --------------------------------------------------------------- public
    def predict_eta(self, state, tid, pred, scheduled_remaining: float) -> dict:
        feat = extract(state, tid, pred)
        return self._reg("eta", feat, _ETA_SCALE, "ETA", scheduled_remaining)

    def predict_delay(self, state, tid, pred, current_delay: float) -> dict:
        feat = extract(state, tid, pred)
        return self._reg("delay", feat, _DELAY_SCALE, "DELAY", current_delay)

    def predict_conflict(self, state, tid, pred) -> dict:
        feat = extract(state, tid, pred)
        if not self.ready:
            return self._fallback("CONFLICT", 0.0, "artifacts unavailable")
        x = vector(feat)
        dm = xgb.DMatrix(np.array([x]), feature_names=FEATURE_NAMES)
        margin = float(self.models["conflict"].predict(dm)[0])
        prob = 1.0 / (1.0 + np.exp(-margin)) if abs(margin) > 1e-9 else float(margin)
        # Booster on a classifier returns probability directly when trained via sklearn API:
        raw = float(self.models["conflict"].predict(dm)[0])
        prob = raw if 0.0 <= raw <= 1.0 else prob
        conf = abs(prob - 0.5) * 2
        if self._ood(feat):
            conf *= 0.6
        status = "LOW_CONFIDENCE" if conf < settings.ml_confidence_floor else "OK"
        return {
            "target": "CONFLICT", "value": round(prob, 3), "confidence": round(conf, 3),
            "status": status, "modelVersion": "conflict-1.0.0",
            "contributions": self._contribs(self.models["conflict"], x, feat),
        }


_service: PredictionService | None = None


def get_service() -> PredictionService:
    global _service
    if _service is None:
        _service = PredictionService()
    return _service
