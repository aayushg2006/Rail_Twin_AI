"""Phase 3 twin tests: movement, resource waits, delay accumulation, scenario
trigger, prediction, reproducibility."""
import pytest

from app.twin.engine import SimulationEngine, DelayEvent
from app.twin.predict import predict
from app.twin.metrics import compute_kpis


def run(scenario="BASE", to=420.0):
    eng = SimulationEngine(scenario, seed=42)
    eng.seek(to)
    return eng


def test_trains_move_no_teleport():
    eng = SimulationEngine("BASE", seed=42)
    start = {t: rt.s for t, rt in eng.trains.items()}
    eng.seek(120)
    moved = [t for t, rt in eng.trains.items() if abs(rt.s - start[t]) > 1]
    assert len(moved) >= 5  # most trains have advanced


def test_delay_buckets_never_exceed_total():
    eng = run("BASE", 300)
    for rt in eng.trains.values():
        assert rt.delays.total == pytest.approx(
            rt.delays.base_schedule + rt.delays.dwell + rt.delays.block_wait
            + rt.delays.junction_wait + rt.delays.platform_wait + rt.delays.headway_wait
            + rt.delays.event + rt.delays.hold + rt.delays.regulation)


def test_base_scenario_predicts_jb_bottleneck():
    """Express + freight both work the JB Diva-branch turnout — the demo conflict
    must be COMPUTED by prediction, not hard-coded."""
    eng = SimulationEngine("BASE", seed=42)
    eng.seek(60)
    pred = predict(eng.analytic_state())
    jb = [c for c in pred.conflicts if c.resource_id == "JB"]
    assert jb, "expected a predicted contention over the JB turnout"


def test_freight_delay_event_propagates():
    eng = SimulationEngine("BASE", seed=42)
    eng.seek(30)
    eng.inject_event(DelayEvent("EV1", "TRAIN", "F-4271", delay_seconds=480,
                                reason="freight brake test", scenario_id="FREIGHT_DELAY"))
    eng.seek(200)
    assert eng.trains["F-4271"].delays.event >= 400
    # the delay must show up as a causal link, not a magic number
    assert any(l.affected_train == "F-4271" and l.cause_type == "EVENT"
               for l in eng.causal_links)


def test_reproducible_same_seed():
    a = run("FREIGHT_DELAY", 300)
    b = run("FREIGHT_DELAY", 300)
    sa = {t: round(rt.s, 3) for t, rt in a.trains.items()}
    sb = {t: round(rt.s, 3) for t, rt in b.trains.items()}
    assert sa == sb


def test_kpis_computed():
    eng = run("BASE", 180)
    kpis = compute_kpis(eng.analytic_state(), predict(eng.analytic_state()))
    assert kpis.trains_tracked > 0
    assert kpis.total_delay_sec >= 0
    assert 0 <= kpis.platform_utilisation <= 1
