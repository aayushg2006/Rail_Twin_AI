"""Fast analytic projection & conflict prediction (Phases 3/5).

Operates on a lightweight `AnalyticState` captured from the authoritative SimPy
twin. This is deterministic time-window conflict detection over projected
resource occupancy — the same technique the frontend used, now fed by the real
DES state and later augmented with ML conflict-probability. Used for the live
conflict list, projected paths, the scrubber, and what-if candidate evaluation
(cheap enough to run many times per tick without cloning the SimPy environment).
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field

from ..network.geometry import (Point, crossing_distance, kmh_to_units_per_sec,
                                path_slice, point_at)
from ..network.topology import (resource_by_id, route_by_id, route_resource_use)

DEFAULT_HORIZON_SEC = 900


@dataclass
class AnalyticTrain:
    train_id: str
    route_id: str
    s: float
    speed_kmh: float
    nominal_speed_kmh: float
    delay_sec: float
    next_stop_index: int
    dwell_remaining: float
    hold_remaining: float
    finished: bool
    priority: int
    ttype: str


@dataclass
class AnalyticState:
    sim_time: float
    trains: dict[str, AnalyticTrain]
    blocked_resources: set[str] = field(default_factory=set)
    headway_multiplier: float = 1.0
    unavailable_routes: set[str] = field(default_factory=set)

    def clone(self) -> "AnalyticState":
        return AnalyticState(
            sim_time=self.sim_time,
            trains={k: copy.copy(v) for k, v in self.trains.items()},
            blocked_resources=set(self.blocked_resources),
            headway_multiplier=self.headway_multiplier,
            unavailable_routes=set(self.unavailable_routes),
        )


@dataclass
class Conflict:
    id: str
    kind: str
    severity: str          # CRITICAL | WARNING
    resource_id: str
    resource_label: str
    at: Point
    train_a: str
    train_b: str
    eta_sec: float
    separation_sec: float
    required_separation_sec: float
    probability: float = 0.0   # filled by ML layer (Phase 4)
    time_to_conflict: float = 0.0


@dataclass
class Prediction:
    horizon_sec: float
    conflicts: list[Conflict]
    paths: dict[str, list[Point]]


def _route_length(route_id: str) -> float:
    from .engine import route_length
    return route_length(route_id)


def project_arrival(state: AnalyticState, tid: str, s_target: float) -> float | None:
    st = state.trains.get(tid)
    if st is None or st.finished:
        return None
    route = route_by_id[st.route_id]
    if s_target < st.s - 1:
        return None
    v = kmh_to_units_per_sec(max(1.0, st.speed_kmh))
    t = st.hold_remaining + st.dwell_remaining
    d = st.s
    for i in range(st.next_stop_index, len(route.stops)):
        stop = route.stops[i]
        if stop.s > s_target:
            break
        if stop.s <= d:
            continue
        t += (stop.s - d) / v
        d = stop.s
        t += stop.dwell_sec
    t += max(0.0, s_target - d) / v
    return t


def project_finish(state: AnalyticState, tid: str) -> float | None:
    st = state.trains.get(tid)
    if st is None:
        return None
    return project_arrival(state, tid, _route_length(st.route_id))


def occupancy_seconds(state: AnalyticState, tid: str, rid: str) -> float:
    res = resource_by_id[rid]
    st = state.trains[tid]
    v = kmh_to_units_per_sec(max(1.0, st.speed_kmh))
    base = (res.radius * 2) / v
    if res.kind == "PLATFORM":
        route = route_by_id[st.route_id]
        for stop in route.stops:
            if stop.platform_id == rid:
                return base + stop.dwell_sec
    return base


def analytic_tick(state: AnalyticState, dt: float) -> None:
    """Free-running projection (no resource contention) — mirrors the frontend
    tick, used to project positions/paths/finish times."""
    state.sim_time += dt
    for st in state.trains.values():
        if st.finished:
            continue
        route = route_by_id[st.route_id]
        length = _route_length(st.route_id)
        if st.hold_remaining > 0:
            st.hold_remaining = max(0.0, st.hold_remaining - dt)
            continue
        if st.dwell_remaining > 0:
            st.dwell_remaining = max(0.0, st.dwell_remaining - dt)
            continue
        v = kmh_to_units_per_sec(max(1.0, st.speed_kmh))
        remaining = v * dt
        while remaining > 0 and not st.finished:
            nxt = route.stops[st.next_stop_index] if st.next_stop_index < len(route.stops) else None
            target = nxt.s if nxt else length
            step = min(remaining, target - st.s)
            st.s += step
            remaining -= step
            if st.s >= target - 1e-3:
                if nxt:
                    st.s = nxt.s
                    st.dwell_remaining = nxt.dwell_sec
                    st.next_stop_index += 1
                    break
                st.finished = True
                break


def advance_to(state: AnalyticState, target: float, step: float = 10.0) -> AnalyticState:
    out = state.clone()
    guard = 0
    while out.sim_time < target - 1e-3 and guard < 5000:
        analytic_tick(out, min(step, target - out.sim_time))
        guard += 1
    return out


def project_state_at(state: AnalyticState, offset_sec: float) -> AnalyticState:
    if offset_sec <= 0:
        return state
    return advance_to(state.clone(), state.sim_time + offset_sec, 5.0)


def apply_action(state: AnalyticState, action) -> AnalyticState:
    """Return a NEW analytic state with the action applied (never mutates input).
    Mirrors the frontend applyAction so what-if evaluation is consistent."""
    nxt = state.clone()
    st = nxt.trains.get(action.train_id)
    if st is None:
        return nxt
    kind = action.kind
    if kind == "SPEED_REGULATION" and action.speed_kmh:
        st.speed_kmh = max(5.0, action.speed_kmh)
    elif kind == "HOLD" and action.hold_sec:
        st.hold_remaining += action.hold_sec
    elif kind in ("ALTERNATE_ROUTE", "PLATFORM_REASSIGNMENT") and action.route_id:
        new_route = route_by_id.get(action.route_id)
        if new_route:
            here = point_at(route_by_id[st.route_id].path, st.s)
            remapped = crossing_distance(new_route.path, here, 400)
            st.route_id = action.route_id
            st.s = remapped if remapped is not None else 0.0
            nsi = next((i for i, stop in enumerate(new_route.stops) if stop.s > st.s), None)
            st.next_stop_index = nsi if nsi is not None else len(new_route.stops)
    return nxt


@dataclass
class _Window:
    train_id: str
    enter: float
    exit: float
    s: float


def predict(state: AnalyticState, horizon_sec: float = DEFAULT_HORIZON_SEC) -> Prediction:
    windows: dict[str, list[_Window]] = {}
    for tid, st in state.trains.items():
        if st.finished:
            continue
        for use in route_resource_use.get(st.route_id, []):
            if use.s < st.s:
                continue
            t = project_arrival(state, tid, use.s)
            if t is None or t > horizon_sec:
                continue
            occ = occupancy_seconds(state, tid, use.resource_id)
            windows.setdefault(use.resource_id, []).append(
                _Window(tid, t, t + occ, use.s))

    conflicts: list[Conflict] = []
    for rid, lst in windows.items():
        res = resource_by_id[rid]
        required = res.headway_sec * state.headway_multiplier
        ordered = sorted(lst, key=lambda w: w.enter)

        if rid in state.blocked_resources:
            for w in ordered:
                conflicts.append(Conflict(
                    id=f"CF-BLK-{rid}-{w.train_id}",
                    kind="PLATFORM_OCCUPATION_OVERLAP" if res.kind == "PLATFORM" else "ROUTE_CONFLICT",
                    severity="CRITICAL", resource_id=rid, resource_label=res.label,
                    at=res.at, train_a=w.train_id, train_b="", eta_sec=w.enter,
                    separation_sec=0.0, required_separation_sec=required,
                    time_to_conflict=w.enter))
            continue

        for i in range(1, len(ordered)):
            a = ordered[i - 1]
            b = ordered[i]
            separation = b.enter - a.exit
            if separation >= required:
                continue
            severity = "CRITICAL" if separation < required * 0.6 else "WARNING"
            if res.kind == "JUNCTION":
                kind = "JUNCTION_CONTENTION"
            elif res.kind == "PLATFORM":
                kind = "PLATFORM_OCCUPATION_OVERLAP"
            elif separation < 0:
                kind = "BLOCK_OCCUPANCY"
            else:
                kind = "HEADWAY_VIOLATION_RISK"
            conflicts.append(Conflict(
                id=f"CF-{rid}-{a.train_id}-{b.train_id}", kind=kind, severity=severity,
                resource_id=rid, resource_label=res.label, at=res.at,
                train_a=a.train_id, train_b=b.train_id, eta_sec=a.enter,
                separation_sec=separation, required_separation_sec=required,
                time_to_conflict=a.enter))

    conflicts.sort(key=lambda c: (0 if c.severity == "CRITICAL" else 1, c.eta_sec))

    paths: dict[str, list[Point]] = {}
    future = advance_to(state.clone(), state.sim_time + horizon_sec, 10.0)
    for tid, st in state.trains.items():
        if st.finished:
            continue
        route = route_by_id[st.route_id]
        fst = future.trains[tid]
        end_s = _route_length(st.route_id) if fst.finished else fst.s
        paths[tid] = path_slice(route.path, st.s, end_s)
    return Prediction(horizon_sec, conflicts, paths)
