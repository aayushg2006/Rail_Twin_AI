"""Feature extraction from a twin state (Phase 4).

One shared, ordered numeric feature vector per train, used by the ETA, delay and
conflict models. Deterministic given the state so predictions are reproducible.
"""
from __future__ import annotations

from ..network.fleet import fleet_by_id
from ..network.topology import (resource_by_id, route_by_id, route_resource_use)
from ..twin.engine import route_length
from ..twin.predict import (AnalyticState, Prediction, kmh_to_units_per_sec,
                            occupancy_seconds, project_arrival, project_finish)

TYPE_CODE = {"EXPRESS": 0, "PASSENGER": 1, "LOCAL": 2, "MEMU": 3, "FREIGHT": 4, "SHUNT": 5}

FEATURE_NAMES = [
    "current_delay_seconds",
    "distance_remaining_m",
    "current_speed_kmh",
    "scheduled_remaining_seconds",
    "train_type",
    "priority_class",
    "dwell_remaining_seconds",
    "number_of_stops_remaining",
    "next_resource_dist_m",
    "junction_congestion",
    "downstream_train_count",
    "average_recent_delay",
    "headway_seconds",
    "route_congestion",
    "blocked_ahead",
    "time_of_day",
]


def _next_resource_distance(state: AnalyticState, tid: str) -> tuple[float, str | None]:
    st = state.trains[tid]
    for use in route_resource_use.get(st.route_id, []):
        if use.s > st.s:
            return (use.s - st.s) * 10.0, use.resource_id
    return 1e6, None


def extract(state: AnalyticState, tid: str, pred: Prediction, epoch_hour: float = 12.0) -> dict[str, float]:
    st = state.trains[tid]
    f = fleet_by_id[tid]
    length = route_length(st.route_id)
    route = route_by_id[st.route_id]
    sched_remaining = project_finish(state, tid) or 0.0
    next_dist, next_res = _next_resource_distance(state, tid)

    active = [s for s in state.trains.values() if not s.finished]
    avg_delay = sum(s.delay_sec for s in active) / max(1, len(active))

    downstream = sum(1 for s in state.trains.values()
                     if not s.finished and s.route_id == st.route_id and s.s > st.s)

    # nearest train ahead on the same route -> headway proxy (seconds)
    ahead = [s.s for s in state.trains.values()
             if not s.finished and s.route_id == st.route_id and s.s > st.s]
    if ahead:
        gap_units = min(ahead) - st.s
        headway = gap_units / max(0.1, kmh_to_units_per_sec(max(1.0, st.speed_kmh)))
    else:
        headway = 600.0

    my_conflicts = [c for c in pred.conflicts if tid in (c.train_a, c.train_b)]
    junction_congestion = sum(1 for c in my_conflicts if resource_by_id.get(c.resource_id)
                              and resource_by_id[c.resource_id].kind == "JUNCTION")
    route_congestion = sum(1 for u in route_resource_use.get(st.route_id, []) if u.s > st.s)
    blocked_ahead = 1.0 if (next_res in state.blocked_resources) else 0.0

    return {
        "current_delay_seconds": st.delay_sec,
        "distance_remaining_m": max(0.0, (length - st.s) * 10.0),
        "current_speed_kmh": st.speed_kmh,
        "scheduled_remaining_seconds": sched_remaining,
        "train_type": float(TYPE_CODE.get(f.type, 2)),
        "priority_class": float(f.priority),
        "dwell_remaining_seconds": st.dwell_remaining,
        "number_of_stops_remaining": float(max(0, len(route.stops) - st.next_stop_index)),
        "next_resource_dist_m": next_dist,
        "junction_congestion": float(junction_congestion),
        "downstream_train_count": float(downstream),
        "average_recent_delay": avg_delay,
        "headway_seconds": min(headway, 1200.0),
        "route_congestion": float(route_congestion),
        "blocked_ahead": blocked_ahead,
        "time_of_day": epoch_hour,
    }


def vector(feat: dict[str, float]) -> list[float]:
    return [feat[name] for name in FEATURE_NAMES]
