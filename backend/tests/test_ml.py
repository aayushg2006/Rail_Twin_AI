"""The ML layer must add information, not imitate a formula the process has.

The previous models were trained on the output of the deterministic `predict()`
and then scored against it, which produced an ETA MAE of 1.95 s and a conflict
AUC of 0.997 while contributing nothing. These tests check the opposite: that
targets come from what actually happened, and that the models beat the
deterministic baseline by a believable - not a miraculous - margin.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.prediction.dataset import generate_dataset, split_by_episode
from app.prediction.features import FEATURE_NAMES, extract, vector
from app.prediction.service import PredictionService, get_service
from app.twin.engine import SimulationEngine
from app.twin.predict import predict


ARTIFACTS = Path(__file__).resolve().parents[1] / "app" / "prediction" / "artifacts"


@pytest.fixture(scope="module")
def scene():
    eng = SimulationEngine("BASE", seed=42)
    eng.advance(600)
    state = eng.analytic_state()
    return eng, state, predict(state)


# ------------------------------------------------------------------- features
def test_feature_vector_is_ordered_and_complete(scene):
    _eng, state, pred = scene
    tid = next(iter(state.trains))
    feat = extract(state, tid, pred)
    assert set(feat) == set(FEATURE_NAMES)
    assert len(vector(feat)) == len(FEATURE_NAMES)
    assert all(isinstance(v, float) and v == v for v in vector(feat))


def test_features_are_deterministic_for_a_state(scene):
    _eng, state, pred = scene
    tid = next(iter(state.trains))
    assert extract(state, tid, pred) == extract(state, tid, pred)


def test_features_are_physical_quantities(scene):
    _eng, state, pred = scene
    tid = next(iter(state.trains))
    feat = extract(state, tid, pred)
    assert 0 <= feat["current_speed_kmh"] <= 200
    assert 0 <= feat["distance_remaining_m"] <= 40_000
    assert 0 <= feat["time_of_day_hours"] < 24


# -------------------------------------------------------------------- dataset
def test_dataset_targets_are_outcomes_not_predictions():
    df = generate_dataset(n_episodes=3, t_max=1800)
    assert len(df) > 300
    for column in ("target_eta", "target_lateness", "target_conflict",
                   "baseline_remaining"):
        assert column in df.columns
    assert df["target_eta"].notna().sum() > 0, "no train was observed clearing"
    # A degenerate target teaches nothing.
    assert 0.01 < df["target_conflict"].mean() < 0.9
    assert df["target_lateness"].std() > 5


def test_the_deterministic_baseline_is_not_already_perfect():
    """If the projection were exact there would be nothing for a model to learn -
    which is exactly the flaw in the old circular setup."""
    df = generate_dataset(n_episodes=3, t_max=1800).dropna(subset=["target_eta"])
    error = (df["baseline_remaining"] - df["target_eta"]).abs()
    assert error.mean() > 15, f"baseline error is only {error.mean():.1f}s"


def test_split_never_leaks_an_episode_across_folds():
    df = generate_dataset(n_episodes=6, t_max=900)
    tr, va, te = split_by_episode(df)
    assert not (set(tr["episode"]) & set(va["episode"]))
    assert not (set(tr["episode"]) & set(te["episode"]))
    assert not (set(va["episode"]) & set(te["episode"]))


# ------------------------------------------------------------------- metrics
@pytest.mark.skipif(not (ARTIFACTS / "metrics.json").exists(),
                    reason="models not trained in this environment")
def test_models_beat_the_baseline_but_are_not_miraculous():
    metrics = json.loads((ARTIFACTS / "metrics.json").read_text())
    eta, delay = metrics["eta"], metrics["delay"]
    assert eta["mae"] < eta["baseline_mae"], "the model must beat the projection"
    assert delay["mae"] < delay["baseline_mae"]
    # An MAE of a couple of seconds over a several-minute horizon means the model
    # is reproducing a formula, not predicting an outcome.
    assert eta["mae"] > 10, f"ETA MAE of {eta['mae']}s suggests circular targets"
    conflict = metrics["conflict"]
    assert conflict["f1"] > conflict["baseline_f1"]
    assert conflict["roc_auc"] < 0.99, "an AUC this high means the label is leaking"


# ------------------------------------------------------------------ inference
def test_service_refuses_artifacts_from_a_different_feature_set(tmp_path):
    (tmp_path / "registry.json").write_text(json.dumps({
        "eta": {"name": "eta", "features": ["only_one_feature"]}}))
    svc = PredictionService(str(tmp_path))
    assert not svc.ready
    assert "feature set" in svc.stale_reason


def test_service_degrades_cleanly_with_no_artifacts(tmp_path):
    svc = PredictionService(str(tmp_path))
    assert not svc.ready
    out = svc.predict_eta(None, None, None, 123.0)
    assert out["status"] == "LOW_CONFIDENCE"
    assert out["value"] == 123.0


@pytest.mark.skipif(not (ARTIFACTS / "eta.ubj").exists(),
                    reason="models not trained in this environment")
def test_inference_is_deterministic_and_bounded(scene):
    _eng, state, pred = scene
    svc = get_service()
    if not svc.ready:
        pytest.skip(f"artifacts not loadable: {svc.stale_reason}")
    tid = next(iter(state.trains))
    a = svc.predict_conflict(state, tid, pred)
    b = svc.predict_conflict(state, tid, pred)
    assert a == b
    assert 0.0 <= a["value"] <= 1.0
    assert 0.0 <= a["confidence"] <= 1.0
    assert a["contributions"], "SHAP contributions explain the score"


@pytest.mark.skipif(not (ARTIFACTS / "eta_err.ubj").exists(),
                    reason="uncertainty models not trained")
def test_confidence_varies_between_states(scene):
    """Confidence used to be `1 - mae/600` - a constant per model."""
    _eng, state, pred = scene
    svc = get_service()
    if not svc.ready:
        pytest.skip("artifacts not loadable")
    seen = set()
    for tid in list(state.trains)[:8]:
        out = svc.predict_eta(state, tid, pred, 0.0)
        assert out["intervalLow"] <= out["value"] <= out["intervalHigh"]
        seen.add(round(out["confidence"], 3))
    assert len(seen) > 1, "confidence is not varying with the state"
