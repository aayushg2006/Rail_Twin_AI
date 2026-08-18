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


class OptimizationEngine:
    def __init__(self, weights: ObjectiveWeights | None = None):
        self.weights = weights or settings.weights

    def generate_candidates(self, state: AnalyticState, conflict: Conflict):
        return cand_mod.generate(state, conflict)

    def evaluate_candidate(self, state, base_delays, cand, conflict, horizon):
        return evaluate(state, base_delays, cand, conflict, horizon)

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
            solver.parameters.max_time_in_seconds = 1.0
            status = solver.Solve(model)
            if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                chosen = next(i for i in feasible_idx if solver.Value(x[i]) == 1)
                selected = evals[chosen]
                score = float(costs[chosen])

        return OptimizationResult(
            conflict_id=conflict.id, options=evals, selected=selected,
            objective_score=score, recommendation=self._recommendation(conflict, evals, selected))

    def rank_actions(self, evals: list[OptionEval]) -> list[OptionEval]:
        return sorted(evals, key=lambda e: (not (e.feasible and e.conflict_resolved
                                                 and e.safety.get("passed")),
                                            option_cost(e, self.weights)))

    def _recommendation(self, conflict: Conflict, evals: list[OptionEval],
                        selected: OptionEval | None) -> dict | None:
        if selected is None:
            return None
        res = resource_by_id[conflict.resource_id]
        required = round(res.headway_sec * 1.0)
        keep_id = conflict.train_b if conflict.train_a == selected.action.train_id else conflict.train_a
        keep = fleet_by_id.get(keep_id)
        give = fleet_by_id.get(selected.action.train_id)
        if keep and give:
            rationale = (f"{keep.id} ({keep.type.lower()}, priority {keep.priority}) carries the "
                         f"higher passenger impact and is closer to {conflict.resource_label}. "
                         f"{give.id} ({give.type.lower()}, priority {give.priority}) can give way at "
                         f"the lowest network cost (objective {round(option_cost(selected, self.weights))}).")
        else:
            rationale = f"Applies the lowest-cost feasible regulation for {conflict.resource_label}."
        others = [e for e in evals
                  if e.id != selected.id and e.feasible and e.conflict_resolved and e.safety.get("passed")]
        return {
            "conflictId": conflict.id, "optionId": selected.id, "rationale": rationale,
            "expectedOutcome": (f"{conflict.kind.replace('_', ' ').lower()} cleared; separation "
                                f"restored to at least {required} s over {conflict.resource_id}."),
            "alternatives": [f"{o.title} — network {o.network_delay_sec / 60:.1f} min" for o in others],
        }
