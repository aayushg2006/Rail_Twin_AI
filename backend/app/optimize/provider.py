"""Wire the OptimizationEngine into the orchestrator so every frame carries
CP-SAT options + recommendation for the live conflicts."""
from __future__ import annotations

from ..twin.predict import Prediction
from .engine import OptimizationEngine

_MAX_CONFLICTS = 3   # bound what-if cost per refresh


def build_options(engine, prediction: Prediction) -> dict:
    astate = engine.analytic_state()
    opt = OptimizationEngine()
    options_by_conflict: dict[str, list] = {}
    rec_by_conflict: dict[str, dict | None] = {}
    top = prediction.conflicts[:_MAX_CONFLICTS]
    for c in top:
        result = opt.optimize(astate, c)
        options_by_conflict[c.id] = [e.as_dict() for e in opt.rank_actions(result.options)]
        rec_by_conflict[c.id] = result.recommendation
    out: dict = {
        "optionsByConflict": options_by_conflict,
        "recommendationByConflict": rec_by_conflict,
    }
    if top:
        out["options"] = options_by_conflict[top[0].id]
        out["recommendation"] = rec_by_conflict[top[0].id]
    return out


def attach_optimizer(orch) -> None:
    orch.options_provider = build_options
