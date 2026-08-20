"""Adapters: internal twin model -> console JSON.

Positions travel as CHAINAGE (corridor + metres), never as pixels. The console
projects chainage onto its schematic at render time, which is the only place a
coordinate exists. Train identity carries the booked timetable fields so the
register can show a real train number, name and booked time.
"""
from __future__ import annotations

from ..network.fleet import fleet_by_id
from ..twin.engine import SimulationEngine
from ..twin.metrics import KPISet
from ..twin.predict import Conflict, Prediction
from ..twin.state import TrainRuntime, TrainStatus

# Internal status -> the coarse state the console colours by.
_STATE_MAP = {
    TrainStatus.SCHEDULED: "SCHEDULED",
    TrainStatus.APPROACHING: "APPROACHING",
    TrainStatus.RUNNING: "RUNNING",
    TrainStatus.REGULATED: "REGULATED",
    TrainStatus.WAITING: "HELD",
    TrainStatus.HELD: "HELD",
    TrainStatus.ARRIVING: "DWELL",
    TrainStatus.DWELLING: "DWELL",
    TrainStatus.DEPARTED: "RUNNING",
    TrainStatus.COMPLETED: "CLEARED",
    TrainStatus.CANCELLED: "CLEARED",
}


def _operational_state(rt: TrainRuntime) -> str:
    if rt.finished:
        return "CLEARED"
    return _STATE_MAP.get(rt.status, "RUNNING")


def train_state_dict(eng: SimulationEngine, rt: TrainRuntime) -> dict:
    now = eng.now
    s, speed_ms = rt.sample(now)
    route = eng.routes.get(rt.train_id)
    f = fleet_by_id.get(rt.train_id)
    position = route.position_at(s) if route else None
    lateness = rt.lateness_sec(now, eng.service_epoch_sec)
    return {
        "trainId": rt.train_id,
        "number": f.number if f else rt.train_id,
        "name": f.name if f else "",
        "category": rt.category,
        "serviceClass": rt.service_class,
        "priority": rt.priority,
        "typicalLoad": f.typical_load if f else 0,
        # Position: metres along the route, plus the chainage the console draws.
        "s": round(s, 1),
        "routeLengthM": round(route.length_m, 1) if route else 0.0,
        "at": position.as_dict() if position else None,
        "line": route.line_at(s) if route else None,
        "platformId": route.platform_id if route else None,
        "speedKmh": round(speed_ms * 3.6, 1),
        "lineSpeedKmh": round(rt.line_speed_kmh, 1),
        "regulatedKmh": rt.regulated_kmh,
        "state": _operational_state(rt),
        "dwellRemainingSec": round(rt.dwell_remaining(now), 1),
        "holdRemainingSec": round(rt.hold_remaining(now), 1),
        # Booked-time accounting: negative lateness is genuine earliness.
        "bookedDepSec": rt.booked_dep_sec,
        "actualDepSec": round(rt.actual_dep_sec, 1) if rt.actual_dep_sec is not None else None,
        "latenessSec": round(lateness, 1),
        "delayBreakdown": rt.delays.as_dict(),
        "origin": f.origin if f else "",
        "destination": f.destination if f else "",
        "arrivalCorridor": f.arrival_corridor if f else "",
        "departureCorridor": f.departure_corridor if f else "",
        "provenance": rt.provenance,
        "admitted": rt.admitted,
        "finished": rt.finished,
    }


def sim_state_dict(eng: SimulationEngine) -> dict:
    now = eng.now
    return {
        "simTimeSec": round(now, 3),
        "serviceSeconds": round(eng.service_seconds, 1),
        "epochStartMs": eng.epoch_start_ms,
        "trains": {tid: train_state_dict(eng, rt) for tid, rt in eng.trains.items()},
        "scenario": eng.scenario_id,
        "blockedResources": sorted(eng.blocked_resources),
        "headwayMultiplier": eng.headway_multiplier,
        "appliedActions": [a.as_dict() for a in eng.applied_actions],
    }


def conflict_dict(c: Conflict) -> dict:
    return {
        "id": c.id, "kind": c.kind, "severity": c.severity,
        "resourceId": c.resource_id, "resourceLabel": c.resource_label,
        "resourceKind": c.resource_kind,
        "at": c.at.as_dict(), "trainA": c.train_a, "trainB": c.train_b,
        "etaSec": round(c.eta_sec, 1),
        "separationSec": round(c.separation_sec, 1),
        "requiredSeparationSec": round(c.required_separation_sec, 1),
        "probability": round(c.probability, 3),
        "timeToConflictSec": round(c.time_to_conflict, 1),
    }


def prediction_dict(pred: Prediction) -> dict:
    return {
        "horizonSec": pred.horizon_sec,
        "conflicts": [conflict_dict(c) for c in pred.conflicts],
        "paths": {tid: [p.as_dict() for p in pts] for tid, pts in pred.paths.items()},
    }


def kpis_dict(k: KPISet) -> dict:
    """None means NOT YET MEASURED, and the console must say so rather than
    printing a zero that looks like a real reading."""
    return {
        "activeConflicts": k.active_conflicts,
        "predictedConflicts": k.predicted_conflicts,
        "trainsTracked": k.trains_tracked,
        "scheduledAhead": k.scheduled_ahead,
        "totalLatenessSec": k.total_lateness_sec,
        "earlinessSec": k.earliness_sec,
        "averageLatenessSec": k.average_lateness_sec,
        "passengerDelaySec": k.passenger_delay_sec,
        "freightDelaySec": k.freight_delay_sec,
        "passengerMinutes": k.passenger_minutes,
        "throughputPerHour": k.throughput_per_hour,
        "platformUtilisation": k.platform_utilisation,
        "onTimePercent": k.on_time_percent,
        "departuresMeasured": k.departures_measured,
    }


def causal_chain_list(eng: SimulationEngine, limit: int = 40) -> list[dict]:
    return [link.as_dict() for link in eng.causal_links[-limit:]]


def delay_buckets_map(eng: SimulationEngine) -> dict[str, dict]:
    return {tid: rt.delays.as_dict() for tid, rt in eng.trains.items() if not rt.finished}


def projected_dict(eng: SimulationEngine, state) -> dict:
    """A future frame of the SAME twin: where each train will be, and the
    contention predicted from there. The console draws this instead of running a
    projection of its own."""
    from ..twin.predict import predict as _predict

    pred = _predict(state, 900)
    trains: dict[str, dict] = {}
    for tid, st in state.trains.items():
        if st.finished or not st.admitted:
            continue
        route = eng.routes.get(tid)
        if route is None:
            continue
        position = route.position_at(st.s)
        trains[tid] = {
            "trainId": tid,
            "s": round(st.s, 1),
            "at": position.as_dict(),
            "line": route.line_at(st.s),
            "latenessSec": round(st.lateness_sec, 1),
        }
    return {
        "offsetSec": round(state.sim_time - eng.now, 1),
        "trains": trains,
        "conflicts": [conflict_dict(c) for c in pred.conflicts],
    }
