"""Wire ML predictions into the live bundle (Phase 4).

Attaches conflict probability + explainability to each live conflict and delay/ETA
predictions per active train. Only enabled when trained artifacts are present;
otherwise the orchestrator simply omits ML and the console shows the deterministic
projection.
"""
from __future__ import annotations

from ..network.fleet import fleet_by_id
from ..twin.predict import Prediction, project_finish
from .service import get_service

_MAX_CONFLICTS = 3


def build_ml(engine, prediction: Prediction) -> dict:
    svc = get_service()
    if not svc.ready:
        return {}
    astate = engine.analytic_state()
    ml_by_train: dict[str, list[dict]] = {}
    for tid, st in astate.trains.items():
        if st.finished:
            continue
        sched = project_finish(astate, tid) or 0.0
        ml_by_train[tid] = [
            svc.predict_delay(astate, tid, prediction, st.delay_sec),
            svc.predict_eta(astate, tid, prediction, sched),
        ]

    ml_by_conflict: dict[str, dict] = {}
    for c in prediction.conflicts[:_MAX_CONFLICTS]:
        # Predict for the give-way (lower-priority) movement — the one that would
        # actually wait — so the probability is meaningful.
        pair = [t for t in (c.train_a, c.train_b) if t and t in astate.trains]
        tid = max(pair, key=lambda t: fleet_by_id[t].priority) if pair else None
        if not tid:
            continue
        pc = svc.predict_conflict(astate, tid, prediction)
        ml_by_conflict[c.id] = pc
        # surface the model's probability on the conflict itself (DTO carries it)
        c.probability = pc["value"]
    return {"mlByTrain": ml_by_train, "mlByConflict": ml_by_conflict}


def attach_ml(orch) -> None:
    orch.ml_provider = build_ml
