"""Feature extraction from a twin state.

One shared, ordered numeric feature vector per train, used by the ETA, delay and
conflict models. Deterministic given the state, so predictions are reproducible.

All distances are metres and all speeds km/h - real physical quantities, not
drawing units.
"""
from __future__ import annotations

from ..network.fleet import fleet_by_id
from ..network.net import resources as net_resources
from ..twin.predict import (AnalyticState, Prediction, project_finish)

TYPE_CODE = {"EXPRESS": 0, "PASSENGER": 1, "LOCAL": 2, "MEMU": 3,
             "FREIGHT": 4, "SHUNT": 5}

FEATURE_NAMES = [
    "current_lateness_seconds",
    "distance_remaining_m",
    "current_speed_kmh",
    "line_speed_kmh",
    "projected_remaining_seconds",
    "train_type",
    "priority_class",
    "passenger_load",
    "dwell_remaining_seconds",
    "hold_remaining_seconds",
    "next_resource_dist_m",
    "next_resource_headway_sec",
    "junction_congestion",
    "same_line_ahead_count",
    "network_mean_lateness",
    "headway_seconds",
    "resources_remaining",
    "blocked_ahead",
    "time_of_day_hours",
]


def _next_resource(state: AnalyticState, tid: str) -> tuple[float, str | None]:
    st = state.trains[tid]
    route = state.routes.get(tid)
    if route is None:
        return 1e6, None
    for use in route.uses:
        if use.enter_s > st.s:
            return use.enter_s - st.s, use.resource_id
    return 1e6, None


def extract(state: AnalyticState, tid: str, pred: Prediction,
            epoch_hour: float | None = None) -> dict[str, float]:
    st = state.trains[tid]
    f = fleet_by_id[tid]
    route = state.routes.get(tid)
    length = route.length_m if route else 0.0
    next_dist, next_res = _next_resource(state, tid)
    spec = net_resources.get(next_res) if next_res else None

    active = [s for s in state.trains.values() if not s.finished and s.admitted]
    mean_lateness = sum(s.lateness_sec for s in active) / max(1, len(active))

    # Nearest movement ahead on the same running line, as a headway proxy.
    my_line = route.line_at(st.s) if route else None
    ahead: list[float] = []
    for other_id, other in state.trains.items():
        if other_id == tid or other.finished:
            continue
        other_route = state.routes.get(other_id)
        if other_route is None or my_line is None:
            continue
        if other_route.line_at(other.s) == my_line and other.s > st.s:
            ahead.append(other.s - st.s)
    if ahead:
        headway = min(ahead) / max(0.5, st.speed_ms or 1.0)
    else:
        headway = 600.0

    my_conflicts = [c for c in pred.conflicts if tid in (c.train_a, c.train_b)]
    junction_congestion = sum(1 for c in my_conflicts if c.resource_kind == "JUNCTION")
    remaining = sum(1 for u in (route.uses if route else ()) if u.enter_s > st.s)

    if epoch_hour is None:
        epoch_hour = ((state.service_epoch_sec + state.sim_time) / 3600.0) % 24

    return {
        "current_lateness_seconds": st.lateness_sec,
        "distance_remaining_m": max(0.0, length - st.s),
        "current_speed_kmh": st.speed_ms * 3.6,
        "line_speed_kmh": st.line_speed_kmh,
        "projected_remaining_seconds": project_finish(state, tid) or 0.0,
        "train_type": float(TYPE_CODE.get(f.category, 2)),
        "priority_class": float(f.priority),
        "passenger_load": float(f.typical_load),
        "dwell_remaining_seconds": st.dwell_remaining,
        "hold_remaining_seconds": st.hold_remaining,
        "next_resource_dist_m": next_dist,
        "next_resource_headway_sec": float(spec.headway_sec if spec else 0.0),
        "junction_congestion": float(junction_congestion),
        "same_line_ahead_count": float(len(ahead)),
        "network_mean_lateness": mean_lateness,
        "headway_seconds": min(headway, 1200.0),
        "resources_remaining": float(remaining),
        "blocked_ahead": 1.0 if (next_res in state.blocked_resources) else 0.0,
        "time_of_day_hours": float(epoch_hour),
    }


def vector(feat: dict[str, float]) -> list[float]:
    """Ordered float vector. Coerced explicitly - a stray int reaching XGBoost
    is silent until it is not."""
    return [float(feat[name]) for name in FEATURE_NAMES]
