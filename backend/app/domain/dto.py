"""Adapters: internal twin model -> frontend JSON (src/domain/types.ts).

The frontend's authoritative live object is `SimState`; derived data arrives as a
`prediction` / `kpis` / `options` / `recommendation` bundle. These builders emit
exactly those shapes so the React console renders backend output with no changes
to any component.
"""
from __future__ import annotations

from ..config import settings
from ..network.fleet import fleet_by_id
from ..network.geometry import kmh_to_units_per_sec
from ..network.topology import route_by_id
from ..twin.engine import SimulationEngine, route_length
from ..twin.metrics import KPISet
from ..twin.predict import Conflict, Prediction
from ..twin.state import TrainRuntime, TrainStatus


def _operational_state(rt: TrainRuntime) -> str:
    """Map rich internal status -> frontend TrainOperationalState."""
    if rt.finished:
        return "CLEARED"
    if rt.status in (TrainStatus.DWELLING, TrainStatus.ARRIVING):
        return "DWELL"
    if rt.status in (TrainStatus.HELD, TrainStatus.WAITING):
        return "HELD"
    if rt.speed_kmh < rt.nominal_speed_kmh - 0.5:
        return "REGULATED"
    route = route_by_id[rt.route_id]
    if rt.next_stop_index < len(route.stops):
        if abs(rt.s - route.stops[rt.next_stop_index].s) < 120:
            return "APPROACHING"
    return "RUNNING"


def train_state_dict(rt: TrainRuntime, now: float) -> dict:
    return {
        "trainId": rt.train_id,
        "s": round(rt.sample_s(now), 4),
        "speedKmh": round(rt.speed_kmh, 2),
        "nominalSpeedKmh": rt.nominal_speed_kmh,
        "state": _operational_state(rt),
        "dwellRemainingSec": round(rt.dwell_remaining(now), 1),
        "nextStopIndex": rt.next_stop_index,
        "holdRemainingSec": round(rt.hold_remaining(now), 1),
        "delaySec": round(rt.delays.total, 1),
        "finished": rt.finished,
    }


def sim_state_dict(eng: SimulationEngine) -> dict:
    now = eng.now
    overrides = {tid: rt.route_id for tid, rt in eng.trains.items()
                 if rt.route_id != fleet_by_id[tid].route_id}
    return {
        "simTimeSec": round(now, 3),
        "epochStartMs": settings.epoch_start_ms,
        "lastUpdateSec": round(now, 3),
        "trains": {tid: train_state_dict(rt, now) for tid, rt in eng.trains.items()},
        "scenario": eng.scenario_id,
        "routeOverrides": overrides,
        "blockedResources": sorted(eng.blocked_resources),
        "headwayMultiplier": eng.headway_multiplier,
        "unavailableRoutes": sorted(eng.unavailable_routes),
        "appliedActions": [a.as_dict() for a in eng.applied_actions],
        "respawnAt": {},
    }


def conflict_dict(c: Conflict) -> dict:
    return {
        "id": c.id, "kind": c.kind, "severity": c.severity,
        "resourceId": c.resource_id, "resourceLabel": c.resource_label,
        "at": c.at.as_dict(), "trainA": c.train_a, "trainB": c.train_b,
        "etaSec": round(c.eta_sec, 1), "separationSec": round(c.separation_sec, 1),
        "requiredSeparationSec": round(c.required_separation_sec, 1),
        # extra fields (ignored by existing panels, used by new UI)
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
    return {
        "activeConflicts": k.active_conflicts,
        "trainsTracked": k.trains_tracked,
        "totalDelaySec": round(k.total_delay_sec, 1),
        "averageDelaySec": round(k.average_delay_sec, 1),
        "passengerDelaySec": round(k.passenger_delay_sec, 1),
        "freightDelaySec": round(k.freight_delay_sec, 1),
        "throughputPerHour": k.throughput_per_hour,
        "platformUtilisation": round(k.platform_utilisation, 4),
        "onTimePercent": round(k.on_time_percent, 2),
        "recoveryTimeSec": k.recovery_time_sec,
        "predictedConflicts": k.predicted_conflicts,
    }


def causal_chain_list(eng: SimulationEngine, limit: int = 40) -> list[dict]:
    return [l.as_dict() for l in eng.causal_links[-limit:]]


def delay_buckets_map(eng: SimulationEngine) -> dict[str, dict]:
    return {tid: rt.delays.as_dict() for tid, rt in eng.trains.items() if not rt.finished}
