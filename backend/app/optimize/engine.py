"""OptimizationEngine — OR-Tools CP-SAT selects the resolution (Phase 5).

For a predicted conflict it generates candidates, evaluates each by what-if, then
solves a CP-SAT model that picks exactly one feasible-and-safe option minimising
the weighted objective. Hard constraints (must resolve the conflict, must pass
safety, must be feasible) are encoded as the model's feasible set — never traded
away. The starred recommendation is whatever CP-SAT selects; nothing is hardcoded.

Formulated as a one-hot selection so a richer joint multi-conflict MILP (Pyomo)
can replace the solver later behind the same interface.
"""
from __future__ import annotations

from dataclasses import dataclass

from ortools.sat.python import cp_model

from ..config import ObjectiveWeights, settings
from ..network.fleet import fleet_by_id
from ..network.topology import resource_by_id
from ..twin.predict import AnalyticState, Conflict
from . import candidates as cand_mod
from .objective import option_cost
from .whatif import OptionEval, delay_profile, evaluate

CONSTRAINTS_CHECKED = [
    "conflict_resolved", "safety_passed", "feasible_action",
    "headway_minimum", "no_block_double_occupancy", "route_available",
]


@dataclass
class OptimizationResult:
    conflict_id: str
    options: list[OptionEval]
    selected: OptionEval | None
    objective_score: float
    recommendation: dict | None
    failure_metrics: dict


class OptimizationEngine:
    def __init__(self, weights: ObjectiveWeights | None = None):
        self.weights = weights or settings.weights

    def generate_candidates(self, state: AnalyticState, conflict: Conflict):
        return cand_mod.generate(state, conflict)

    def evaluate_candidate(self, state, base_delays, cand, conflict, horizon,
                           response_class: str = "RESOLUTION"):
        return evaluate(state, base_delays, cand, conflict, horizon, response_class)

    def optimize(self, state: AnalyticState, conflict: Conflict,
                 horizon: float = settings.default_horizon_sec) -> OptimizationResult:
        base_delays = delay_profile(state)
        cands = self.generate_candidates(state, conflict)
        evals = [self.evaluate_candidate(state, base_delays, c, conflict, horizon) for c in cands]

        # Feasible set = hard constraints satisfied (resolved + safe + feasible action).
        feasible_idx = [i for i, ev in enumerate(evals)
                        if ev.feasible and ev.conflict_resolved and ev.safety.get("passed")]

        selected: OptionEval | None = None
        score = 0.0
        if feasible_idx:
            model = cp_model.CpModel()
            x = {i: model.NewBoolVar(f"x{i}") for i in feasible_idx}
            model.Add(sum(x.values()) == 1)               # choose exactly one
            costs = {i: int(round(option_cost(evals[i], self.weights))) for i in feasible_idx}
            model.Minimize(sum(costs[i] * x[i] for i in feasible_idx))
            solver = cp_model.CpSolver()
            # One conflict must never monopolize the live event loop. The
            # candidate evaluations are deterministic; CP-SAT only chooses
            # among them and is bounded tightly for real-time refreshes.
            solver.parameters.max_time_in_seconds = 0.05
            status = solver.Solve(model)
            if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                chosen = next(i for i in feasible_idx if solver.Value(x[i]) == 1)
                selected = evals[chosen]
                score = float(costs[chosen])

        failure_metrics = self._failure_metrics(state, conflict, evals)
        if selected is not None:
            recommendation = self._recommendation(conflict, evals, selected, failure_metrics)
        else:
            containment_evals = [
                self.evaluate_candidate(state, base_delays, cand, conflict, horizon, "CONTAINMENT")
                for cand in cands
                if cand.action.kind in ("HOLD", "SPEED_REGULATION")
            ]
            for containment in containment_evals:
                containment.id = f"CONTAIN-{containment.id}"
            safe_containment = [e for e in containment_evals if e.feasible and e.safety.get("passed")]
            evals.extend(safe_containment)
            if safe_containment:
                selected = min(
                    safe_containment,
                    key=lambda e: (e.residual_conflicts, max(0.0, e.weighted_delay_sec),
                                   e.action.hold_sec or 0.0, e.title),
                )
                recommendation = self._containment_recommendation(conflict, selected, failure_metrics)
            else:
                recommendation = {
                    "mode": "CONTAINMENT", "status": "NO_SAFE_RESOLUTION",
                    "conflictId": conflict.id, "optionId": None,
                    "rationale": "No safe containment command could be generated; interlocking and the section controller must protect the movement.",
                    "expectedOutcome": "No automatic movement command is available.",
                    "alternatives": [], "failureMetrics": failure_metrics,
                }

        return OptimizationResult(
            conflict_id=conflict.id, options=evals, selected=selected,
            objective_score=score, recommendation=recommendation,
            failure_metrics=failure_metrics)

    def rank_actions(self, evals: list[OptionEval]) -> list[OptionEval]:
        return sorted(evals, key=lambda e: (not (e.feasible and e.conflict_resolved
                                                 and e.safety.get("passed")),
                                            option_cost(e, self.weights)))

    def _recommendation(self, conflict: Conflict, evals: list[OptionEval],
                        selected: OptionEval | None, failure_metrics: dict) -> dict | None:
        if selected is None:
            return None
        res = resource_by_id[conflict.resource_id]
        required = round(res.headway_sec * 1.0)
        keep_id = conflict.train_b if conflict.train_a == selected.action.train_id else conflict.train_a
        keep = fleet_by_id.get(keep_id)
        give = fleet_by_id.get(selected.action.train_id)
        if keep and give:
            keep_cls = (keep.service_class or keep.type).lower().replace("_", " ")
            give_cls = (give.service_class or give.type).lower().replace("_", " ")
            obj = round(option_cost(selected, self.weights))
            if keep.economic_weight >= give.economic_weight:
                rationale = (f"{keep.id} ({keep_cls}, economic weight {keep.economic_weight:g}) is the "
                             f"higher-value movement over {conflict.resource_label}, so {give.id} "
                             f"({give_cls}, economic weight {give.economic_weight:g}) gives way at the "
                             f"lowest network cost (objective {obj}).")
            else:
                # Give-way is forced here (e.g. a following move on the same line);
                # holding {give} still clears the conflict at least network cost.
                rationale = (f"Holding {give.id} ({give_cls}, economic weight {give.economic_weight:g}) "
                             f"clears {conflict.resource_label} at the lowest network cost (objective "
                             f"{obj}); {keep.id} ({keep_cls}, economic weight {keep.economic_weight:g}) "
                             f"keeps its path.")
        else:
            rationale = f"Applies the lowest-cost feasible regulation for {conflict.resource_label}."
        others = [e for e in evals
                  if e.id != selected.id and e.feasible and e.conflict_resolved and e.safety.get("passed")]
        return {
            "mode": "RESOLUTION", "status": "READY",
            "conflictId": conflict.id, "optionId": selected.id, "rationale": rationale,
            "expectedOutcome": (f"{conflict.kind.replace('_', ' ').lower()} cleared; separation "
                                f"restored to at least {required} s over {conflict.resource_id}."),
            "alternatives": [f"{o.title} — network {o.network_delay_sec / 60:.1f} min" for o in others],
            "failureMetrics": failure_metrics,
        }

    def _containment_recommendation(self, conflict: Conflict, selected: OptionEval,
                                    failure_metrics: dict) -> dict:
        return {
            "mode": "CONTAINMENT", "status": "NO_SAFE_RESOLUTION",
            "conflictId": conflict.id, "optionId": selected.id,
            "rationale": (f"Full resolution is unavailable at {conflict.resource_label}. "
                          f"Recommended safe containment: {selected.title}. "
                          "The movement remains protected, but residual network work requires controller/interlocking intervention."),
            "expectedOutcome": "Movement protected without claiming the unavailable resource is restored.",
            "alternatives": [], "failureMetrics": failure_metrics,
        }

    def _failure_metrics(self, state: AnalyticState, conflict: Conflict,
                         evals: list[OptionEval]) -> dict:
        failed_checks: dict[str, dict] = {}
        infeasible_reasons = []
        for ev in evals:
            if ev.infeasible_reason:
                infeasible_reasons.append({"candidate": ev.title, "reason": ev.infeasible_reason})
            for check in ev.safety.get("checks", []):
                if not check.get("passed"):
                    failed_checks[check["id"]] = {
                        "id": check["id"], "label": check["label"], "detail": check["detail"],
                    }
        deficit = max(0.0, conflict.required_separation_sec - conflict.separation_sec)
        primary = "No safe candidate clears the conflict"
        if conflict.resource_id in state.blocked_resources:
            primary = f"Resource {conflict.resource_id} is withdrawn or blocked"
        elif deficit > 0:
            primary = f"Separation shortfall of {round(deficit)} seconds"
        elif any("speed" in (v["label"] + v["detail"]).lower() for v in failed_checks.values()):
            primary = "Required regulation is outside the permissible speed band"
        elif state.unavailable_routes:
            primary = "Required alternate route is unavailable"
        return {
            "candidateCount": len(evals),
            "feasibleCandidateCount": sum(1 for e in evals if e.feasible),
            "safetyPassingCandidateCount": sum(1 for e in evals if e.safety.get("passed")),
            "conflictClearingCandidateCount": sum(1 for e in evals if e.conflict_resolved),
            "actualSeparationSec": round(conflict.separation_sec, 1),
            "requiredSeparationSec": round(conflict.required_separation_sec, 1),
            "separationDeficitSec": round(deficit, 1),
            "residualCriticalConflicts": max((e.residual_conflicts for e in evals), default=0),
            "residualWarningConflicts": 0,
            "failedSafetyChecks": list(failed_checks.values()),
            "infeasibleReasons": infeasible_reasons,
            "blockedResources": sorted(state.blocked_resources),
            "unavailableRoutes": sorted(state.unavailable_routes),
            "headwayMultiplier": round(state.headway_multiplier, 2),
            "networkDelaySec": round(min((e.network_delay_sec for e in evals), default=0.0), 1),
            "passengerDelaySec": round(min((e.passenger_delay_sec for e in evals), default=0.0), 1),
            "freightDelaySec": round(min((e.freight_delay_sec for e in evals), default=0.0), 1),
            "weightedDelaySec": round(min((e.weighted_delay_sec for e in evals), default=0.0), 1),
            "primaryFailureReason": primary,
        }
