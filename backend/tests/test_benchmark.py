from __future__ import annotations

import json

from app.benchmark.runner import load_latest_report, manifest_path
from app.twin.engine import SimulationEngine


def test_manifest_is_frozen_balanced_and_paired():
    manifest = json.loads(manifest_path().read_text(encoding="utf-8"))
    runs = manifest["runs"]
    assert len(runs) == 100
    counts = {}
    for run in runs:
        counts[run["scenario"]] = counts.get(run["scenario"], 0) + 1
        assert run["passengerObservationSet"]
        assert run["freightPathSet"] == "freight-paths.json"
    assert max(counts.values()) - min(counts.values()) <= 1
    assert len({run["seed"] for run in runs}) == 100
    assert {run["windowClass"] for run in runs} == {
        "peak", "off_peak", "freight_heavy", "disruption"}


def test_missing_report_can_never_claim_validation(monkeypatch, tmp_path):
    from app import benchmark as package
    from app.benchmark import runner
    monkeypatch.setattr(runner, "report_path", lambda: tmp_path / "missing.json")
    report = load_latest_report()
    assert report["status"] == "NOT_VALIDATED"
    assert report["validated"] is False


def test_fixed_cohort_cutoff_stops_post_window_admissions():
    engine = SimulationEngine(
        "BASE", seed=42, stochastic=False, admission_cutoff_sec=60)
    engine.advance(600)
    records = [*engine.trains.values(), *engine.completed_trains.values()]
    assert records
    assert all(runtime.entry_at_sec < 60 for runtime in records)
