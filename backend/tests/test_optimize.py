"""The optimiser must solve the horizon jointly and beat the do-nothing order."""
from __future__ import annotations

import pytest

from app.optimize import altgraph
from app.optimize.engine import OptimizationEngine, _weight_of
from app.optimize.safety import validate
from app.twin.engine import SimulationEngine
from app.twin.predict import predict
from app.twin.state import AppliedAction


@pytest.fixture(scope="module")
def scene():
    eng = SimulationEngine("SIGNAL_DEGRADED", seed=42)
    eng.advance(900)
    state = eng.analytic_state()
    return eng, state, predict(state)


# ------------------------------------------------------------ alternative graph
def test_graph_has_one_alternative_pair_per_contending_couple(scene):
    _eng, state, pred = scene
    contended = {c.resource_id for c in pred.conflicts}
    plans = {t: [w for w in pred.plans.get(t, []) if w.resource_id in contended]
             for c in pred.conflicts for t in (c.train_a, c.train_b) if t}
    plans = {k: v for k, v in plans.items() if v}
    g = altgraph.build(plans, lambda rid: 120.0, set())
    assert g.nodes and g.pairs
    for a, b in g.pairs:
        assert a[0] == b[1] and a[1] == b[0], "a pair must be the two passing orders"
        assert a[0].train_id != a[1].train_id


def test_a_positive_cycle_is_reported_as_infeasible():
    """Selecting both directions of a pair means a train precedes itself."""
    plans = {
        "A": [type("W", (), {"train_id": "A", "resource_id": "R", "enter": 0.0,
                             "exit": 30.0, "s": 0.0})()],
        "B": [type("W", (), {"train_id": "B", "resource_id": "R", "enter": 10.0,
                             "exit": 40.0, "s": 0.0})()],
    }
    g = altgraph.build(plans, lambda rid: 120.0, set())
    a, b = g.pairs[0]
    assert altgraph.longest_paths(g, [a]) is not None
    assert altgraph.longest_paths(g, [a, b]) is None


def test_natural_order_is_always_feasible(scene):
    _eng, state, pred = scene
    contended = {c.resource_id for c in pred.conflicts}
    plans = {t: [w for w in pred.plans.get(t, []) if w.resource_id in contended]
             for c in pred.conflicts for t in (c.train_a, c.train_b) if t}
    plans = {k: v for k, v in plans.items() if v}
    g = altgraph.build(plans, lambda rid: 120.0, set())
    assert altgraph.longest_paths(g, altgraph.natural_order(g)) is not None


# -------------------------------------------------------------- joint solving
def test_joint_plan_is_never_worse_than_doing_nothing(scene):
    _eng, state, pred = scene
    plan = OptimizationEngine().solve_joint(state, pred)
    assert plan.status in ("OPTIMAL", "FEASIBLE", "HEURISTIC", "EMPTY")
    if plan.status != "EMPTY":
        assert plan.passenger_minutes <= plan.fcfs_passenger_minutes + 1e-6, (
            "the optimiser must not be beaten by first-come-first-served")


def test_joint_plan_reports_a_real_optimality_gap(scene):
    _eng, state, pred = scene
    plan = OptimizationEngine().solve_joint(state, pred)
    if plan.solver == "CP-SAT":
        assert plan.optimality_gap is not None and 0.0 <= plan.optimality_gap <= 1.0


def test_joint_solve_is_fast_enough_for_a_live_frame(scene):
    _eng, state, pred = scene
    plan = OptimizationEngine().solve_joint(state, pred, time_limit_sec=1.0)
    assert plan.solve_ms < 2500


def test_amcc_fallback_produces_a_feasible_order(scene):
    _eng, state, pred = scene
    contended = {c.resource_id for c in pred.conflicts}
    plans = {t: [w for w in pred.plans.get(t, []) if w.resource_id in contended]
             for c in pred.conflicts for t in (c.train_a, c.train_b) if t}
    plans = {k: v for k, v in plans.items() if v}
    g = altgraph.build(plans, lambda rid: 120.0, set())
    result = altgraph.solve_amcc(g, _weight_of)
    assert result is not None
    selected, cost = result
    assert altgraph.longest_paths(g, selected) is not None
    assert cost >= 0


def test_a_crowded_local_outranks_a_lightly_loaded_express():
    """Passenger-minutes, not a hand-assigned priority number."""
    from app.network.fleet import fleet_by_id
    local = next(f for f in fleet_by_id.values() if f.service_class == "LOCAL_FAST")
    premium = next(f for f in fleet_by_id.values() if f.service_class == "PREMIUM")
    assert _weight_of(local.id) > _weight_of(premium.id)


# -------------------------------------------------------------------- options
def test_options_are_generated_and_ranked(scene):
    _eng, state, pred = scene
    if not pred.conflicts:
        pytest.skip("no conflict in this frame")
    opt = OptimizationEngine()
    result = opt.optimize(state, pred.conflicts[0])
    assert result.options
    ranked = opt.rank_actions(result.options)
    viable = [e for e in ranked if e.feasible and e.conflict_resolved
              and e.safety.get("passed")]
    assert ranked[:len(viable)] == viable, "viable options must rank first"


def test_no_option_can_claim_a_negative_delay(scene):
    """A re-route used to arrive EARLIER than doing nothing, because changing
    route remapped the train onto a different-length polyline."""
    _eng, state, pred = scene
    opt = OptimizationEngine()
    for conflict in pred.conflicts[:5]:
        for ev in opt.optimize(state, conflict).options:
            if ev.feasible:
                assert ev.network_delay_sec > -1.0, f"{ev.title} claims {ev.network_delay_sec}s"


def test_rationale_is_plain_language(scene):
    _eng, state, pred = scene
    if not pred.conflicts:
        pytest.skip("no conflict in this frame")
    rec = OptimizationEngine().optimize(state, pred.conflicts[0]).recommendation
    assert rec and rec["rationale"]
    assert "economic weight" not in rec["rationale"].lower()
    assert "CF-" not in rec["rationale"], "conflict codes do not belong in prose"


# --------------------------------------------------------------------- safety
def test_safety_rejects_re_platforming_onto_a_blocked_face(scene):
    _eng, state, pred = scene
    if not pred.conflicts:
        pytest.skip("no conflict in this frame")
    conflict = pred.conflicts[0]
    blocked = state.clone()
    blocked.blocked_resources.add("PF3")
    action = AppliedAction("PLATFORM_REASSIGNMENT", conflict.train_a, platform_id="PF3")
    assert not validate(action, blocked, conflict, True)["passed"]


def test_safety_rejects_a_speed_above_the_line_limit(scene):
    _eng, state, pred = scene
    if not pred.conflicts:
        pytest.skip("no conflict in this frame")
    conflict = pred.conflicts[0]
    st = state.trains[conflict.train_a]
    action = AppliedAction("SPEED_REGULATION", conflict.train_a,
                           speed_kmh=st.line_speed_kmh + 40)
    assert not validate(action, state, conflict, True)["passed"]
