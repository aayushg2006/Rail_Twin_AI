"""OptimizationEngine - joint conflict resolution over the whole junction.

The old engine solved one conflict at a time and used CP-SAT as an `argmin` over
five pre-scored candidates: a model whose only constraint was `sum(x) == 1`.
Resolving conflicts one by one cannot see that holding a train to clear the
branch turnout pushes it into the platform road behind another service.

Here the horizon is modelled as an ALTERNATIVE GRAPH (see altgraph.py) - a
blocking job-shop over every contended resource at once - and solved as a
disjunctive MILP with CP-SAT:

    start[n]                    when train t reaches resource r
    start[v] >= start[u] + w    blocking / running-time arcs
    ordering booleans           for every contending pair on a shared resource,
                                one train goes first, with full headway between
    minimise                    sum over trains of weighted lateness

CP-SAT reports a real optimality gap. If it exceeds its time budget, the AMCC
heuristic supplies a feasible passing order instead, and the result says which
solver produced it. Per-conflict option tables are still generated for the
controller, but the starred recommendation now comes from the joint plan.
"""
from __future__ import annotations

import time
import hashlib
from dataclasses import dataclass, field

from ortools.sat.python import cp_model

from ..config import ObjectiveWeights, settings
from ..network.fleet import fleet_by_id
from ..network.net import resources as net_resources
from ..network.routes import alternate_platforms
from ..twin.predict import (AnalyticState, Conflict, Prediction,
                            project_finish_free, queue_rollout)
from ..twin.state import AppliedAction
from . import altgraph
from . import candidates as cand_mod
from .objective import explain_cost, option_cost
from .whatif import (OptionEval, delay_profile, evaluate,
                     evaluate_do_nothing)

# A hold shorter than this is inside the noise of the projection and is not
# worth issuing as a controller instruction.
# The queue model is deterministic to the second and command idempotency now
# prevents repeated micro-holds from stacking.  Five seconds is the smallest
# schedule shift worth presenting to a controller.
MIN_ACTIONABLE_HOLD_SEC = 5.0
SOLVER_TIME_LIMIT_SEC = 1.0


@dataclass
class JointPlan:
    status: str                      # OPTIMAL | FEASIBLE | HEURISTIC | INFEASIBLE | EMPTY
    solver: str                      # CP-SAT | AMCC | NONE
    actions: list[AppliedAction] = field(default_factory=list)
    # Both in PASSENGER-MINUTES: lateness weighted by the people on board, so
    # the saving is denominated in the same currency the console reports.
    passenger_minutes: float = 0.0
    fcfs_passenger_minutes: float = 0.0
    optimality_gap: float | None = None
    solve_ms: float = 0.0
    conflicts_considered: int = 0
    resources_contended: int = 0
    plan_id: str | None = None

    @property
    def passenger_minutes_saved(self) -> float:
        return max(0.0, self.fcfs_passenger_minutes - self.passenger_minutes)

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "solver": self.solver,
            "actions": [a.as_dict() for a in self.actions],
            "passengerMinutes": round(self.passenger_minutes, 1),
            "fcfsPassengerMinutes": round(self.fcfs_passenger_minutes, 1),
            "passengerMinutesSaved": round(self.passenger_minutes_saved, 1),
            "optimalityGap": (round(self.optimality_gap, 4)
                              if self.optimality_gap is not None else None),
            "solveMs": round(self.solve_ms, 1),
            "conflictsConsidered": self.conflicts_considered,
            "resourcesContended": self.resources_contended,
            "planId": self.plan_id,
        }


@dataclass
class OptimizationResult:
    conflict_id: str
    options: list[OptionEval]
    selected: OptionEval | None
    objective_score: float
    recommendation: dict | None
    joint_plan: JointPlan | None = None


def _weight_of(train_id: str) -> float:
    """What a second of lateness to this train costs the network.

    Passengers per minute on board, with freight valued through its tonnage, so
    the objective is denominated in the same passenger-minute currency the
    console reports.
    """
    f = fleet_by_id.get(train_id)
    if f is None:
        return 1.0
    if f.is_freight:
        return max(0.05, f.gross_tonnes * settings.weights.freight / 60.0)
    return max(0.1, f.typical_load / 60.0)


def _before_route_control(state: AnalyticState, train_id: str) -> bool:
    train = state.trains.get(train_id)
    route = state.routes.get(train_id)
    if (train is None or route is None or train.departed_platform
            or train.current_resource_id or train.queued_resource_id
            or not route.stops):
        return False
    stop_s = route.stops[0].s
    controls = [
        use.enter_s for use in route.uses
        if (net_resources[use.resource_id].kind == "JUNCTION"
            and use.enter_s < stop_s)
    ]
    safe_control_s = max(controls) if controls else stop_s
    return train.s < safe_control_s - 1e-6


class OptimizationEngine:
    def __init__(self, weights: ObjectiveWeights | None = None):
        self.weights = weights or settings.weights

    # ------------------------------------------------------------ joint plan
    def solve_joint(self, state: AnalyticState, prediction: Prediction,
                    time_limit_sec: float = SOLVER_TIME_LIMIT_SEC) -> JointPlan:
        # Only schedule around conflicts that are actually worth intervening in.
        worth_acting = [c for c in prediction.conflicts
                        if c.severity == "CRITICAL"
                        or do_nothing_cost(state, c, self.weights) > 0]
        conflict_resources = {c.resource_id for c in worth_acting}
        if not conflict_resources:
            return JointPlan("EMPTY", "NONE", conflicts_considered=0)
        prediction = replace_conflicts(prediction, worth_acting)

        # A withdrawn platform is a route-choice problem, not a sequencing
        # problem. Put the first compatible in-service face into the same joint
        # receding-horizon lifecycle before building the disjunctive schedule.
        # Its exact queue consequences are still checked by optimize().
        for conflict in prediction.conflicts:
            if conflict.resource_id not in state.blocked_resources:
                continue
            spec = net_resources.get(conflict.resource_id)
            if spec is None or spec.kind != "PLATFORM":
                continue
            tid = conflict.train_a or conflict.train_b
            train = state.trains.get(tid)
            route = state.routes.get(tid)
            if train is None or route is None or not _before_route_control(state, tid):
                continue
            choices = [pid for pid in alternate_platforms(route)
                       if pid not in state.blocked_resources]
            if not choices:
                continue
            target = choices[0]
            signature = f"PLATFORM_REASSIGNMENT:{tid}:{target}:{conflict.id}"
            plan_id = f"PLAN-{hashlib.sha1(signature.encode()).hexdigest()[:12]}"
            action = AppliedAction(
                "PLATFORM_REASSIGNMENT", tid, platform_id=target,
                plan_id=plan_id, action_id=f"{plan_id}:A1",
                effective_resource_id=conflict.resource_id,
                reason_conflict_id=conflict.id)
            return JointPlan(
                status="FEASIBLE", solver="CP-SAT", actions=[action],
                conflicts_considered=len(prediction.conflicts),
                resources_contended=1, plan_id=plan_id)

        # Before sequencing trains with holds, check whether a physically valid
        # platform road removes an urgent conflict and its downstream queue.
        # This is intentionally bounded to the first three episodes and two
        # alternatives per train.  A route is admitted only with a substantial
        # deterministic margin, fewer critical episodes and no throughput loss;
        # the authoritative SimPy twin still decides whether application at the
        # control point succeeds.
        route_options: list[tuple[tuple[float, ...], OptionEval, Conflict]] = []
        base_delays = delay_profile(state)
        for conflict in prediction.conflicts[:3]:
            if conflict.resource_id in state.blocked_resources:
                continue
            reference = evaluate_do_nothing(state, base_delays, conflict)
            for tid in (conflict.train_a, conflict.train_b):
                if not tid or not _before_route_control(state, tid):
                    continue
                route = state.routes.get(tid)
                if route is None:
                    continue
                for target in alternate_platforms(route)[:2]:
                    action = AppliedAction(
                        "PLATFORM_REASSIGNMENT", tid, platform_id=target,
                        effective_resource_id=conflict.resource_id,
                        reason_conflict_id=conflict.id)
                    candidate = cand_mod.Candidate(
                        "OPT-JOINT-ROUTE", "R",
                        f"Re-platform {tid} to {target} (queue plan)",
                        action, "LOW")
                    evaluated = evaluate(
                        state, base_delays, candidate, conflict,
                        reference=reference)
                    saving = reference.network_delay_sec - evaluated.network_delay_sec
                    critical_gain = (reference.critical_conflicts
                                     - evaluated.critical_conflicts)
                    if (evaluated.feasible and evaluated.conflict_resolved
                            and evaluated.safety.get("passed")
                            and evaluated.residual_conflicts <= 0
                            and saving >= 60.0 and critical_gain >= 1
                            and evaluated.throughput_delta >= 0):
                        rank = (-float(critical_gain),
                                -float(evaluated.throughput_delta),
                                -saving, option_cost(evaluated, self.weights))
                        route_options.append((rank, evaluated, conflict))
        if route_options:
            _, selected_route, conflict = min(route_options, key=lambda item: item[0])
            action = selected_route.action
            signature = (f"{action.kind}:{action.train_id}:{action.platform_id}:"
                         f"{conflict.id}")
            plan_id = f"PLAN-{hashlib.sha1(signature.encode()).hexdigest()[:12]}"
            action.plan_id = plan_id
            action.action_id = f"{plan_id}:A1"
            return JointPlan(
                status="FEASIBLE", solver="QUEUE-ROUTE", actions=[action],
                passenger_minutes=selected_route.passenger_minutes,
                fcfs_passenger_minutes=evaluate_do_nothing(
                    state, base_delays, conflict).passenger_minutes,
                conflicts_considered=len(prediction.conflicts),
                resources_contended=len(conflict_resources), plan_id=plan_id)

        # Downstream closure: add the complete remaining resource chain of each
        # involved train, then every other train that touches those resources,
        # until the set stops growing.  This is what lets a branch decision see
        # the platform or north-throat queue it creates later.
        source_plans = prediction.free_plans or prediction.plans
        involved = {t for c in prediction.conflicts
                    for t in (c.train_a, c.train_b) if t}
        closure_resources = set(conflict_resources)
        changed = True
        while changed:
            changed = False
            for tid in tuple(involved):
                for window in source_plans.get(tid, []):
                    if window.enter <= prediction.horizon_sec and \
                            window.resource_id not in closure_resources:
                        closure_resources.add(window.resource_id)
                        changed = True
            for tid, seq in source_plans.items():
                if tid in involved:
                    continue
                if any(w.enter <= prediction.horizon_sec
                       and w.resource_id in closure_resources for w in seq):
                    involved.add(tid)
                    changed = True

        plans = {
            tid: [w for w in source_plans.get(tid, [])
                  if w.enter <= prediction.horizon_sec
                  and w.resource_id in closure_resources]
            for tid in involved
        }
        plans = {tid: p for tid, p in plans.items() if p}
        if len(plans) < 2:
            return JointPlan("EMPTY", "NONE", conflicts_considered=len(prediction.conflicts))

        def headway_of(rid: str) -> float:
            spec = net_resources.get(rid)
            return (spec.headway_sec if spec else 120.0) * state.headway_multiplier

        graph = altgraph.build(plans, headway_of, state.blocked_resources)
        fifo_rollout = queue_rollout(state, source_plans, prediction.horizon_sec)
        baseline = 0.0
        for tid in plans:
            actual = fifo_rollout.finish_sec.get(tid)
            free = project_finish_free(state, tid)
            if actual is not None and free is not None:
                baseline += max(0.0, actual - free) * _weight_of(tid)

        t0 = time.perf_counter()
        plan = self._solve_cpsat(graph, time_limit_sec)
        if plan is None:
            heur = altgraph.solve_amcc(graph, _weight_of)
            if heur is None:
                return JointPlan("INFEASIBLE", "NONE", fcfs_passenger_minutes=baseline,
                                 conflicts_considered=len(prediction.conflicts),
                                 resources_contended=len(closure_resources),
                                 solve_ms=(time.perf_counter() - t0) * 1000)
            selected, cost = heur
            start = altgraph.longest_paths(graph, selected) or {}
            plan = ("HEURISTIC", "AMCC", start, cost, None)

        status, solver, start, cost, gap = plan
        # Normalize every solver through the same graph cost used for FIFO.
        # CP-SAT uses integer coefficients and bounds; its raw objective is not
        # a safe value to compare directly with the floating-point reference.
        comparable_cost = altgraph.total_cost(graph, start, _weight_of)
        if comparable_cost > baseline + 1e-6:
            # Doing nothing is itself a feasible plan.  Never emit commands
            # when rounding or a bounded solve fails to improve upon it.
            return JointPlan(
                status="FEASIBLE", solver="FIFO", actions=[],
                passenger_minutes=baseline, fcfs_passenger_minutes=baseline,
                optimality_gap=None, solve_ms=(time.perf_counter() - t0) * 1000,
                conflicts_considered=len(prediction.conflicts),
                resources_contended=len(closure_resources))
        actions = self._actions_from_schedule(graph, start, state.sim_time)
        for action in actions:
            related = [
                c for c in prediction.conflicts
                if action.train_id in {c.train_a, c.train_b}
            ]
            if related:
                action.reason_conflict_id = min(related, key=lambda c: c.eta_sec).id
        actions.sort(key=lambda action: (
            next((c.eta_sec for c in prediction.conflicts
                  if c.id == action.reason_conflict_id), float("inf")),
            action.train_id,
        ))
        signature = "|".join(
            f"{a.kind}:{a.train_id}:{a.effective_resource_id}:"
            f"{a.reason_conflict_id}:{a.hold_sec}"
            for a in actions)
        plan_id = (f"PLAN-{hashlib.sha1(signature.encode()).hexdigest()[:12]}"
                   if signature else None)
        for index, action in enumerate(actions):
            action.plan_id = plan_id
            action.action_id = f"{plan_id}:A{index + 1}" if plan_id else None
        return JointPlan(
            status=status, solver=solver, actions=actions,
            passenger_minutes=comparable_cost, fcfs_passenger_minutes=baseline,
            optimality_gap=gap, solve_ms=(time.perf_counter() - t0) * 1000,
            conflicts_considered=len(prediction.conflicts),
            resources_contended=len(closure_resources), plan_id=plan_id)

    def _solve_cpsat(self, graph: altgraph.AltGraph, time_limit_sec: float):
        if not graph.nodes:
            return None
        occupancy = getattr(graph, "occupancy", {})
        horizon = int(max(graph.release.values(), default=0)
                      + sum(occupancy.values()) + 3600)

        model = cp_model.CpModel()
        start = {n: model.NewIntVar(int(graph.release.get(n, 0)), horizon, f"s{i}")
                 for i, n in enumerate(graph.nodes)}

        for u, targets in graph.fixed.items():
            for v, w in targets:
                if u in start and v in start:
                    model.Add(start[v] >= start[u] + int(round(w)))

        for idx, (a, b) in enumerate(graph.pairs):
            # An arc (x, y, w) means start[y] >= start[x] + w. Exactly one arc of
            # the pair holds, which is what makes this a passing-order decision.
            (a_from, a_to, a_w), (b_from, b_to, b_w) = a, b
            if any(n not in start for n in (a_from, a_to, b_from, b_to)):
                continue
            before = model.NewBoolVar(f"b{idx}")
            model.Add(start[a_to] >= start[a_from] + int(round(a_w))).OnlyEnforceIf(before)
            model.Add(start[b_to] >= start[b_from] + int(round(b_w))).OnlyEnforceIf(before.Not())

        # Weighted lateness: how much later each train finishes than its
        # conflict-free projection. Weights are scaled to integers for CP-SAT.
        terms = []
        by_train: dict[str, list] = {}
        for n in graph.nodes:
            by_train.setdefault(graph.train_of[n], []).append(n)
        for tid, nodes in by_train.items():
            due = int(round(graph.due.get(tid, 0.0)))
            finish = model.NewIntVar(0, horizon, f"f_{tid}")
            for n in nodes:
                model.Add(finish >= start[n] + int(round(occupancy.get(n, 0.0))))
            late = model.NewIntVar(0, horizon, f"l_{tid}")
            model.Add(late >= finish - due)
            terms.append(int(round(_weight_of(tid) * 100)) * late)
        model.Minimize(sum(terms))

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = time_limit_sec
        # A single worker is reproducible across paired benchmark runs.  The
        # bounded horizon is small enough that parallel search is unnecessary.
        solver.parameters.num_search_workers = 1
        result = solver.Solve(model)
        if result not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return None

        schedule = {n: float(solver.Value(v)) for n, v in start.items()}
        cost = solver.ObjectiveValue() / 100.0
        bound = solver.BestObjectiveBound() / 100.0
        gap = 0.0 if cost <= 1e-9 else max(0.0, (cost - bound) / max(1e-9, cost))
        status = "OPTIMAL" if result == cp_model.OPTIMAL else "FEASIBLE"
        return (status, "CP-SAT", schedule, cost, gap)

    @staticmethod
    def _actions_from_schedule(graph: altgraph.AltGraph,
                               start: dict[altgraph.Node, float],
                               snapshot_time: float) -> list[AppliedAction]:
        """Translate a schedule into controller instructions.

        A node scheduled later than the train would naturally arrive means that
        train is being held back; the size of that shift is the hold to issue.
        Only the first (largest) shift per train becomes an instruction - once it
        has waited, the rest of its path follows.
        """
        shift: dict[str, dict[str, float | str]] = {}
        for node, t in sorted(start.items(), key=lambda item: (
                item[0].train_id, item[0].index)):
            delta = t - graph.release.get(node, 0.0)
            tid = graph.train_of[node]
            if delta >= MIN_ACTIONABLE_HOLD_SEC and tid not in shift:
                shift[tid] = {
                    "hold": delta, "resource": node.resource_id,
                    "ready": graph.release.get(node, 0.0), "start": t}
        return [
            AppliedAction(
                "HOLD", tid, hold_sec=round(float(value["hold"])),
                effective_resource_id=str(value["resource"]),
                release_at_sec=snapshot_time + float(value["start"]))
            for tid, value in sorted(
                shift.items(), key=lambda kv: (float(kv[1]["ready"]), kv[0]))
            for delta in [float(value["hold"])]
            if delta >= MIN_ACTIONABLE_HOLD_SEC
        ]

    # ---------------------------------------------------- per-conflict options
    def generate_candidates(self, state: AnalyticState, conflict: Conflict):
        return cand_mod.generate(state, conflict)

    def optimize(self, state: AnalyticState, conflict: Conflict,
                 horizon: float = settings.default_horizon_sec,
                 joint: JointPlan | None = None) -> OptimizationResult:
        base_delays = delay_profile(state)
        reference = evaluate_do_nothing(state, base_delays, conflict, horizon)
        cands = self.generate_candidates(state, conflict)
        evals = [evaluate(state, base_delays, c, conflict, horizon, reference=reference)
                 for c in cands]

        # Every option is scored against DOING NOTHING, measured through the
        # identical projection path. An action is only worth issuing if it beats
        # that reference on the objective AND does not raise total lateness.
        # This pairing is the guarantee: the AI can never be worse than leaving
        # the section alone, which is exactly what the shadow twins measure.
        do_nothing = reference
        do_nothing_score = option_cost(do_nothing, self.weights)

        def beats_doing_nothing(ev: OptionEval) -> bool:
            """Is issuing this command better than letting the signal do it?

            Left alone the interlocking holds the FOLLOWING movement by exactly
            the shortfall, which is unbeatable on raw seconds - any controller
            hold is at least that long. So the AI's value is never "fewer
            seconds of delay for this pair"; it is putting those seconds on the
            cheaper train and not creating fresh conflicts downstream.

            Three conditions, and all three are load-bearing:

              cost        cheaper in passenger-minutes than letting it happen
              seconds     no more total delay than the signal would have caused
              conflicts   creates nothing new downstream

            Dropping the seconds condition was tried and measured: the optimiser
            then issued 28-40 commands per run and the simulated section came
            out three to six times LATER than leaving it alone. The projection
            believed those trades helped; the twin proved they did not. Until
            the projection models queueing as well as the engine does, the
            conservative rule is the only one that can be justified - so the
            optimiser declines more often than it acts, and says so.
            """
            base_gate = (option_cost(ev, self.weights) < do_nothing_score
                         and ev.network_delay_sec <= do_nothing.network_delay_sec + 1e-6
                         and ev.residual_conflicts <= 0)
            if not base_gate:
                return False
            if ev.action.kind in {"HOLD", "SPEED_REGULATION"}:
                saving = do_nothing.network_delay_sec - ev.network_delay_sec
                return (saving >= 120.0
                        and ev.critical_conflicts < do_nothing.critical_conflicts
                        and ev.throughput_delta >= 0)
            return True

        viable = [e for e in evals
                  if e.feasible and e.conflict_resolved and e.safety.get("passed")
                  and beats_doing_nothing(e)]
        selected = min(viable, key=lambda e: option_cost(e, self.weights)) if viable else None

        # Receding horizon: expose exactly the first command of the joint plan.
        # Its exact magnitude is re-evaluated here.  Substituting a similarly
        # shaped per-conflict candidate used to turn an 11-second joint shift
        # into a 385-second hold, defeating the whole schedule.
        if joint is not None:
            first = joint.actions[0] if joint.actions else None
            if first is None or first.reason_conflict_id != conflict.id:
                selected = None
            else:
                entry = fleet_by_id.get(first.train_id)
                number = entry.number if entry else first.train_id
                if first.kind == "PLATFORM_REASSIGNMENT":
                    title = f"Re-platform {number} to {first.platform_id} (joint plan)"
                elif first.kind == "SPEED_REGULATION":
                    title = f"Regulate {number} to {round(first.speed_kmh or 0)} km/h (joint plan)"
                else:
                    title = f"Hold {number} for {round(first.hold_sec or 0)} s (joint plan)"
                exact = cand_mod.Candidate(
                    "OPT-JOINT-FIRST", "J", title, first,
                    "LOW" if first.kind == "PLATFORM_REASSIGNMENT" else "NONE")
                exact_eval = evaluate(
                    state, base_delays, exact, conflict, horizon, reference=reference)
                evals.append(exact_eval)
                selected = exact_eval if (
                    exact_eval.feasible and exact_eval.conflict_resolved
                    and exact_eval.safety.get("passed")
                    and beats_doing_nothing(exact_eval)) else None

        if selected is None:
            best_effort = min(
                (e for e in evals if e.feasible and e.safety.get("passed")),
                key=lambda e: option_cost(e, self.weights), default=None)
            if best_effort is not None:
                return OptimizationResult(
                    conflict_id=conflict.id, options=evals, selected=None,
                    objective_score=0.0, joint_plan=joint,
                    recommendation=self._monitor(conflict, best_effort, do_nothing))

        score = option_cost(selected, self.weights) if selected else 0.0

        if selected is None:
            containment = [
                evaluate(state, base_delays, c, conflict, horizon, "CONTAINMENT")
                for c in cands if c.action.kind in ("HOLD", "SPEED_REGULATION")
            ]
            for c in containment:
                c.id = f"CONTAIN-{c.id}"
            safe = [e for e in containment if e.feasible and e.safety.get("passed")]
            evals.extend(safe)
            if safe:
                selected = min(safe, key=lambda e: (e.residual_conflicts,
                                                    max(0.0, e.passenger_minutes),
                                                    e.action.hold_sec or 0.0))
                recommendation = self._containment(conflict, selected)
            else:
                recommendation = {
                    "mode": "CONTAINMENT", "status": "NO_SAFE_RESOLUTION",
                    "conflictId": conflict.id, "optionId": None,
                    "rationale": ("No safe command clears this without creating a worse "
                                  "conflict. The interlocking and the section controller "
                                  "must protect the movement."),
                    "expectedOutcome": "No automatic movement command is available.",
                    "alternatives": [],
                }
        else:
            recommendation = self._recommendation(conflict, evals, selected, joint)

        return OptimizationResult(
            conflict_id=conflict.id, options=evals, selected=selected,
            objective_score=score, recommendation=recommendation, joint_plan=joint)

    def rank_actions(self, evals: list[OptionEval]) -> list[OptionEval]:
        return sorted(evals, key=lambda e: (
            not (e.feasible and e.conflict_resolved and e.safety.get("passed")),
            option_cost(e, self.weights)))

    # --------------------------------------------------------------- rationale
    def _recommendation(self, conflict: Conflict, evals: list[OptionEval],
                        selected: OptionEval, joint: JointPlan | None) -> dict:
        give = fleet_by_id.get(selected.action.train_id)
        keep_id = (conflict.train_b if conflict.train_a == selected.action.train_id
                   else conflict.train_a)
        keep = fleet_by_id.get(keep_id)
        where = _place(conflict)

        if give and keep:
            give_load = give.typical_load or int(give.gross_tonnes)
            unit = "passengers" if give.typical_load else "tonnes"
            keep_unit = "passengers" if keep.typical_load else "tonnes"
            keep_load = keep.typical_load or int(keep.gross_tonnes)
            rationale = (
                f"{keep.number} carries {keep_load:,} {keep_unit} and "
                f"{give.number} {give_load:,} {unit}, so holding {give.number} "
                f"at {where} costs the fewest passenger-minutes."
            )
        else:
            rationale = f"Lowest-cost regulation available at {where}."

        if joint and joint.solver == "CP-SAT" and joint.status == "OPTIMAL":
            rationale += " This is part of a plan proven optimal across every conflict in the horizon."
        elif joint and joint.solver == "AMCC":
            rationale += " Chosen by the fast heuristic; the exact solver hit its time budget."

        others = [e for e in evals
                  if e.id != selected.id and e.feasible and e.conflict_resolved
                  and e.safety.get("passed")]
        return {
            "mode": "RESOLUTION", "status": "READY",
            "conflictId": conflict.id, "optionId": selected.id,
            "rationale": rationale,
            "expectedOutcome": (
                f"{where} clears with at least "
                f"{round(conflict.required_separation_sec)} s between the two movements."),
            "alternatives": [
                {"title": o.title,
                 "passengerMinutes": round(max(0.0, o.passenger_minutes), 1),
                 "networkDelaySec": round(o.network_delay_sec, 1)}
                for o in others
            ],
            "costBreakdown": explain_cost(selected, self.weights),
        }

    def _monitor(self, conflict: Conflict, best: OptionEval,
                 do_nothing: OptionEval) -> dict:
        """No available intervention beats leaving the section alone."""
        where = _place(conflict)
        cost = option_cost(best, self.weights)
        reference = option_cost(do_nothing, self.weights)
        return {
            "mode": "MONITORING", "status": "NO_ACTION_WORTHWHILE",
            "conflictId": conflict.id, "optionId": None,
            "rationale": (
                f"Letting this run costs {max(0.0, do_nothing.network_delay_sec) / 60:.1f} min "
                f"across the section. The cheapest way to clear it "
                f"({best.title.lower()}) costs {max(0.0, best.network_delay_sec) / 60:.1f} min, "
                "so intervening would make the section later, not earlier. The "
                "interlocking holds the second movement briefly at the signal."),
            "expectedOutcome": (
                f"{where}: the following movement is checked for roughly "
                f"{round(max(0.0, conflict.required_separation_sec - conflict.separation_sec))} s "
                "and then proceeds."),
            "alternatives": [
                {"title": best.title,
                 "passengerMinutes": round(max(0.0, best.passenger_minutes), 1),
                 "networkDelaySec": round(best.network_delay_sec, 1)}
            ],
            "costBreakdown": {
                "doNothingScore": round(reference, 1),
                "bestActionScore": round(cost, 1),
                "doNothingNetworkSec": round(do_nothing.network_delay_sec, 1),
            },
        }

    @staticmethod
    def _containment(conflict: Conflict, selected: OptionEval) -> dict:
        where = _place(conflict)
        return {
            "mode": "CONTAINMENT", "status": "NO_SAFE_RESOLUTION",
            "conflictId": conflict.id, "optionId": selected.id,
            "rationale": (f"{where} cannot be cleared. The safest available command is "
                          f"{selected.title.lower()}, which protects the movement without "
                          "pretending the conflict is resolved."),
            "expectedOutcome": "Movement protected; residual work needs the section controller.",
            "alternatives": [],
            "costBreakdown": explain_cost(selected),
        }


def replace_conflicts(prediction: Prediction, conflicts: list[Conflict]) -> Prediction:
    """A view of the prediction narrowed to the conflicts worth scheduling."""
    return Prediction(prediction.horizon_sec, conflicts, prediction.plans, prediction.paths)


def do_nothing_cost(state: AnalyticState, conflict: Conflict,
                    weights: ObjectiveWeights | None = None) -> float:
    """First-order cost of letting this conflict happen, used only to decide
    whether a conflict is worth putting into the joint schedule at all.

    The AUTHORITATIVE comparison is `whatif.evaluate_do_nothing`, which measures
    the same way every option is measured. This remains a cheap screen because
    the joint solver runs over every conflict on every frame and cannot afford a
    full re-projection per conflict just to decide what to include.
    """
    weights = weights or settings.weights
    shortfall = max(0.0, conflict.required_separation_sec - conflict.separation_sec)
    if shortfall <= 0:
        return 0.0
    follower = conflict.train_b or conflict.train_a
    f = fleet_by_id.get(follower)
    if f is None:
        return shortfall
    if f.is_freight:
        return weights.freight * (shortfall * f.gross_tonnes / 60.0)
    return weights.passenger * (shortfall * f.typical_load / 60.0)


def _place(conflict: Conflict) -> str:
    """Where the problem is, in words a controller would use."""
    spec = net_resources.get(conflict.resource_id)
    return spec.label if spec else conflict.resource_id
