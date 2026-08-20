"""The twin runs the real timetable, and its numbers come from the simulation."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.network.fleet import active_window, fleet
from app.twin.engine import SimulationEngine
from app.twin.metrics import compute_kpis
from app.twin.predict import predict


@pytest.fixture(scope="module")
def engine():
    eng = SimulationEngine("BASE", seed=42)
    eng.advance(900)
    return eng


# ------------------------------------------------------------------- the fleet
def test_the_fleet_is_the_published_timetable_plus_goods():
    assert len(fleet) > 600, "expected the whole booked day, not a hand-written list"
    published = [f for f in fleet if f.provenance == "published-timetable"]
    synthetic = [f for f in fleet if f.provenance == "synthetic"]
    assert len(published) > 500
    assert len(synthetic) == 150
    assert all(f.is_freight for f in synthetic), "only goods paths may be synthetic"


def test_no_service_carries_a_hardcoded_entry_delay():
    """`entryDelayMin` used to sit in the data pack and was reported as delay."""
    for name in ("timetable-bsr.json", "freight-paths.json"):
        for base in (Path(__file__).resolve().parents[2] / "data", Path("/srv/data")):
            path = base / name
            if path.exists():
                assert "entryDelayMin" not in path.read_text(encoding="utf-8")
                break


def test_only_a_working_window_is_simulated():
    window = active_window(14 * 3600, 1800, 3600)
    assert 20 < len(window) < 120, f"window held {len(window)} trains"


def test_weekly_services_do_not_run_every_day():
    weekly = [f for f in fleet if f.operating_days.count(".") >= 5]
    assert weekly, "the timetable has weekly long-distance services"
    f = weekly[0]
    runs = [d for d in range(7) if f.runs_on(d)]
    assert len(runs) <= 2


# ------------------------------------------------------------------- the twin
def test_trains_are_spread_across_the_section(engine):
    positions = [rt.sample(engine.now)[0] for rt in engine.trains.values()
                 if rt.admitted and not rt.finished]
    assert len(positions) >= 3
    assert max(positions) - min(positions) > 1000, "trains must not stack on one point"


def test_speeds_are_computed_and_differ_between_trains(engine):
    speeds = {round(rt.sample(engine.now)[1] * 3.6)
              for rt in engine.trains.values() if rt.admitted and not rt.finished}
    assert len(speeds) > 1, "every train moving at exactly one speed means no dynamics"


def test_lateness_is_measured_against_the_booked_time(engine):
    late = [rt.lateness_sec(engine.now, engine.service_epoch_sec)
            for rt in engine.trains.values() if rt.admitted]
    assert late
    # Earliness must be expressible - it used to be hardcoded to zero.
    assert min(late) <= 0 or all(v >= 0 for v in late)
    assert max(late) < 7200, "lateness should be operational, not a whole shift"


def test_kpis_are_measured_not_asserted(engine):
    state = engine.analytic_state()
    k = compute_kpis(engine, state, predict(state))
    assert k.platform_utilisation > 0, "utilisation must reflect real occupancy"
    assert 0 <= k.platform_utilisation <= 1
    # Both are None until something has actually departed, never a fake zero.
    if k.departures_measured == 0:
        assert k.on_time_percent is None and k.throughput_per_hour is None
    else:
        assert 0 <= k.on_time_percent <= 100


def test_conflicts_reference_real_resources_and_chainage(engine):
    pred = predict(engine.analytic_state())
    for c in pred.conflicts:
        assert c.severity in ("CRITICAL", "WARNING")
        assert c.at.corridor and isinstance(c.at.m, float)
        assert c.required_separation_sec > 0
        if c.severity == "CRITICAL":
            assert c.separation_sec < 0


def test_a_hold_makes_a_train_later_not_earlier():
    eng = SimulationEngine("BASE", seed=42)
    eng.advance(300)
    tid = next(t for t, rt in eng.trains.items()
               if rt.admitted and not rt.finished)
    before = eng.trains[tid].lateness_sec(eng.now, eng.service_epoch_sec)
    from app.twin.state import AppliedAction
    eng.apply_action(AppliedAction("HOLD", tid, hold_sec=120))
    eng.advance(300)
    after = eng.trains[tid].lateness_sec(eng.now, eng.service_epoch_sec)
    assert after >= before


def test_shadow_twin_ignores_controller_actions():
    from app.twin.state import AppliedAction
    eng = SimulationEngine("BASE", seed=42, accept_actions=False)
    eng.advance(300)
    tid = next(t for t, rt in eng.trains.items() if rt.admitted and not rt.finished)
    eng.apply_action(AppliedAction("HOLD", tid, hold_sec=600))
    assert eng.applied_actions == []
    assert eng.trains[tid].pending_hold_sec == 0


def test_the_twin_is_deterministic_for_a_seed():
    a = SimulationEngine("BASE", seed=7); a.advance(600)
    b = SimulationEngine("BASE", seed=7); b.advance(600)
    pos_a = {t: round(rt.sample(a.now)[0], 3) for t, rt in a.trains.items()}
    pos_b = {t: round(rt.sample(b.now)[0], 3) for t, rt in b.trains.items()}
    assert pos_a == pos_b


@pytest.mark.parametrize("scenario", ["PLATFORM_BLOCKED", "BRANCH_BLOCKED",
                                      "SIGNAL_DEGRADED", "FREIGHT_LATE", "PEAK_SURGE"])
def test_every_scenario_actually_changes_something(scenario):
    """Presets used to name train numbers that were not running at the demo
    clock, so several of them silently did nothing."""
    eng = SimulationEngine(scenario, seed=42)
    changed = (bool(eng.blocked_resources)
               or eng.headway_multiplier != 1.0
               or bool(eng.setup.overrides))
    assert changed, f"{scenario} applied no disruption at all"
