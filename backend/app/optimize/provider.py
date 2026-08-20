"""Wire the OptimizationEngine into the orchestrator.

Every derived frame carries: the JOINT plan across all conflicts in the horizon
(solved once), plus a per-conflict option table so the controller can see and
compare the alternatives for whichever conflict they are inspecting.
"""
from __future__ import annotations

from ..twin.predict import Prediction
from .engine import OptimizationEngine

MONITORING = {
    "mode": "MONITORING", "status": "NO_CONFLICT",
    "conflictId": None, "optionId": None,
    "rationale": ("No contention is predicted in the current horizon; no "
                  "intervention is required."),
    "expectedOutcome": "Continue monitoring the section.",
    "alternatives": [],
}


def build_options(engine, prediction: Prediction) -> dict:
    astate = engine.analytic_state()
    opt = OptimizationEngine()

    # Solve the whole horizon once, then slice it per conflict for the UI.
    joint = opt.solve_joint(astate, prediction)

    options_by_conflict: dict[str, list] = {}
    rec_by_conflict: dict[str, dict | None] = {}
    for c in prediction.conflicts:
        result = opt.optimize(astate, c, joint=joint)
        options_by_conflict[c.id] = [e.as_dict() for e in opt.rank_actions(result.options)]
        rec_by_conflict[c.id] = result.recommendation

    out: dict = {
        "optionsByConflict": options_by_conflict,
        "recommendationByConflict": rec_by_conflict,
        "jointPlan": joint.as_dict(),
    }
    if prediction.conflicts:
        first = prediction.conflicts[0].id
        out["options"] = options_by_conflict.get(first, [])
        out["recommendation"] = rec_by_conflict.get(first)
    else:
        out["options"] = []
        out["recommendation"] = MONITORING

    cleared = [c.id for c in prediction.conflicts
               if (rec_by_conflict.get(c.id) or {}).get("status") == "READY"]
    out["globalPlan"] = {
        "id": f"PLAN-{engine.now:.0f}",
        "status": ("MONITORING" if not prediction.conflicts
                   else "COMPLETE" if len(cleared) == len(prediction.conflicts)
                   else "PARTIAL" if cleared else "NO_SAFE_PLAN"),
        "solver": joint.solver,
        "solverStatus": joint.status,
        "optimalityGap": joint.as_dict()["optimalityGap"],
        "solveMs": round(joint.solve_ms, 1),
        "actions": [a.as_dict() for a in joint.actions],
        "clearedConflicts": cleared,
        "residualConflicts": [c.id for c in prediction.conflicts if c.id not in cleared],
        # Real figures from the joint solve, not the zeros this used to carry.
        "passengerMinutes": round(joint.passenger_minutes, 1),
        "fcfsPassengerMinutes": round(joint.fcfs_passenger_minutes, 1),
        "passengerMinutesSaved": round(joint.passenger_minutes_saved, 1),
        "rationale": (
            "No conflict is currently predicted; continue monitoring."
            if not prediction.conflicts else
            f"{joint.solver} scheduled {joint.conflicts_considered} conflicts across "
            f"{joint.resources_contended} contended resources jointly; controller "
            "approval is required for each command."),
    }
    out["globalPlanStatus"] = out["globalPlan"]["status"]
    return out


def attach_optimizer(orch) -> None:
    orch.options_provider = build_options
    orch.model_status["optimizer"] = {
        "status": "READY",
        "reason": "Alternative-graph model solved with CP-SAT (AMCC fallback)",
    }
