"""Wire the OptimizationEngine into the orchestrator so every frame carries
CP-SAT options + recommendation for the live conflicts."""
from __future__ import annotations

from ..twin.predict import Prediction
from .engine import OptimizationEngine

def build_options(engine, prediction: Prediction) -> dict:
    astate = engine.analytic_state()
    opt = OptimizationEngine()
    options_by_conflict: dict[str, list] = {}
    rec_by_conflict: dict[str, dict | None] = {}
    for c in prediction.conflicts:
        result = opt.optimize(astate, c)
        options_by_conflict[c.id] = [e.as_dict() for e in opt.rank_actions(result.options)]
        rec_by_conflict[c.id] = result.recommendation
    out: dict = {
        "optionsByConflict": options_by_conflict,
        "recommendationByConflict": rec_by_conflict,
    }
    if prediction.conflicts:
        first = prediction.conflicts[0]
        out["options"] = options_by_conflict.get(first.id, [])
        out["recommendation"] = rec_by_conflict.get(first.id)
    else:
        out["options"] = []
        out["recommendation"] = {
            "mode": "MONITORING", "status": "NO_CONFLICT",
            "conflictId": None, "optionId": None,
            "rationale": "No resource contention is predicted in the current 15-minute horizon; no intervention is required.",
            "expectedOutcome": "Continue monitoring the network and advance the timeline for the next event.",
            "alternatives": [],
            "failureMetrics": {
                "candidateCount": 0, "feasibleCandidateCount": 0,
                "safetyPassingCandidateCount": 0, "conflictClearingCandidateCount": 0,
                "actualSeparationSec": None, "requiredSeparationSec": None,
                "separationDeficitSec": 0, "residualCriticalConflicts": 0,
                "residualWarningConflicts": 0, "failedSafetyChecks": [],
                "infeasibleReasons": [],
                "blockedResources": sorted(astate.blocked_resources),
                "unavailableRoutes": sorted(astate.unavailable_routes),
                "headwayMultiplier": round(astate.headway_multiplier, 2),
                "networkDelaySec": 0, "passengerDelaySec": 0,
                "freightDelaySec": 0, "weightedDelaySec": 0,
                "primaryFailureReason": "No conflict in current horizon",
            },
        }

    selected = []
    used_trains: set[str] = set()
    cleared: list[str] = []
    for c in prediction.conflicts:
        rec = rec_by_conflict.get(c.id)
        if not rec:
            continue
        option = next((o for o in options_by_conflict[c.id] if rec.get("optionId") and o["id"] == rec["optionId"]), None)
        train_id = option["action"].get("trainId") if option else None
        if not option or train_id in used_trains:
            continue
        selected.append(option["action"])
        used_trains.add(train_id or "")
        if option.get("conflictResolved"):
            cleared.append(c.id)
    out["globalPlan"] = {
        "id": f"PLAN-{engine.now:.0f}",
        "status": ("MONITORING" if not prediction.conflicts
                   else "COMPLETE" if len(cleared) == len(prediction.conflicts)
                   else "PARTIAL" if selected else "NO_SAFE_PLAN"),
        "actions": selected,
        "clearedConflicts": cleared,
        "residualConflicts": [c.id for c in prediction.conflicts if c.id not in cleared],
        "networkDelaySec": 0,
        "throughputDelta": 0,
        "rationale": ("No conflict is currently predicted; continue monitoring the horizon."
                      if not prediction.conflicts else
                      "Safe per-conflict options combined by train dependency; controller approval required."),
    }
    out["globalPlanStatus"] = out["globalPlan"]["status"]
    return out


def attach_optimizer(orch) -> None:
    orch.options_provider = build_options
    orch.model_status["optimizer"] = {"status": "READY", "reason": "CP-SAT optimizer attached"}
