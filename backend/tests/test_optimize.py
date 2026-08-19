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
    # MODIFY: a hold too short to restore separation must be rejected by the
    # live safety re-validation and never applied.
    mod = {"kind": "HOLD", "trainId": action["trainId"], "holdSec": 5}
    orch._decide({"conflictId": conflict.id, "action": mod, "outcome": "MODIFIED"})
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


def test_optimizer_protects_high_value_train():
    """The AI must give way with the lower economic-value movement: at the JB
    demo conflict the freight yields, the express is never held."""
    astate, pred = _state_with_conflict()
    conflict = next(c for c in pred.conflicts if c.resource_id == "JB")
    result = OptimizationEngine().optimize(astate, conflict)
    from app.network.fleet import fleet_by_id
    held = result.selected.action.train_id
    other = conflict.train_b if conflict.train_a == held else conflict.train_a
    assert fleet_by_id[held].economic_weight <= fleet_by_id[other].economic_weight
    # F-4271 (freight, weight 2) yields to E-12928 (express, weight 5).
    assert held == "F-4271"


def test_scenarios_produce_distinct_whatif_output():
    """Different scenarios must yield different re-simulated options — the bug
    where every scenario returned identical output must not regress."""
    from app.optimize.provider import build_options

    def jb_signature(scenario: str):
        eng = SimulationEngine(scenario, seed=42)
        eng.seek(70)
        pred = predict(eng.analytic_state())
        res = build_options(eng, pred)
        jb = [c for c in pred.conflicts if c.resource_id == "JB"]
        if not jb:
            return ("no-jb", scenario)
        opts = res["optionsByConflict"][jb[0].id]
        return tuple(round(o["networkDelaySec"], 1) for o in opts)

    assert jb_signature("FREIGHT_DELAY") != jb_signature("EXPRESS_DELAY")


def test_counterfactual_baseline_never_below_current():
    """Accepting the AI's minimal-cost decision records delay avoided, so the
    baseline (naive controller) is >= the current delay."""
    orch = SimulationOrchestrator("BASE")
    orch._set_clock_mode("DEMO")
    orch.engine.seek(70)
    orch._refresh_derived()
    conflict = next(c for c in orch._cached_prediction.conflicts if c.resource_id == "JB")
    result = OptimizationEngine().optimize(orch.engine.analytic_state(), conflict)
    orch._decide({"conflictId": conflict.id, "action": result.selected.action.as_dict(),
                  "outcome": "ACCEPTED"})
    bundle = orch._build_bundle()
    assert bundle["delayAvoidedSec"] >= 0
    assert bundle["baselineKpis"]["totalDelaySec"] >= bundle["kpis"]["totalDelaySec"]


def test_worst_case_returns_safe_containment_with_failure_metrics():
    """Unavailable PF6 must produce a protective hold response, not null."""
    from app.optimize.provider import build_options

    eng = SimulationEngine("WORST_CASE", seed=42)
    eng.seek(70)
    pred = predict(eng.analytic_state())
    result = build_options(eng, pred)
    pf6 = next(c for c in pred.conflicts if c.resource_id == "PF6")
    rec = result["recommendationByConflict"][pf6.id]
    option = next(o for o in result["optionsByConflict"][pf6.id] if o["id"] == rec["optionId"])

    assert rec["mode"] == "CONTAINMENT"
    assert rec["status"] == "NO_SAFE_RESOLUTION"
    assert option["responseClass"] == "CONTAINMENT"
    assert option["safety"]["passed"] is True
    assert option["conflictResolved"] is False
    assert "PF6" in rec["failureMetrics"]["blockedResources"]
    assert rec["failureMetrics"]["primaryFailureReason"] == "Resource PF6 is withdrawn or blocked"
    assert any(check["id"] == "PLT" for check in rec["failureMetrics"]["failedSafetyChecks"])


def test_containment_can_be_accepted_after_live_revalidation():
    from app.optimize.provider import build_options

    orch = SimulationOrchestrator("WORST_CASE")
    orch._set_clock_mode("DEMO")
    orch.engine.seek(70)
    orch.options_provider = build_options
    orch._refresh_derived()
    conflict = next(c for c in orch._cached_prediction.conflicts if c.resource_id == "PF6")
    rec = orch._cached_options["recommendationByConflict"][conflict.id]
    option = next(o for o in orch._cached_options["optionsByConflict"][conflict.id] if o["id"] == rec["optionId"])

    orch._decide({
        "conflictId": conflict.id,
        "action": option["action"],
        "outcome": "ACCEPTED",
        "responseMode": "CONTAINMENT",
    })

    assert orch.engine.applied_actions[-1].train_id == option["action"]["trainId"]
    assert orch._last_decision_status["status"] == "ACCEPTED"


def test_no_conflict_returns_monitoring_response():
    from app.optimize.provider import build_options

    eng = SimulationEngine("FREIGHT_DELAY", seed=42)
    pred = predict(eng.analytic_state())
    assert not pred.conflicts
    result = build_options(eng, pred)

    assert result["recommendation"]["mode"] == "MONITORING"
    assert result["recommendation"]["status"] == "NO_CONFLICT"
    assert result["globalPlan"]["status"] == "MONITORING"


def test_every_predefined_scenario_has_a_response_state():
    from app.network.data import data_pack
    from app.optimize.provider import build_options

    for scenario in [item["id"] for item in data_pack["scenarios"]]:
        eng = SimulationEngine(scenario, seed=42)
        eng.seek(70)
        pred = predict(eng.analytic_state())
        result = build_options(eng, pred)
        assert result["recommendation"] is not None, scenario
        assert result["recommendation"]["mode"] in {"RESOLUTION", "CONTAINMENT", "MONITORING"}
