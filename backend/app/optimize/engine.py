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
from dataclasses import dataclass, field

from ortools.sat.python import cp_model

from ..config import ObjectiveWeights, settings
from ..network.fleet import fleet_by_id
from ..network.net import resources as net_resources
from ..twin.predict import AnalyticState, Conflict, Prediction
from ..twin.state import AppliedAction
from . import altgraph
from . import candidates as cand_mod
from .objective import explain_cost, option_cost
from .whatif import OptionEval, delay_profile, evaluate

# A hold shorter than this is inside the noise of the projection and is not
# worth issuing as a controller instruction.
MIN_ACTIONABLE_HOLD_SEC = 20.0
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


class OptimizationEngine:
    def __init__(self, weights: ObjectiveWeights | None = None):
        self.weights = weights or settings.weights

    # ------------------------------------------------------------ joint plan
    def solve_joint(self, state: AnalyticState, prediction: Prediction,
                    time_limit_sec: float = SOLVER_TIME_LIMIT_SEC) -> JointPlan:
        contended = {c.resource_id for c in prediction.conflicts}
        if not contended:
            return JointPlan("EMPTY", "NONE", conflicts_considered=0)

        # Only trains involved in contention need to be scheduled; everything
        # else is running clear and would only enlarge the model.
        involved = {t for c in prediction.conflicts for t in (c.train_a, c.train_b) if t}
        plans = {tid: [w for w in prediction.plans.get(tid, []) if w.resource_id in contended]
                 for tid in involved}
        plans = {tid: p for tid, p in plans.items() if p}
        if len(plans) < 2:
            return JointPlan("EMPTY", "NONE", conflicts_considered=len(prediction.conflicts))

        def headway_of(rid: str) -> float:
            spec = net_resources.get(rid)
            return (spec.headway_sec if spec else 120.0) * state.headway_multiplier

        graph = altgraph.build(plans, headway_of, state.blocked_resources)
        baseline_start = altgraph.longest_paths(graph, altgraph.natural_order(graph))
        baseline = (altgraph.total_cost(graph, baseline_start, _weight_of)
                    if baseline_start is not None else 0.0)

        t0 = time.perf_counter()
        plan = self._solve_cpsat(graph, time_limit_sec)
        if plan is None:
            heur = altgraph.solve_amcc(graph, _weight_of)
            if heur is None:
                return JointPlan("INFEASIBLE", "NONE", fcfs_passenger_minutes=baseline,
                                 conflicts_considered=len(prediction.conflicts),
                                 resources_contended=len(contended),
                                 solve_ms=(time.perf_counter() - t0) * 1000)
            selected, cost = heur
            start = altgraph.longest_paths(graph, selected) or {}
            plan = ("HEURISTIC", "AMCC", start, cost, None)

        status, solver, start, cost, gap = plan
        actions = self._actions_from_schedule(graph, start)
        return JointPlan(
            status=status, solver=solver, actions=actions,
            passenger_minutes=cost, fcfs_passenger_minutes=baseline,
            optimality_gap=gap, solve_ms=(time.perf_counter() - t0) * 1000,
            conflicts_considered=len(prediction.conflicts),
            resources_contended=len(contended))

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
        solver.parameters.num_search_workers = 4
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
                               start: dict[altgraph.Node, float]) -> list[AppliedAction]:
        """Translate a schedule into controller instructions.

        A node scheduled later than the train would naturally arrive means that
        train is being held back; the size of that shift is the hold to issue.
        Only the first (largest) shift per train becomes an instruction - once it
        has waited, the rest of its path follows.
        """
        shift: dict[str, float] = {}
        for node, t in start.items():
            delta = t - graph.release.get(node, 0.0)
            tid = graph.train_of[node]
            if delta > shift.get(tid, 0.0):
                shift[tid] = delta
        return [
            AppliedAction("HOLD", tid, hold_sec=round(delta))
            for tid, delta in sorted(shift.items(), key=lambda kv: -kv[1])
            if delta >= MIN_ACTIONABLE_HOLD_SEC
        ]

    # ---------------------------------------------------- per-conflict options
    def generate_candidates(self, state: AnalyticState, conflict: Conflict):
        return cand_mod.generate(state, conflict)

    def optimize(self, state: AnalyticState, conflict: Conflict,
                 horizon: float = settings.default_horizon_sec,
                 joint: JointPlan | None = None) -> OptimizationResult:
        base_delays = delay_profile(state)
        cands = self.generate_candidates(state, conflict)
        evals = [evaluate(state, base_delays, c, conflict, horizon) for c in cands]

        viable = [e for e in evals
                  if e.feasible and e.conflict_resolved and e.safety.get("passed")]
        selected = min(viable, key=lambda e: option_cost(e, self.weights)) if viable else None

        # Prefer whatever the joint plan does to this conflict's trains: it is
        # the only choice that accounts for what happens further along.
        if joint and joint.actions:
            involved = {conflict.train_a, conflict.train_b}
            joint_trains = {a.train_id for a in joint.actions if a.train_id in involved}
            aligned = [e for e in viable if e.action.train_id in joint_trains]
            if aligned:
                selected = min(aligned, key=lambda e: option_cost(e, self.weights))

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


def _place(conflict: Conflict) -> str:
    """Where the problem is, in words a controller would use."""
    spec = net_resources.get(conflict.resource_id)
    return spec.label if spec else conflict.resource_id
