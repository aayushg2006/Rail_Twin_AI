"""Fast analytic projection and conflict prediction.

Operates on a lightweight `AnalyticState` captured from the authoritative SimPy
twin. Each train's remaining run is projected under the same longitudinal
dynamics the twin uses, producing the time it enters and clears every junction,
block section and platform road ahead of it. Where two movements are closer over
a shared resource than its headway allows, that is a predicted conflict.

Cheap enough to run many times per tick, so the what-if layer can re-project the
whole network for every candidate action without cloning the SimPy environment.
"""
from __future__ import annotations

import copy
import heapq
from dataclasses import dataclass, field

from ..network.chainage import Position
from ..network.net import lines, resources as net_resources
from ..network.routes import RailRoute
from .dynamics import build_profile, traction_for

DEFAULT_HORIZON_SEC = 900
KMH = 1000.0 / 3600.0
EPS = 1e-6


@dataclass
class AnalyticTrain:
    train_id: str
    route_id: str
    s: float                    # metres along route
    speed_ms: float
    line_speed_kmh: float
    regulated_kmh: float | None
    lateness_sec: float
    next_use_index: int
    dwell_remaining: float
    hold_remaining: float
    finished: bool
    admitted: bool
    priority: int
    category: str
    service_class: str
    booked_dep_sec: int = 0
    entry_at_sec: float = 0.0
    departed_platform: bool = False
    source: str = ""
    provenance: str = ""
    current_resource_id: str | None = None
    queued_resource_id: str | None = None
    data_source: str = "TIMETABLE_ESTIMATE"
    observed_at_epoch: float | None = None
    scheduled_hold_sec: float = 0.0
    scheduled_hold_resource_id: str | None = None
    scheduled_release_at_sec: float | None = None

    @property
    def is_freight(self) -> bool:
        return self.category in ("FREIGHT", "SHUNT")


@dataclass
class AnalyticState:
    sim_time: float
    service_epoch_sec: float
    trains: dict[str, AnalyticTrain]
    routes: dict[str, RailRoute]
    blocked_resources: set[str] = field(default_factory=set)
    headway_multiplier: float = 1.0
    unavailable_routes: set[str] = field(default_factory=set)
    resources: dict[str, "AnalyticResource"] = field(default_factory=dict)

    def clone(self) -> "AnalyticState":
        return AnalyticState(
            sim_time=self.sim_time, service_epoch_sec=self.service_epoch_sec,
            trains={k: copy.copy(v) for k, v in self.trains.items()},
            routes=dict(self.routes),
            blocked_resources=set(self.blocked_resources),
            headway_multiplier=self.headway_multiplier,
            unavailable_routes=set(self.unavailable_routes),
            resources={k: copy.copy(v) for k, v in self.resources.items()},
        )


@dataclass
class AnalyticResource:
    resource_id: str
    free_at_sec: float = 0.0       # seconds from the analytic snapshot
    owner: str | None = None
    queued: tuple[str, ...] = ()
    blocked: bool = False


@dataclass
class Conflict:
    id: str
    kind: str
    severity: str                    # CRITICAL | WARNING
    resource_id: str
    resource_label: str
    resource_kind: str
    at: Position
    train_a: str
    train_b: str
    eta_sec: float                   # seconds until the first movement arrives
    separation_sec: float
    required_separation_sec: float
    probability: float = 0.0         # filled by the ML layer
    time_to_conflict: float = 0.0
    episode_id: str = ""
    evidence: str = "PREDICTED"
    confidence: float = 0.5
    first_seen_sec: float | None = None
    last_seen_sec: float | None = None


@dataclass
class Prediction:
    horizon_sec: float
    conflicts: list[Conflict]
    plans: dict[str, list["PassWindow"]]
    paths: dict[str, list[Position]]
    # Earliest demand windows before interlocking queues are imposed.  Conflict
    # detection and CP-SAT use these; `plans` contains the realizable queued
    # schedule used for consequences and controller validation.
    free_plans: dict[str, list["PassWindow"]] = field(default_factory=dict)
    queue_delay_sec: dict[str, float] = field(default_factory=dict)


@dataclass
class PassWindow:
    train_id: str
    resource_id: str
    enter: float
    exit: float
    s: float


@dataclass
class QueueRollout:
    plans: dict[str, list[PassWindow]]
    finish_sec: dict[str, float]
    queue_delay_sec: dict[str, float]
    blocked_trains: set[str] = field(default_factory=set)


def _limit_for(state: AnalyticState, st: AnalyticTrain, s: float) -> float:
    route = state.routes.get(st.train_id)
    limit = st.line_speed_kmh
    if route is not None:
        line = lines.get(route.line_at(s))
        if line:
            limit = min(limit, line.speed_limit_kmh)
    if st.regulated_kmh is not None:
        limit = min(limit, st.regulated_kmh)
    return max(5.0, limit) * KMH


def project_plan(state: AnalyticState, tid: str) -> list[PassWindow]:
    """When this train enters and clears every resource still ahead of it."""
    st = state.trains.get(tid)
    route = state.routes.get(tid)
    if st is None or route is None or st.finished:
        return []

    t = max(0.0, st.entry_at_sec - state.sim_time) + st.hold_remaining
    s = st.s
    v = st.speed_ms
    traction = traction_for(st.service_class)
    out: list[PassWindow] = []

    for use in route.uses:
        if use.exit_s <= s + EPS:
            continue
        if st.scheduled_hold_resource_id == use.resource_id:
            if st.scheduled_release_at_sec is not None:
                t += max(0.0, st.scheduled_release_at_sec - (state.sim_time + t))
            else:
                t += st.scheduled_hold_sec
        is_platform = use.resource_id == route.platform_id
        already_inside = (
            st.current_resource_id == use.resource_id
            or (is_platform and st.dwell_remaining > 0
                and use.enter_s - EPS <= s <= use.exit_s + EPS)
        )
        if already_inside:
            enter = 0.0
            if is_platform:
                t = max(1.0, st.dwell_remaining)
                v = 0.0
            else:
                limit = _limit_for(state, st, s)
                profile = build_profile(
                    max(0.0, use.exit_s - s), v, limit, limit, traction)
                t = profile.duration
                v = profile.v_exit
            s = max(s, use.exit_s)
            out.append(PassWindow(tid, use.resource_id, enter, t, use.enter_s))
            continue
        limit = _limit_for(state, st, max(s, use.enter_s))
        if use.enter_s > s + EPS:
            v_exit = 0.0 if is_platform else limit
            profile = build_profile(use.enter_s - s, v, limit, v_exit, traction)
            t += profile.duration
            v = profile.v_exit
            s = use.enter_s
        enter = t
        if is_platform:
            stop = route.stops[0] if route.stops else None
            t += stop.dwell_sec if stop else 0.0
            v = 0.0
        else:
            profile = build_profile(max(0.0, use.exit_s - s), v, limit, limit, traction)
            t += profile.duration
            v = profile.v_exit
        s = max(s, use.exit_s)
        out.append(PassWindow(tid, use.resource_id, enter, t, use.enter_s))
    return out


def queue_rollout(state: AnalyticState,
                  free_plans: dict[str, list[PassWindow]] | None = None,
                  horizon_sec: float | None = None) -> QueueRollout:
    """Propagate FIFO resource queues over every remaining train path.

    This is deliberately small and deterministic, but it mirrors the part of
    the SimPy twin that the old predictor omitted: a delayed resource entry
    shifts every downstream arrival, which can in turn join a different queue.
    Both the no-action reference and every candidate pass through this exact
    scheduler.
    """
    source = free_plans or {
        tid: project_plan(state, tid)
        for tid, st in state.trains.items() if not st.finished
    }
    plans: dict[str, list[PassWindow]] = {tid: [] for tid in source}
    queue_delay = {tid: 0.0 for tid in source}
    finish: dict[str, float] = {}
    blocked: set[str] = set()

    # Per-resource availability starts with the live headway gate.  An owner
    # that appears in the plans will naturally be dispatched first at t=0.
    available = {
        rid: max(0.0, snap.free_at_sec)
        for rid, snap in state.resources.items()
    }
    queue_rank: dict[tuple[str, str], int] = {}
    for rid, snap in state.resources.items():
        if snap.owner:
            queue_rank[(rid, snap.owner)] = -1
        for index, tid in enumerate(snap.queued):
            queue_rank[(rid, tid)] = index

    # A heap item is the earliest time a train can request its next resource.
    # The original free-window times provide travel gaps between resources;
    # realized exit times carry every upstream wait forward.
    heap: list[tuple[float, int, int, str, int]] = []
    serial = 0
    for tid, seq in source.items():
        if not seq:
            route = state.routes.get(tid)
            if route is not None:
                free_finish = project_finish_free(state, tid)
                if free_finish is not None:
                    finish[tid] = free_finish
            continue
        first = seq[0]
        heapq.heappush(heap, (
            first.enter, queue_rank.get((first.resource_id, tid), 10_000),
            serial, tid, 0))
        serial += 1

    while heap:
        ready, _, _, tid, index = heapq.heappop(heap)
        seq = source[tid]
        window = seq[index]
        rid = window.resource_id
        spec = net_resources.get(rid)
        if spec is None:
            continue
        if rid in state.blocked_resources or state.resources.get(
                rid, AnalyticResource(rid)).blocked:
            blocked.add(tid)
            finish[tid] = (horizon_sec + 3600.0
                           if horizon_sec is not None else 86_400.0)
            continue

        start = max(ready, available.get(rid, 0.0))
        duration = max(1.0, window.exit - window.enter)
        end = start + duration
        queue_delay[tid] += max(0.0, start - ready)
        plans[tid].append(PassWindow(tid, rid, start, end, window.s))
        available[rid] = end + spec.headway_sec * state.headway_multiplier

        if index + 1 < len(seq):
            nxt = seq[index + 1]
            travel_gap = max(0.0, nxt.enter - window.exit)
            next_ready = end + travel_gap
            if horizon_sec is None or next_ready <= horizon_sec + 3600.0:
                heapq.heappush(heap, (
                    next_ready,
                    queue_rank.get((nxt.resource_id, tid), 10_000),
                    serial, tid, index + 1))
                serial += 1
        else:
            free_finish = project_finish_free(state, tid)
            tail = max(0.0, (free_finish or window.exit) - window.exit)
            finish[tid] = end + tail

    return QueueRollout(plans, finish, queue_delay, blocked)


def project_arrival(state: AnalyticState, tid: str, target_s: float) -> float | None:
    """Seconds until this train reaches `target_s` metres along its route."""
    st = state.trains.get(tid)
    route = state.routes.get(tid)
    if st is None or route is None or st.finished or target_s < st.s - 1:
        return None
    t = max(0.0, st.entry_at_sec - state.sim_time) + st.hold_remaining + st.dwell_remaining
    s, v = st.s, st.speed_ms
    traction = traction_for(st.service_class)
    for stop in route.stops:
        if stop.s > target_s or stop.s <= s:
            continue
        limit = _limit_for(state, st, s)
        profile = build_profile(stop.s - s, v, limit, 0.0, traction)
        t += profile.duration + stop.dwell_sec
        s, v = stop.s, 0.0
    if target_s > s:
        limit = _limit_for(state, st, s)
        t += build_profile(target_s - s, v, limit, limit, traction).duration
    return t


def project_finish_free(state: AnalyticState, tid: str) -> float | None:
    route = state.routes.get(tid)
    if route is None:
        return None
    return project_arrival(state, tid, route.length_m)


def project_finish(state: AnalyticState, tid: str) -> float | None:
    """Queue-aware finish time for one train in the shared network rollout."""
    return queue_rollout(state).finish_sec.get(tid)


def apply_action(state: AnalyticState, action) -> AnalyticState:
    """A NEW analytic state with the action applied; never mutates the input."""
    from ..network.fleet import fleet_by_id
    from ..network.routes import route_template

    nxt = state.clone()
    st = nxt.trains.get(action.train_id)
    if st is None:
        return nxt
    kind = action.kind
    if kind == "SPEED_REGULATION" and action.speed_kmh:
        st.regulated_kmh = max(5.0, float(action.speed_kmh))
    elif kind == "HOLD" and action.hold_sec:
        st.scheduled_hold_sec = float(action.hold_sec)
        st.scheduled_hold_resource_id = action.effective_resource_id
        st.scheduled_release_at_sec = action.release_at_sec
        if action.effective_resource_id is None:
            st.hold_remaining = max(st.hold_remaining, float(action.hold_sec))
    elif kind in ("PLATFORM_REASSIGNMENT", "ALTERNATE_ROUTE"):
        f = fleet_by_id.get(action.train_id)
        current = nxt.routes.get(action.train_id)
        target_pf = action.platform_id or (current.platform_id if current else None)
        if f and current and target_pf:
            new_route = route_template(f.arrival_corridor, f.departure_corridor,
                                       f.service_class, target_pf, f.dwell_sec)
            # Chainage is shared between routes, so `s` carries over unchanged -
            # a reassignment can never teleport a train forward.
            nxt.routes[action.train_id] = new_route
            st.route_id = new_route.id
    return nxt


def predict(state: AnalyticState, horizon_sec: float = DEFAULT_HORIZON_SEC) -> Prediction:
    free_plans: dict[str, list[PassWindow]] = {}
    windows: dict[str, list[PassWindow]] = {}
    for tid, st in state.trains.items():
        if st.finished:
            continue
        plan = project_plan(state, tid)
        free_plans[tid] = plan
        for w in plan:
            if w.enter <= horizon_sec:
                windows.setdefault(w.resource_id, []).append(w)

    conflicts: list[Conflict] = []
    for rid, lst in windows.items():
        spec = net_resources.get(rid)
        if spec is None:
            continue
        required = spec.headway_sec * state.headway_multiplier
        ordered = sorted(lst, key=lambda w: w.enter)
        at = Position(spec.corridor, spec.centre_m)

        if rid in state.blocked_resources:
            for w in ordered:
                conflicts.append(Conflict(
                    id=f"CF-BLK-{rid}-{w.train_id}",
                    kind="RESOURCE_UNAVAILABLE", severity="CRITICAL",
                    resource_id=rid, resource_label=spec.label, resource_kind=spec.kind,
                    at=at, train_a=w.train_id, train_b="", eta_sec=w.enter,
                    separation_sec=0.0, required_separation_sec=required,
                    time_to_conflict=w.enter,
                    **_evidence(state, rid, w.train_id, "")))
            continue

        for i in range(1, len(ordered)):
            a, b = ordered[i - 1], ordered[i]
            if a.train_id == b.train_id:
                continue
            separation = b.enter - a.exit
            if separation >= required:
                continue
            # CRITICAL means the movements actually overlap on the resource, so
            # the second train WILL be stopped at the protecting signal. WARNING
            # means the headway is infringed but the movement still clears --
            # the train gets a caution and loses time rather than stopping.
            severity = "CRITICAL" if separation < 0 else "WARNING"
            if spec.kind == "JUNCTION":
                kind = "JUNCTION_CONTENTION"
            elif spec.kind == "PLATFORM":
                kind = "PLATFORM_OCCUPATION_OVERLAP"
            elif separation < 0:
                kind = "BLOCK_OCCUPANCY"
            else:
                kind = "HEADWAY_VIOLATION_RISK"
            conflicts.append(Conflict(
                id=f"CF-{rid}-{a.train_id}-{b.train_id}", kind=kind, severity=severity,
                resource_id=rid, resource_label=spec.label, resource_kind=spec.kind,
                at=at, train_a=a.train_id, train_b=b.train_id, eta_sec=a.enter,
                separation_sec=separation, required_separation_sec=required,
                time_to_conflict=a.enter,
                **_evidence(state, rid, a.train_id, b.train_id)))

    conflicts.sort(key=lambda c: (0 if c.severity == "CRITICAL" else 1, c.eta_sec))

    paths: dict[str, list[Position]] = {}
    for tid, st in state.trains.items():
        if st.finished or not st.admitted:
            continue
        route = state.routes.get(tid)
        if route is None:
            continue
        end_s = _reach_within(state, tid, horizon_sec, route.length_m)
        paths[tid] = route.path.slice(st.s, end_s)

    queued = queue_rollout(state, free_plans, horizon_sec)
    return Prediction(
        horizon_sec, conflicts, queued.plans, paths,
        free_plans=free_plans, queue_delay_sec=queued.queue_delay_sec)


def _evidence(state: AnalyticState, resource_id: str,
              train_a: str, train_b: str) -> dict:
    trains = [state.trains.get(tid) for tid in (train_a, train_b) if tid]
    sources = {st.data_source for st in trains if st is not None}
    if "SYNTHETIC_FREIGHT" in sources:
        evidence = "HYBRID_PREDICTION"
    elif sources and sources <= {"RAILRADAR_LIVE"}:
        evidence = "LIVE_ASSIMILATED_PREDICTION"
    elif "RAILRADAR_REPLAY" in sources:
        evidence = "REPLAY_PREDICTION"
    else:
        evidence = "PREDICTED"
    confidence_by_source = {
        "RAILRADAR_LIVE": 0.85,
        "RAILRADAR_REPLAY": 0.6,
        "SYNTHETIC_FREIGHT": 0.7,
        "TIMETABLE_ESTIMATE": 0.45,
    }
    confidence = min(
        (confidence_by_source.get(source, 0.45) for source in sources),
        default=0.45)
    pair = sorted(tid for tid in (train_a, train_b) if tid)
    episode = f"EP-{resource_id}-{'-'.join(pair) if pair else 'NA'}"
    return {"episode_id": episode, "evidence": evidence,
            "confidence": confidence}


def _reach_within(state: AnalyticState, tid: str, horizon: float, max_s: float) -> float:
    """How far along its route the train gets inside the horizon (bisection on
    the projection, which is monotonic in distance)."""
    st = state.trains[tid]
    lo, hi = st.s, max_s
    if (project_arrival(state, tid, hi) or 0.0) <= horizon:
        return hi
    for _ in range(18):
        mid = (lo + hi) / 2
        t = project_arrival(state, tid, mid)
        if t is None or t <= horizon:
            lo = mid
        else:
            hi = mid
    return lo


def occupancy_seconds(state: AnalyticState, tid: str, rid: str) -> float:
    """How long this train occupies the resource, from its projected plan."""
    for w in project_plan(state, tid):
        if w.resource_id == rid:
            return max(0.0, w.exit - w.enter)
    spec = net_resources.get(rid)
    st = state.trains.get(tid)
    if spec is None or st is None:
        return 0.0
    return spec.length_m / max(1.0, st.line_speed_kmh * KMH)


def project_state_at(state: AnalyticState, offset_sec: float) -> AnalyticState:
    """Free-running projection of every train `offset_sec` into the future."""
    if offset_sec <= 0:
        return state
    out = state.clone()
    out.sim_time += offset_sec
    for tid, st in out.trains.items():
        route = out.routes.get(tid)
        if st.finished or route is None:
            continue
        remaining = offset_sec
        hold = min(st.hold_remaining, remaining)
        st.hold_remaining -= hold
        remaining -= hold
        dwell = min(st.dwell_remaining, remaining)
        st.dwell_remaining -= dwell
        remaining -= dwell
        if remaining <= 0:
            continue
        target = _advance_by_time(state, tid, remaining, route.length_m)
        st.s = target
        if target >= route.length_m - EPS:
            st.finished = True
    return out


def _advance_by_time(state: AnalyticState, tid: str, seconds: float, max_s: float) -> float:
    st = state.trains[tid]
    lo, hi = st.s, max_s
    if (project_arrival(state, tid, hi) or 0.0) <= seconds:
        return hi
    for _ in range(18):
        mid = (lo + hi) / 2
        t = project_arrival(state, tid, mid)
        if t is None or t <= seconds:
            lo = mid
        else:
            hi = mid
    return lo
