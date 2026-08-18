"""SimulationEngine — the authoritative SimPy discrete-event digital twin (Phase 3).

Trains are SimPy processes that travel their route segment by segment and acquire
junction / block / platform resources (with headway) before entering them. Real
contention produces real waits and per-cause delay accumulation — nothing is
scripted. The simulation clock is independent of wall-clock time and can be
advanced at any rate (frontend speeds 1/5/10/20).

The engine also exposes a lightweight `analytic_state()` used by the fast
prediction / what-if layer (predict.py) that drives the conflict & options UI.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

import simpy

from ..config import settings
from ..network.fleet import fleet, fleet_by_id
from ..network.geometry import (crossing_distance, kmh_to_units_per_sec,
                                path_length, point_at)
from ..network.topology import (resource_by_id, route_by_id, route_resource_use)
from ..network.scenarios import ScenarioSetup, scenario_setup
from .resources import BLOCKED_FREE_AT, ManagedResource, build_resources
from .state import (AppliedAction, CausalLink, DelayBuckets, RESOURCE_WAIT_BUCKET,
                    TrainRuntime, TrainStatus)

EPS = 1e-6


@dataclass
class DelayEvent:
    event_id: str
    target_type: str        # TRAIN | PLATFORM | TRACK | JUNCTION | BLOCK | SPEED | PRIORITY
    target_id: str
    delay_seconds: float = 0.0
    reason: str = ""
    severity: str = "MEDIUM"
    scenario_id: str = ""
    timestamp: float = 0.0

    def as_dict(self) -> dict:
        return {
            "event_id": self.event_id, "target_type": self.target_type,
            "target_id": self.target_id, "delay_seconds": self.delay_seconds,
            "reason": self.reason, "severity": self.severity,
            "scenario_id": self.scenario_id, "timestamp": self.timestamp,
        }


_len_cache: dict[str, float] = {}

# Active window of the fixed daily snapshot (earliest..latest scheduled
# departure). LIVE mode remaps any real time-of-day into this window so the
# console is populated at any hour instead of showing an all-completed network.
_departures = [f.scheduled_departure_sec for f in fleet if f.scheduled_departure_sec]
WINDOW_START = min(_departures) if _departures else 0
WINDOW_END = max(_departures) if _departures else 86400


def remap_into_window(service_seconds: float) -> float:
    """Fold a time-of-day into the snapshot's active window (LIVE mode)."""
    span = WINDOW_END - WINDOW_START
    if span <= 0 or WINDOW_START <= service_seconds <= WINDOW_END:
        return service_seconds
    return WINDOW_START + ((service_seconds - WINDOW_START) % span)


def route_length(route_id: str) -> float:
    if route_id not in _len_cache:
        _len_cache[route_id] = path_length(route_by_id[route_id].path)
    return _len_cache[route_id]


class SimulationEngine:
    def __init__(self, scenario_id: str = "BASE", seed: int | None = None,
                 epoch_start_ms: int | None = None, clock_mode: str = "DEMO"):
        self.scenario_id = scenario_id
        self.seed = settings.seed if seed is None else seed
        self.clock_mode = (clock_mode or "DEMO").upper()
        # Direct engine consumers/tests use the deterministic snapshot clock.
        # The live orchestrator passes its wall-clock epoch explicitly.
        self.epoch_start_ms = epoch_start_ms or settings.demo_epoch_start_ms
        instant = datetime.fromtimestamp(self.epoch_start_ms / 1000, tz=ZoneInfo("Asia/Kolkata"))
        self.service_seconds = instant.hour * 3600 + instant.minute * 60 + instant.second
        # LIVE runs on the real wall clock; if that time-of-day sits outside the
        # snapshot's active window, fold it in (and shift the display epoch to
        # match) so trains are actually running instead of all pre-completed.
        if self.clock_mode == "LIVE":
            clamped = remap_into_window(self.service_seconds)
            if clamped != self.service_seconds:
                self.epoch_start_ms -= int((self.service_seconds - clamped) * 1000)
                self.service_seconds = clamped
        self.setup: ScenarioSetup = scenario_setup(scenario_id)
        self.env = simpy.Environment()
        self.trains: dict[str, TrainRuntime] = {}
        self.procs: dict[str, simpy.Process] = {}
        self.resources: dict[str, ManagedResource] = build_resources(
            self.env, self.setup.headway_multiplier, set(self.setup.blocked_resources))
        self.blocked_resources: set[str] = set(self.setup.blocked_resources)
        self.unavailable_routes: set[str] = set(self.setup.unavailable_routes)
        self.headway_multiplier: float = self.setup.headway_multiplier
        self.applied_actions: list[AppliedAction] = []
        self.causal_links: list[CausalLink] = []
        self.events: list[DelayEvent] = []
        self._init_trains()
        for tid in self.trains:
            self.procs[tid] = self.env.process(self._train_process(tid))

    # ----------------------------------------------------------------- setup
    def _init_trains(self) -> None:
        for f in fleet:
            ov = self.setup.overrides.get(f.id)
            route = route_by_id[f.route_id]
            base_s = crossing_distance(route.path, f.start, 80) or 0.0
            shift_units = 0.0
            if ov and ov.start_shift:
                # start_shift is in seconds of travel at nominal speed -> map units
                v = kmh_to_units_per_sec(f.nominal_speed_kmh)
                shift_units = ov.start_shift * v
            s = max(0.0, base_s - shift_units)  # positive shift => started later => further back
            departure = int(f.scheduled_departure_sec)
            elapsed_from_departure = self.service_seconds - departure
            nominal_duration = path_length(route.path) / max(EPS, kmh_to_units_per_sec(f.nominal_speed_kmh))
            nominal_duration += sum(stop.dwell_sec for stop in route.stops)
            completed_at_start = elapsed_from_departure > nominal_duration + 300
            activation_at = max(0.0, departure - self.service_seconds)
            speed = ov.speed_kmh if (ov and ov.speed_kmh is not None) else f.nominal_speed_kmh
            nominal = ov.nominal_speed_kmh if (ov and ov.nominal_speed_kmh is not None) else f.nominal_speed_kmh
            rt = TrainRuntime(
                train_id=f.id, route_id=f.route_id, s=s, speed_kmh=speed,
                nominal_speed_kmh=nominal, priority=f.priority, ttype=f.type,
                scheduled_departure_sec=f.scheduled_departure_sec,
                source=f.source, provenance=f.provenance,
                activation_at_sec=activation_at,
                status=TrainStatus.COMPLETED if completed_at_start else TrainStatus.SCHEDULED if activation_at > 0 else TrainStatus.RUNNING,
                finished=completed_at_start,
            )
            rt.delays.base_schedule = f.entry_delay_min * 60.0
            rt.next_stop_index = self._first_stop_index(f.route_id, s)
            self.trains[rt.train_id] = rt

    @staticmethod
    def _first_stop_index(route_id: str, s: float) -> int:
        stops = route_by_id[route_id].stops
        for i, st in enumerate(stops):
            if st.s > s:
                return i
        return len(stops)

    @property
    def now(self) -> float:
        return self.env.now

    # ------------------------------------------------------- train process
    def _route_events(self, rt: TrainRuntime) -> list[tuple[str, float, object]]:
        """Ordered upcoming ('res'|'stop', s, payload) strictly ahead of rt.s."""
        route = route_by_id[rt.route_id]
        stop_platforms = {st.platform_id for st in route.stops}
        out: list[tuple[str, float, object]] = []
        for u in route_resource_use[rt.route_id]:
            res = resource_by_id[u.resource_id]
            if res.kind == "PLATFORM" and u.resource_id in stop_platforms:
                continue  # handled as a stop
            if u.s > rt.s + EPS:
                out.append(("res", u.s, u.resource_id))
        for i, st in enumerate(route.stops):
            if st.s > rt.s + EPS and i >= rt.next_stop_index - 1:
                out.append(("stop", st.s, i))
        out.sort(key=lambda e: e[1])
        return out

    def _train_process(self, tid: str):
        env = self.env
        rt = self.trains[tid]
        while not rt.finished:
            try:
                if env.now < rt.activation_at_sec:
                    rt.status = TrainStatus.SCHEDULED
                    rt.moving = False
                    yield env.timeout(rt.activation_at_sec - env.now)
                    rt.status = TrainStatus.RUNNING
                    continue
                # consume any controller-imposed hold first
                if rt.pending_hold_sec > 0:
                    hold = rt.pending_hold_sec
                    event_part = min(hold, rt.pending_event_hold)
                    rt.pending_hold_sec = 0.0
                    rt.pending_event_hold = max(0.0, rt.pending_event_hold - event_part)
                    rt.status = TrainStatus.HELD
                    rt.moving = False
                    rt.hold_end_t = env.now + hold
                    # Delay is committed the moment the hold starts (credit up front)
                    # so live KPIs reflect it while the train is still standing.
                    # The event portion was already credited at injection.
                    rt.delays.add("hold", hold - event_part)
                    yield env.timeout(hold)

                events = self._route_events(rt)
                length = route_length(rt.route_id)
                target_s, kind, payload = (length, "end", None)
                if events:
                    kind, target_s, payload = events[0]

                # travel to the next event
                if target_s > rt.s + EPS:
                    yield from self._travel(rt, target_s)

                if kind == "end":
                    rt.finished = True
                    rt.status = TrainStatus.COMPLETED
                    rt.moving = False
                    env.process(self._respawn(tid))
                    break
                if kind == "res":
                    yield from self._pass_resource(rt, payload)
                elif kind == "stop":
                    yield from self._dwell(rt, payload)
            except simpy.Interrupt:
                # an action (reroute / immediate hold) changed the plan; re-loop
                rt.moving = False
                continue

    def _travel(self, rt: TrainRuntime, target_s: float):
        env = self.env
        v = kmh_to_units_per_sec(max(1.0, rt.speed_kmh))
        seg = target_s - rt.s
        travel = seg / v
        rt.status = (TrainStatus.REGULATED if rt.speed_kmh < rt.nominal_speed_kmh - 0.5
                     else TrainStatus.RUNNING) if False else rt.status  # keep rich status
        rt.status = TrainStatus.RUNNING
        rt.moving = True
        rt.move_s0, rt.move_s1 = rt.s, target_s
        rt.move_t0, rt.move_t1 = env.now, env.now + travel
        yield env.timeout(travel)
        rt.s = target_s
        rt.moving = False
        if rt.speed_kmh < rt.nominal_speed_kmh - EPS:
            v_nom = kmh_to_units_per_sec(rt.nominal_speed_kmh)
            rt.delays.add("regulation", seg / v - seg / v_nom)

    def _pass_resource(self, rt: TrainRuntime, rid: str):
        env = self.env
        mr = self.resources[rid]
        res = resource_by_id[rid]
        bucket = RESOURCE_WAIT_BUCKET.get(res.kind, "block_wait")
        req = mr.res.request()
        rt.status = TrainStatus.WAITING
        yield req
        # block-clearance wait (indefinite while resource withdrawn from service)
        waited = 0.0
        while mr.blocked:
            rt.status = TrainStatus.WAITING
            rt.wait_end_t = None
            rt.moving = False
            yield env.timeout(2.0)
            waited += 2.0
        # Junctions are decision points: the live twin enforces only physical
        # mutual exclusion (the brief pass-through), NOT the scheduling headway —
        # that upcoming contention is surfaced to the controller as a predicted
        # conflict to resolve. Blocks keep the full headway gate (real waits).
        gate = 0.0 if (res.kind == "JUNCTION" and not mr.blocked) else mr.gate_wait(env.now)
        if gate > 0:
            rt.status = TrainStatus.WAITING
            rt.wait_end_t = env.now + gate
            rt.moving = False
            prev = mr.occupancy[-2].train_id if len(mr.occupancy) >= 2 else (
                mr.occupancy[-1].train_id if mr.occupancy else "")
            yield env.timeout(gate)
            waited += gate
            self._causal(f"{res.kind}_OCCUPANCY", prev or rid, rt.train_id, rid, gate)
        if waited > 0:
            rt.delays.add(bucket, waited)
        rec = mr.on_enter(rt.train_id, env.now)
        rt.status = TrainStatus.RUNNING
        pass_t = (res.radius * 2) / kmh_to_units_per_sec(max(1.0, rt.speed_kmh))
        yield env.timeout(pass_t)
        rt.s = min(route_length(rt.route_id), rt.s + res.radius)
        mr.on_exit(rec, env.now)
        mr.res.release(req)

    def _dwell(self, rt: TrainRuntime, stop_index: int):
        env = self.env
        route = route_by_id[rt.route_id]
        stop = route.stops[stop_index]
        rt.s = stop.s
        rt.next_stop_index = stop_index + 1
        pid = stop.platform_id
        req = None
        mr = self.resources.get(pid)
        if mr is not None:
            req = mr.res.request()
            rt.status = TrainStatus.ARRIVING
            yield req
            waited = 0.0
            while mr.blocked:
                rt.status = TrainStatus.WAITING
                rt.wait_end_t = None
                yield env.timeout(2.0)
                waited += 2.0
            gate = mr.gate_wait(env.now)
            if gate > 0:
                rt.status = TrainStatus.WAITING
                rt.wait_end_t = env.now + gate
                prev = mr.occupancy[-1].train_id if mr.occupancy else ""
                yield env.timeout(gate)
                waited += gate
                self._causal("PLATFORM_OCCUPANCY", prev or pid, rt.train_id, pid, gate)
            if waited > 0:
                rt.delays.add("platform_wait", waited)
            rec = mr.on_enter(rt.train_id, env.now)
        rt.status = TrainStatus.DWELLING
        rt.moving = False
        rt.dwell_end_t = env.now + stop.dwell_sec
        yield env.timeout(stop.dwell_sec)
        if mr is not None and req is not None:
            mr.on_exit(rec, env.now)
            mr.res.release(req)
        rt.status = TrainStatus.DEPARTED_STATION

    def _respawn(self, tid: str):
        yield self.env.timeout(settings.respawn_gap_sec)
        f = fleet_by_id[tid]
        route = route_by_id[f.route_id]
        base_s = crossing_distance(route.path, f.start, 80) or 0.0
        rt = self.trains[tid]
        rt.route_id = f.route_id
        rt.s = base_s
        rt.speed_kmh = f.nominal_speed_kmh
        rt.nominal_speed_kmh = f.nominal_speed_kmh
        rt.status = TrainStatus.RUNNING
        rt.finished = False
        rt.moving = False
        rt.next_stop_index = self._first_stop_index(f.route_id, base_s)
        rt.delays = DelayBuckets(base_schedule=f.entry_delay_min * 60.0)
        rt.pending_hold_sec = 0.0
        self.procs[tid] = self.env.process(self._train_process(tid))

    # ----------------------------------------------------------- causal
    def _causal(self, cause_type: str, cause_entity: str, affected: str,
                resource: str, seconds: float) -> None:
        if seconds <= 0:
            return
        self.causal_links.append(CausalLink(
            cause_type=cause_type, cause_entity=cause_entity, affected_train=affected,
            resource=resource, added_delay_seconds=seconds, timestamp=self.env.now))

    # ----------------------------------------------------------- advance
    def advance(self, dt: float) -> None:
        if dt <= 0:
            return
        target = self.env.now + dt
        self.env.run(until=target)
        now = self.env.now
        for rt in self.trains.values():
            if not rt.finished:
                rt.s = rt.sample_s(now)

    def seek(self, target_sim_time: float, step: float = 2.0) -> None:
        while self.env.now < target_sim_time - EPS:
            self.advance(min(step, target_sim_time - self.env.now))

    # ----------------------------------------------------------- actions
    def apply_action(self, action: AppliedAction) -> None:
        rt = self.trains.get(action.train_id)
        if rt is None or rt.finished:
            return
        if action.kind == "SPEED_REGULATION" and action.speed_kmh:
            rt.speed_kmh = max(5.0, action.speed_kmh)
            self._interrupt(action.train_id)
        elif action.kind == "HOLD" and action.hold_sec:
            rt.pending_hold_sec += action.hold_sec
            self._interrupt(action.train_id)
        elif action.kind in ("ALTERNATE_ROUTE", "PLATFORM_REASSIGNMENT") and action.route_id:
            new_route = route_by_id.get(action.route_id)
            if new_route:
                here = point_at(route_by_id[rt.route_id].path, rt.s)
                remapped = crossing_distance(new_route.path, here, 400)
                rt.route_id = action.route_id
                rt.s = remapped if remapped is not None else 0.0
                rt.next_stop_index = self._first_stop_index(action.route_id, rt.s)
                self._interrupt(action.train_id)
        self.applied_actions.append(action)

    def set_headway_multiplier(self, multiplier: float) -> None:
        """Apply a validated operational headway change to live resources."""
        value = max(0.5, min(4.0, float(multiplier)))
        self.headway_multiplier = value
        for resource in self.resources.values():
            resource.headway_multiplier = value

    def clear_resource(self, resource_id: str) -> None:
        if resource_id in self.resources:
            self.blocked_resources.discard(resource_id)
            self.resources[resource_id].blocked = False

    def _interrupt(self, tid: str) -> None:
        proc = self.procs.get(tid)
        if proc is not None and proc.is_alive:
            try:
                proc.interrupt()
            except RuntimeError:
                pass

    def inject_event(self, event: DelayEvent) -> None:
        event.timestamp = self.env.now
        self.events.append(event)
        if event.target_type == "TRAIN":
            rt = self.trains.get(event.target_id)
            if rt and not rt.finished:
                # An external delay: the train is held now, and the lost time is
                # attributed to the `event` cause (not a resource wait).
                rt.pending_event_hold += event.delay_seconds
                rt.pending_hold_sec += event.delay_seconds
                rt.delays.event += event.delay_seconds  # committed immediately
                self._interrupt(event.target_id)
                self._causal("EVENT", event.reason or event.event_id, rt.train_id,
                             event.target_id, event.delay_seconds)
        elif event.target_type in ("PLATFORM", "JUNCTION", "BLOCK", "TRACK"):
            self.blocked_resources.add(event.target_id)
            if event.target_id in self.resources:
                self.resources[event.target_id].blocked = True
        elif event.target_type == "SPEED":
            rt = self.trains.get(event.target_id)
            if rt:
                rt.speed_kmh = max(5.0, min(rt.speed_kmh, event.delay_seconds or rt.speed_kmh))

    # ----------------------------------------------------------- analytic view
    def analytic_state(self) -> "AnalyticState":
        from .predict import AnalyticState, AnalyticTrain
        now = self.env.now
        trains: dict[str, AnalyticTrain] = {}
        for tid, rt in self.trains.items():
            trains[tid] = AnalyticTrain(
                train_id=tid, route_id=rt.route_id, s=rt.sample_s(now),
                speed_kmh=rt.speed_kmh, nominal_speed_kmh=rt.nominal_speed_kmh,
                delay_sec=rt.delays.total, next_stop_index=rt.next_stop_index,
                dwell_remaining=rt.dwell_remaining(now), hold_remaining=rt.hold_remaining(now),
                finished=rt.finished, priority=rt.priority, ttype=rt.ttype,
                scheduled_departure_sec=rt.scheduled_departure_sec,
                activation_at_sec=rt.activation_at_sec,
                source=rt.source, provenance=rt.provenance,
            )
        return AnalyticState(
            sim_time=now, trains=trains,
            blocked_resources=set(self.blocked_resources),
            headway_multiplier=self.headway_multiplier,
            unavailable_routes=set(self.unavailable_routes),
        )
