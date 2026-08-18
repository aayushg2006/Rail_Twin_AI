"""Phase 5 tests: conflict types, candidate generation, what-if isolation,
CP-SAT optimizer, hard constraints, safety rejection, recommendation,
human-in-the-loop accept/modify/reject + audit."""
import pytest

from app.optimize import candidates as cand_mod
from app.optimize.engine import OptimizationEngine
from app.optimize.whatif import evaluate, delay_profile
from app.optimize.safety import validate
from app.orchestrator.orchestrator import SimulationOrchestrator
from app.twin.engine import SimulationEngine
from app.twin.predict import apply_action, predict


def _state_with_conflict(scenario="BASE", to=70.0):
    eng = SimulationEngine(scenario, seed=42)
    eng.seek(to)
    astate = eng.analytic_state()
    pred = predict(astate)
    return astate, pred


def test_conflict_types_detected():
    astate, pred = _state_with_conflict("BASE", 70)
    kinds = {c.kind for c in pred.conflicts}
    assert "JUNCTION_CONTENTION" in kinds  # the JB demo conflict is computed

    # A withdrawn platform must surface a platform/route conflict.
    _, predp = _state_with_conflict("PLATFORM_UNAVAILABLE", 60)
    assert any(c.resource_id == "PF6" for c in predp.conflicts)


def test_candidates_are_bounded_and_meaningful():
    astate, pred = _state_with_conflict()
    conflict = next(c for c in pred.conflicts if c.resource_id == "JB")
    cands = cand_mod.generate(astate, conflict)
    kinds = {c.action.kind for c in cands}
    assert "HOLD" in kinds
    for c in cands:
        assert c.action.train_id  # every action names an affected train


def test_whatif_never_mutates_live_state():
    astate, pred = _state_with_conflict()
    conflict = next(c for c in pred.conflicts if c.resource_id == "JB")
    before = {t: st.s for t, st in astate.trains.items()}
    before_speed = {t: st.speed_kmh for t, st in astate.trains.items()}
    cands = cand_mod.generate(astate, conflict)
    base_delays = delay_profile(astate)
    for c in cands:
        evaluate(astate, base_delays, c, conflict)
    after = {t: st.s for t, st in astate.trains.items()}
    after_speed = {t: st.speed_kmh for t, st in astate.trains.items()}
    assert before == after and before_speed == after_speed


def test_optimizer_selects_feasible_safe_resolving():
    astate, pred = _state_with_conflict()
    conflict = next(c for c in pred.conflicts if c.resource_id == "JB")
    result = OptimizationEngine().optimize(astate, conflict)
    assert result.selected is not None
    assert result.selected.conflict_resolved
    assert result.selected.safety["passed"]
    assert result.recommendation and result.recommendation["optionId"] == result.selected.id


def test_hard_constraint_unsafe_never_selected():
    astate, pred = _state_with_conflict()
    conflict = next(c for c in pred.conflicts if c.resource_id == "JB")
    result = OptimizationEngine().optimize(astate, conflict)
    # Any option that fails safety or leaves the conflict unresolved must not win.
    for ev in result.options:
        if ev is result.selected:
            assert ev.safety["passed"] and ev.conflict_resolved


def test_safety_validator_flags_blocked_resource():
    astate, pred = _state_with_conflict("PLATFORM_UNAVAILABLE", 60)
    conflict = next((c for c in pred.conflicts if c.resource_id == "PF6"), pred.conflicts[0])
    # An action leaving the withdrawn platform in play fails the PLT check.
    from app.twin.state import AppliedAction
    sv = validate(AppliedAction("HOLD", conflict.train_a, hold_sec=60), astate, conflict, resolved=False)
    assert sv["passed"] is False
    assert any(chk["id"] == "PLT" and not chk["passed"] for chk in sv["checks"])


def test_human_in_loop_accept_modify_reject(monkeypatch):
    orch = SimulationOrchestrator("BASE")
    orch._set_clock_mode("DEMO")
    orch.engine.seek(70)
    orch._refresh_derived()
    logged = []
    orch.decision_hook = logged.append
    conflict = next(c for c in orch._cached_prediction.conflicts if c.resource_id == "JB")
    result = OptimizationEngine().optimize(orch.engine.analytic_state(), conflict)
    action = result.selected.action.as_dict()

    n0 = len(orch.engine.applied_actions)
    # REJECT: no change to the twin, but logged.
    orch._decide({"conflictId": conflict.id, "action": action, "outcome": "REJECTED"})
    assert len(orch.engine.applied_actions) == n0
    # ACCEPT: applies to the live twin.
    orch._decide({"conflictId": conflict.id, "action": action, "outcome": "ACCEPTED"})
    assert len(orch.engine.applied_actions) == n0 + 1
    # MODIFY: a changed hold still applies.
    mod = {"kind": "HOLD", "trainId": action["trainId"], "holdSec": 90}
    orch._decide({"conflictId": conflict.id, "action": mod, "outcome": "MODIFIED"})
    # The live re-validation may reject a modified hold that no longer clears
    # the current conflict; unsafe modifications must not be applied.
    assert len(orch.engine.applied_actions) == n0 + 1
    assert orch._last_decision_status["status"] == "REJECTED"
    assert [d["outcome"] for d in logged] == ["REJECTED", "ACCEPTED", "REJECTED"]


def test_options_are_generated_for_every_predicted_conflict():
    orch = SimulationOrchestrator("BASE")
    orch._set_clock_mode("DEMO")
    orch.engine.seek(70)
    orch._refresh_derived()
    from app.optimize.provider import build_options
    result = build_options(orch.engine, orch._cached_prediction)
    assert set(result["optionsByConflict"]) == {c.id for c in orch._cached_prediction.conflicts}
