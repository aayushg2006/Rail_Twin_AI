"""Phase 4 ML tests: dataset generation, episode split, inference determinism,
low-confidence fallback, baseline comparison."""
import os

import pytest

from app.prediction.dataset import generate_dataset, split_by_episode
from app.prediction.features import FEATURE_NAMES
from app.prediction.service import PredictionService, get_service
from app.twin.engine import SimulationEngine
from app.twin.predict import predict

ART = os.path.join(os.path.dirname(__file__), "..", "app", "prediction", "artifacts")
HAVE_ARTIFACTS = os.path.exists(os.path.join(ART, "delay.ubj"))


def test_dataset_generation_and_split():
    df = generate_dataset(n_episodes=2, t_max=200, dt=15)
    assert len(df) > 0
    for f in FEATURE_NAMES:
        assert f in df.columns
    assert {"target_eta", "target_delay", "target_conflict", "episode"} <= set(df.columns)
    tr, va, te = split_by_episode(df)
    # no episode leaks across splits
    assert set(tr["episode"]) & set(te["episode"]) == set()
    assert set(tr["episode"]) & set(va["episode"]) == set()


def test_low_confidence_fallback_without_artifacts():
    svc = PredictionService(artifacts_dir="___no_such_dir___")
    assert svc.ready is False
    eng = SimulationEngine("BASE", seed=42); eng.seek(60)
    astate = eng.analytic_state(); pred = predict(astate)
    out = svc.predict_delay(astate, "F-4271", pred, current_delay=120.0)
    assert out["status"] == "LOW_CONFIDENCE"
    assert out["value"] == pytest.approx(120.0, abs=0.1)  # falls back to deterministic


@pytest.mark.skipif(not HAVE_ARTIFACTS, reason="trained artifacts not present")
def test_inference_is_deterministic():
    svc = get_service()
    assert svc.ready
    eng = SimulationEngine("BASE", seed=42); eng.seek(60)
    astate = eng.analytic_state(); pred = predict(astate)
    a = svc.predict_delay(astate, "F-4271", pred, 120.0)
    b = svc.predict_delay(astate, "F-4271", pred, 120.0)
    assert a["value"] == b["value"]
    assert a["contributions"] and "feature" in a["contributions"][0]


@pytest.mark.skipif(not HAVE_ARTIFACTS, reason="trained artifacts not present")
def test_xgboost_beats_baseline():
    import json
    metrics = json.loads(open(os.path.join(ART, "metrics.json")).read())
    assert metrics["eta"]["mae"] < metrics["eta"]["baseline_mae"]
    assert metrics["delay"]["mae"] <= metrics["delay"]["baseline_mae"]
    assert metrics["conflict"]["f1"] >= metrics["conflict"]["baseline_f1"]
