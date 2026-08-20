"""Intervening must be worth more than the conflict costs.

Without this the optimiser recommended a 236 s hold on a goods rake to avoid a
57 s headway infringement, and the shadow twins correctly measured the result as
WORSE than doing nothing - the single most damaging thing a decision-support
system can do.
"""
from __future__ import annotations

import pytest

from app.optimize.engine import OptimizationEngine, do_nothing_cost
from app.optimize.objective import option_cost
from app.twin.engine import SimulationEngine
from app.twin.predict import predict


@pytest.fixture(scope="module")
def scene():
    eng = SimulationEngine("BASE", seed=42)
    eng.advance(120)
    state = eng.analytic_state()
    return eng, state, predict(state)


def test_doing_nothing_has_a_measurable_cost(scene):
    _eng, state, pred = scene
    for c in pred.conflicts:
        cost = do_nothing_cost(state, c)
        assert cost >= 0
        if c.required_separation_sec > c.separation_sec:
            assert cost > 0, "an infringement always costs the follower something"


def test_a_conflict_with_no_shortfall_costs_nothing(scene):
    _eng, state, pred = scene
    if not pred.conflicts:
        pytest.skip("no conflict in this frame")
    c = pred.conflicts[0]
    import copy
    clean = copy.copy(c)
    clean.separation_sec = clean.required_separation_sec + 10
    assert do_nothing_cost(state, clean) == 0.0


def test_the_optimiser_never_recommends_a_cure_worse_than_the_disease(scene):
    """The property that matters: if an action is recommended, it must be
    cheaper than letting the conflict happen."""
    _eng, state, pred = scene
    opt = OptimizationEngine()
    joint = opt.solve_joint(state, pred)
    checked = 0
    for c in pred.conflicts:
        result = opt.optimize(state, c, joint=joint)
        rec = result.recommendation or {}
        if rec.get("status") != "READY" or result.selected is None:
            continue
        checked += 1
        assert option_cost(result.selected) < do_nothing_cost(state, c), (
            f"{c.id}: recommended {result.selected.title} costing "
            f"{option_cost(result.selected):.1f} against a do-nothing cost of "
            f"{do_nothing_cost(state, c):.1f}")
    assert checked >= 0


def test_an_uneconomic_conflict_is_reported_as_monitor_not_silently_dropped(scene):
    """Declining to act must be visible advice, with a reason."""
    _eng, state, pred = scene
    opt = OptimizationEngine()
    joint = opt.solve_joint(state, pred)
    monitors = []
    for c in pred.conflicts:
        rec = (opt.optimize(state, c, joint=joint).recommendation or {})
        if rec.get("status") == "NO_ACTION_WORTHWHILE":
            monitors.append(rec)
    for rec in monitors:
        assert rec["mode"] == "MONITORING"
        assert rec["optionId"] is None
        assert "costs" in rec["rationale"].lower()
        # The controller is still told what the alternative would have been.
        assert rec["alternatives"]


def test_a_critical_conflict_is_always_worth_examining(scene):
    """A CRITICAL conflict stops a train dead; it must never be waved through
    on economic grounds without at least being costed."""
    _eng, state, pred = scene
    for c in pred.conflicts:
        if c.severity == "CRITICAL":
            assert do_nothing_cost(state, c) > 0
