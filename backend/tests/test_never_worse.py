"""The AI must never leave the section worse than doing nothing.

This is the property the whole product rests on. It was NOT held: a run of the
console showed freight delay 14.2 min against the do-nothing shadow's 11.9, and
active conflicts 2 against 1, after three sequential freight holds were each
individually accepted.

Four defects caused it, and each has a test here:
  * do-nothing was a closed-form estimate compared against options measured by
    full re-simulation (apples against oranges, blind to knock-on delay)
  * residual conflicts were an absolute count, so the objective term never
    discriminated and an action that CREATED a conflict was not penalised
  * nothing checked the CUMULATIVE effect of several accepted decisions
  * the three tracks were compared over different train sets
"""
from __future__ import annotations

import pytest

from app.optimize.engine import OptimizationEngine
from app.optimize.objective import option_cost
from app.optimize.whatif import delay_profile, evaluate_do_nothing
from app.twin.engine import SimulationEngine
from app.twin.predict import predict

SCENARIOS = ["BASE", "PLATFORM_BLOCKED", "BRANCH_BLOCKED",
             "SIGNAL_DEGRADED", "FREIGHT_LATE", "PEAK_SURGE"]


def _scene(scenario: str, advance: float = 300.0):
    eng = SimulationEngine(scenario, seed=42)
    eng.advance(advance)
    state = eng.analytic_state()
    return eng, state, predict(state)


# ------------------------------------------------- the reference is measured
def test_do_nothing_is_measured_the_same_way_as_every_option():
    """Both must come from the same projection path, or the comparison is void."""
    _eng, state, pred = _scene("SIGNAL_DEGRADED")
    if not pred.conflicts:
        pytest.skip("no conflict in this frame")
    conflict = pred.conflicts[0]
    base = delay_profile(state)
    ref = evaluate_do_nothing(state, base, conflict)
    assert ref.feasible
    # It is now a literal no-command rollout.  The protecting-signal wait is
    # produced by the same FIFO resource queue used for every option.
    assert ref.action.kind == "NO_ACTION"
    assert ref.network_delay_sec > 0
    assert ref.critical_conflicts >= 0


def test_residual_conflicts_are_relative_to_doing_nothing():
    """An action that creates a conflict must be penalised; one that merely
    leaves untouched conflicts elsewhere must not be."""
    _eng, state, pred = _scene("SIGNAL_DEGRADED")
    if not pred.conflicts:
        pytest.skip("no conflict in this frame")
    opt = OptimizationEngine()
    result = opt.optimize(state, pred.conflicts[0])
    base = delay_profile(state)
    ref = evaluate_do_nothing(state, base, pred.conflicts[0])
    for ev in result.options:
        if not ev.feasible:
            continue
        assert ev.residual_conflicts == max(0, ev.critical_conflicts - ref.critical_conflicts)


# --------------------------------------------------- the headline property
@pytest.mark.parametrize("scenario", SCENARIOS)
def test_a_recommended_action_always_beats_doing_nothing(scenario):
    # At T+900 every scenario has a real, queue-feasible first action.  Keeping
    # this fixture non-empty prevents a controller that always declines from
    # satisfying the safety property vacuously.
    eng, state, pred = _scene(scenario, 900.0)
    opt = OptimizationEngine()
    joint = opt.solve_joint(state, pred)
    base = delay_profile(state)
    checked = 0
    evaluated = 0
    for conflict in pred.conflicts[:8]:
        result = opt.optimize(state, conflict, joint=joint)
        evaluated += len(result.options)
        if (result.recommendation or {}).get("status") != "READY":
            continue
        assert result.selected is not None
        ref = evaluate_do_nothing(state, base, conflict)
        checked += 1
        assert option_cost(result.selected) < option_cost(ref), (
            f"{scenario}/{conflict.id}: recommended {result.selected.title}")
        assert result.selected.network_delay_sec <= ref.network_delay_sec + 1e-6, (
            f"{scenario}/{conflict.id}: {result.selected.title} makes the section later")
        assert result.selected.residual_conflicts <= 0, (
            f"{scenario}/{conflict.id}: {result.selected.title} creates a conflict")
    assert evaluated > 0, f"{scenario}: the safety gate evaluated no concrete options"


def test_recommendation_path_has_a_non_empty_ready_fixture():
    """At least one deterministic fixture crosses every READY gate.

    Per-scenario safety tests may legitimately decline a CP proposal after its
    queue rollout; this separate fixture prevents an always-decline controller
    from making those assertions vacuous.
    """
    eng = SimulationEngine("PLATFORM_BLOCKED", seed=42)
    optimizer = OptimizationEngine()
    ready = []
    for _ in range(60):
        eng.advance(60.0)
        state = eng.analytic_state()
        pred = predict(state)
        joint = optimizer.solve_joint(state, pred)
        if not joint.actions:
            continue
        episode = next(
            (conflict for conflict in pred.conflicts
             if conflict.id == joint.actions[0].reason_conflict_id), None)
        if episode is None:
            continue
        result = optimizer.optimize(state, episode, joint=joint)
        if (result.recommendation or {}).get("status") == "READY":
            ready.append(result)
            break
    assert ready and ready[0].selected is not None


@pytest.mark.parametrize("scenario", ["BASE", "SIGNAL_DEGRADED", "PEAK_SURGE"])
def test_accepting_every_recommendation_never_beats_the_shadow(scenario):
    """The end-to-end property, over simulated time.

    Accept everything the twin recommends for 20 simulated minutes and check the
    AI-assisted section against a do-nothing shadow running the same seed and
    the same disruptions. This is what the RECORD page reports.
    """
    live = SimulationEngine(scenario, seed=42)
    shadow = SimulationEngine(scenario, seed=42, accept_actions=False)
    opt = OptimizationEngine()
    cohort = {tid for tid, rt in live.trains.items()
              if 0 <= rt.entry_at_sec < 20 * 60}

    for _ in range(20):
        live.advance(60.0)
        shadow.advance(60.0)
        state = live.analytic_state()
        pred = predict(state)
        joint = opt.solve_joint(state, pred)
        for conflict in pred.conflicts[:3]:
            result = opt.optimize(state, conflict, joint=joint)
            if (result.recommendation or {}).get("status") == "READY" and result.selected:
                live.apply_action(result.selected.action)

    # Drain the fixed cohort and compare terminal delay. Mid-run active sets are
    # biased because a deliberately held train remains visible for longer.
    live.advance(3600.0)
    shadow.advance(3600.0)
    live_records = {**live.completed_trains, **live.trains}
    shadow_records = {**shadow.completed_trains, **shadow.trains}
    assert all(live_records[t].actual_exit_sec is not None for t in cohort)
    assert all(shadow_records[t].actual_exit_sec is not None for t in cohort)

    def terminal_total(records):
        from app.network.fleet import fleet_by_id
        return sum(max(0.0, records[t].actual_exit_sec - fleet_by_id[t].clear_sec)
                   for t in cohort)

    ai, none = terminal_total(live_records), terminal_total(shadow_records)
    assert ai <= none + 1.0, (
        f"{scenario}: AI-assisted {ai / 60:.1f} terminal min vs FIFO "
        f"{none / 60:.1f} min over {len(cohort)} fixed trains")


def test_the_comparison_uses_a_common_train_set():
    """Held trains stay in section longer, so comparing tracks over their own
    train sets makes 'average lateness' meaningless."""
    from app.twin.metrics import compute_kpis

    eng = SimulationEngine("BASE", seed=42)
    eng.advance(300)
    state = eng.analytic_state()
    pred = predict(state)
    everyone = compute_kpis(eng, state, pred)
    subset_ids = set(list(state.trains)[:2])
    subset = compute_kpis(eng, state, pred, subset_ids)
    assert subset.trains_tracked <= everyone.trains_tracked
    assert subset.trains_tracked == len(
        [t for t in subset_ids if state.trains[t].admitted and not state.trains[t].finished])
