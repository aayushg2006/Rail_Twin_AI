"""SimulationEngine - the authoritative SimPy discrete-event digital twin.

Trains are SimPy processes running real metric routes under real longitudinal
dynamics. Each acquires the junctions, block sections and platform roads it needs
before entering them, with the absolute-block headway enforced, so contention
produces real waiting and real lateness. Nothing is scripted.

Two things changed fundamentally from the pixel-space version:

* position is CHAINAGE IN METRES and movement follows an accelerate / cruise /
  brake profile, so run times, separations and speeds are physical quantities;
* the fleet is the 680-service booked timetable, admitted through a ROLLING
  WINDOW, so only the few dozen trains actually on the ground are simulated.

`analytic_state()` exposes a lightweight view for the fast prediction and
what-if layer in predict.py.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import simpy

from ..config import settings
from ..network.fleet import FleetEntry, active_window, fleet_by_id
from ..network.net import (STATION_LIMIT_M, corridors, lines,
                           resources as net_resources)
from ..network.routes import RailRoute, build_route, route_template
from ..network.scenarios import (ScenarioSetup, TrainOverride, matches,
                                 scenario_setup)
from .dynamics import build_profile
from .separation import safe_gap_m, safe_speed_ms
from .resources import ManagedResource, build_resources
from .state import (AppliedAction, CausalLink, DelayBuckets, RESOURCE_WAIT_BUCKET,
                    TrainRuntime, TrainStatus)

EPS = 1e-6
KMH = 1000.0 / 3600.0

# How much of the day the twin holds in memory around the current clock.
WINDOW_BEFORE_SEC = 1800.0
WINDOW_AFTER_SEC = 3600.0
ADMISSION_TICK_SEC = 30.0
# How often a running train re-reads the road in front. Signalling is
# continuous; checking only at resource boundaries let followers close right up.
SEPARATION_CHECK_SEC = 12.0


def _through_position(route, s: float) -> float:
    """Signed distance from the platform line along the direction of travel.

    Negative while approaching, zero at the platform, positive beyond. This is
    the only frame in which two trains on the same road can be compared, because
    the road runs continuously through the station while corridor chainage
    resets to zero there.
    """
    legs = route.path.legs
    if not legs:
        return s
    return s - legs[0].length_m



@dataclass
class DelayEvent:
    event_id: str
    target_type: str        # TRAIN | PLATFORM | BLOCK | JUNCTION | SPEED
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


class SimulationEngine:
    def __init__(self, scenario_id: str = "BASE", seed: int | None = None,
                 epoch_start_ms: int | None = None, clock_mode: str = "DEMO",
                 stochastic: bool = True, policy: str = "FIFO",
                 accept_actions: bool = True,
                 admission_cutoff_sec: float | None = None):
        self.scenario_id = scenario_id
        self.seed = settings.seed if seed is None else seed
        self.rng = random.Random(self.seed)
        self.stochastic = stochastic
        self.clock_mode = (clock_mode or "DEMO").upper()
        self.epoch_start_ms = epoch_start_ms or settings.demo_epoch_start_ms

        instant = datetime.fromtimestamp(self.epoch_start_ms / 1000, tz=ZoneInfo("Asia/Kolkata"))
        self.service_date = instant.date().isoformat()
        # Time of day (seconds since midnight IST) at simulation time zero.
        self.service_epoch_sec = instant.hour * 3600 + instant.minute * 60 + instant.second
        self.weekday_sun0 = (instant.weekday() + 1) % 7

        # FIFO = uncontrolled running. PRIORITY = the traditional "higher class
        # first" dispatching rule. Shadow twins use these as baselines; the live
        # twin runs FIFO plus whatever the controller accepts.
        self.policy = policy
        self.accept_actions = accept_actions
        # Relative simulation time after which new services are not admitted.
        # Live twins leave this unset; fixed-cohort benchmarks set it to the end
        # of the measurement window so the declared drain is a real drain.
        self.admission_cutoff_sec = admission_cutoff_sec
        self.setup: ScenarioSetup = scenario_setup(scenario_id)
        self.env = simpy.Environment()
        self.trains: dict[str, TrainRuntime] = {}
        self.completed_trains: dict[str, TrainRuntime] = {}
        self.routes: dict[str, RailRoute] = {}
        self.procs: dict[str, simpy.Process] = {}
        self.resources: dict[str, ManagedResource] = build_resources(
            self.env, self.setup.headway_multiplier,
            set(self.setup.blocked_resources), policy)
        self.blocked_resources: set[str] = set(self.setup.blocked_resources)
        self.unavailable_routes: set[str] = set(self.setup.unavailable_routes)
        self.headway_multiplier: float = self.setup.headway_multiplier
        self.applied_actions: list[AppliedAction] = []
        self.applied_action_ids: set[str] = set()
        self.causal_links: list[CausalLink] = []
        self.events: list[DelayEvent] = []
        # Live observations (train number -> lateness seconds) fed by ingestion.
        self.observed_delay_sec: dict[str, float] = {}
        self.observed_speed_kmh: dict[str, float] = {}
        self.observed_platform: dict[str, str] = {}
        self.observed_source: dict[str, str] = {}
        self.observed_at_epoch: dict[str, float] = {}

        self._resolve_dynamic_overrides()
        self._resolve_block_selector()
        self._admit_due()
        self.env.process(self._admission_loop())

    def _resolve_block_selector(self) -> None:
        """Resolve a selector-based resource block against the current window."""
        if self.setup.block_selector != "BUSIEST_PLATFORM":
            return
        window = active_window(self.service_seconds, 0.0, WINDOW_AFTER_SEC,
                               self.weekday_sun0)
        counts: dict[str, int] = {}
        for f in window:
            pid = f.route.platform_id
            if pid:
                counts[pid] = counts.get(pid, 0) + 1
        if not counts:
            return
        busiest = max(counts, key=lambda k: (counts[k], k))
        self.setup.blocked_resources = [busiest]
        self.blocked_resources = {busiest}
        if busiest in self.resources:
            self.resources[busiest].blocked = True

    def _resolve_dynamic_overrides(self) -> None:
        """Turn each scenario selector into concrete per-service overrides.

        Resolved against the services actually on the ground at the current
        clock, so a preset behaves the same way at any hour of the day.
        """
        if not self.setup.dynamic:
            return
        window = active_window(self.service_seconds, 0.0, WINDOW_AFTER_SEC,
                               self.weekday_sun0)
        window.sort(key=lambda f: f.booked_dep_sec)
        for rule in self.setup.dynamic:
            picked = [f for f in window
                      if matches(f, rule.match) and f.id not in self.setup.overrides]
            for i, f in enumerate(picked[:rule.count]):
                base = rule.override
                self.setup.overrides[f.id] = TrainOverride(
                    entry_delay_sec=base.entry_delay_sec + i * rule.stagger_sec,
                    speed_kmh=base.speed_kmh, platform_id=base.platform_id,
                )

    # ----------------------------------------------------------------- clock
    @property
    def now(self) -> float:
        return self.env.now

    @property
    def service_seconds(self) -> float:
        """Time of day, in seconds since midnight IST."""
        return self.service_epoch_sec + self.env.now

    # ------------------------------------------------------------- admission
    def _admission_loop(self):
        while True:
            yield self.env.timeout(ADMISSION_TICK_SEC)
            self._admit_due()
            self._retire_stale()

    def _admit_due(self) -> None:
        window = active_window(self.service_seconds, WINDOW_BEFORE_SEC,
                               WINDOW_AFTER_SEC, self.weekday_sun0)
        created = False
        for f in window:
            if f.id in self.trains:
                continue
            if (self.admission_cutoff_sec is not None
                    and f.entry_sec - self.service_epoch_sec >= self.admission_cutoff_sec):
                continue
            self._create(f)
            created = True
        if created:
            self._enforce_initial_separation()

    def _enforce_initial_separation(self) -> None:
        """Pull warm-started trains apart to a safe standing distance.

        A service admitted mid-day is placed where free running would have
        carried it, which takes no account of what is already on that road - two
        goods rakes were being planted 187 m apart. Nothing in the movement loop
        can undo that, because a train never reverses. So the placement itself
        has to respect the gap: followers are set back behind the movement in
        front, which is where the signalling would in fact have held them.
        """
        # Grouped by (running line, heading). Grouping by corridor split the road
        # at the station datum and hid pairs straddling it; grouping by line
        # alone put head-on movements on the bidirectional goods chord into the
        # same queue.
        by_road: dict[tuple[str, str], list[str]] = {}
        for tid, rt in self.trains.items():
            if rt.finished or not rt.admitted:
                continue
            if self.routes.get(tid) is None:
                continue
            route = self.routes[tid]
            by_road.setdefault(
                (route.line_at(rt.s), route.departure_corridor), []).append(tid)

        for group in by_road.values():
            if len(group) < 2:
                continue
            # Order along the direction of travel, leader first.
            group.sort(key=lambda t: _through_position(self.routes[t], self.trains[t].s),
                       reverse=True)
            for leader_id, follower_id in zip(group, group[1:]):
                leader, follower = self.trains[leader_id], self.trains[follower_id]
                f = fleet_by_id[follower_id]
                required = safe_gap_m(follower.speed_ms, f.traction, leader.service_class)
                gap = (_through_position(self.routes[leader_id], leader.s)
                       - _through_position(self.routes[follower_id], follower.s))
                if gap < required:
                    follower.s = max(0.0, follower.s - (required - gap))
                    follower.speed_ms = 0.0
                    follower.profile = None
                    route = self.routes.get(follower_id)
                    if route is not None:
                        follower.next_use_index = next(
                            (i for i, u in enumerate(route.uses) if u.enter_s > follower.s),
                            len(route.uses))

    def _retire_stale(self) -> None:
        cutoff = self.service_seconds - WINDOW_BEFORE_SEC - 600
        for tid in [t for t, rt in self.trains.items()
                    if rt.finished and (rt.booked_dep_sec < cutoff)]:
            self.completed_trains[tid] = self.trains[tid]
            self.trains.pop(tid, None)
            self.routes.pop(tid, None)
            self.procs.pop(tid, None)

    def _create(self, f: FleetEntry) -> None:
        override = self.setup.overrides.get(f.id) or self.setup.overrides.get(f.number)
        route = f.route
        if override and override.platform_id:
            route = route_template(f.arrival_corridor, f.departure_corridor,
                                   f.service_class, override.platform_id, f.dwell_sec)

        entry_at = f.entry_sec - self.service_epoch_sec
        # A service whose whole modelled run is already in the past never enters.
        if entry_at + f.free_run_sec < -60:
            return
        rt = TrainRuntime(
            train_id=f.id, route_id=route.id, s=0.0, speed_ms=f.line_speed_kmh * KMH,
            line_speed_kmh=f.line_speed_kmh, service_class=f.service_class,
            priority=f.priority, category=f.category,
            booked_dep_sec=f.booked_dep_sec, entry_at_sec=entry_at,
            source=f.source, provenance=f.provenance,
            status=TrainStatus.SCHEDULED,
        )
        # Lateness carried into the section: a live observation when we have one,
        # otherwise a draw from the observed distribution. Never a constant.
        rt.delays.entry = self._entry_lateness(f)
        if override:
            if override.entry_delay_sec:
                rt.delays.entry += override.entry_delay_sec
            if override.speed_kmh is not None:
                rt.regulated_kmh = override.speed_kmh

        if entry_at < 0:
            # Mid-day start: the service is already on the ground, so put it where
            # free running would have carried it rather than stacking every such
            # train on the section boundary.
            self._warm_start(rt, f, route, -entry_at)

        self.trains[f.id] = rt
        self.routes[f.id] = route
        self.procs[f.id] = self.env.process(self._train_process(f.id))

    def _warm_start(self, rt: TrainRuntime, f: FleetEntry, route: RailRoute,
                    elapsed: float) -> None:
        """Advance a runtime to where free running would have put it `elapsed`
        seconds after it entered, and skip the resources it has already passed."""
        traction = f.traction
        v_line = f.line_speed_kmh * KMH
        t = 0.0
        s_pos = 0.0
        v = v_line
        for index, use in enumerate(route.uses):
            is_platform = use.resource_id == route.platform_id
            if use.enter_s > s_pos:
                v_exit = 0.0 if is_platform else v_line
                leg = build_profile(use.enter_s - s_pos, v, v_line, v_exit, traction)
                if t + leg.duration >= elapsed:
                    covered, speed = leg.sample(elapsed - t)
                    rt.s, rt.speed_ms, rt.next_use_index = s_pos + covered, speed, index
                    rt.status = TrainStatus.RUNNING
                    rt.admitted = True
                    rt.entry_at_sec = 0.0
                    return
                t += leg.duration
                s_pos, v = use.enter_s, leg.v_exit
            if is_platform:
                dwell = route.stops[0].dwell_sec if route.stops else f.dwell_sec
                if t + dwell >= elapsed:
                    rt.s, rt.speed_ms, rt.next_use_index = s_pos, 0.0, index
                    rt.status = TrainStatus.DWELLING
                    rt.dwell_end_t = self.env.now + (t + dwell - elapsed)
                    rt.admitted = True
                    rt.entry_at_sec = 0.0
                    return
                t += dwell
                v = 0.0
                rt.departed_platform = True
                rt.actual_dep_sec = f.booked_dep_sec
            else:
                through = build_profile(max(0.0, use.exit_s - s_pos), v, v_line, v_line, traction)
                t += through.duration
                s_pos, v = max(s_pos, use.exit_s), through.v_exit
        # Past every resource: it is running out on the far leg.
        tail = build_profile(max(0.0, route.length_m - s_pos), v, v_line, v_line, traction)
        covered, speed = tail.sample(elapsed - t)
        rt.s = min(route.length_m, s_pos + covered)
        rt.speed_ms = speed
        rt.next_use_index = len(route.uses)
        rt.status = TrainStatus.RUNNING
        rt.admitted = True
        rt.entry_at_sec = 0.0

    def _entry_lateness(self, f: FleetEntry) -> float:
        observed = self.observed_delay_sec.get(f.number)
        if observed is not None:
            return max(0.0, observed)
        if not self.stochastic:
            return 0.0
        # Lognormal-ish: most services near time, a long thin tail of late ones.
        seed_key = (f"{self.seed}:{self.service_date}:{f.id}"
                    if f.is_freight else f"{self.seed}:{f.id}")
        rng = random.Random(seed_key)
        if rng.random() < 0.62:
            return 0.0
        return round(min(1800.0, rng.lognormvariate(4.6, 0.85)), 1)

    # ------------------------------------------------------------ dynamics
    def _limit_ms(self, rt: TrainRuntime, s: float) -> float:
        if rt.regulated_until_sec is not None and self.env.now >= rt.regulated_until_sec:
            rt.regulated_kmh = None
            rt.regulated_until_sec = None
            rt.regulation_expires_after_resource_id = None
        route = self.routes[rt.train_id]
        line = lines.get(route.line_at(s))
        limit = min(rt.line_speed_kmh, line.speed_limit_kmh if line else rt.line_speed_kmh)
        if rt.regulated_kmh is not None:
            limit = min(limit, rt.regulated_kmh)
        return max(5.0, limit) * KMH

    def _nominal_ms(self, rt: TrainRuntime, s: float) -> float:
        """Speed limit ignoring any controller regulation, for delay attribution."""
        route = self.routes[rt.train_id]
        line = lines.get(route.line_at(s))
        limit = min(rt.line_speed_kmh, line.speed_limit_kmh if line else rt.line_speed_kmh)
        return max(5.0, limit) * KMH

    def _train_ahead(self, rt: TrainRuntime, now: float) -> tuple[float, str] | None:
        """(gap in metres, service class) of the nearest movement in front on the
        same running line, or None when the road is clear.

        Positions are compared in THROUGH-CHAINAGE - signed distance from the
        platform line along the direction of travel, negative on the approach
        and positive beyond. Comparing raw corridor chainage instead meant a
        train on the NORTH approach could not see one just past the station on
        the DIVA side, even though they are on the same continuous road: a goods
        rake ran clear at 60 km/h right up to 187 m behind a stationary one.
        """
        route = self.routes.get(rt.train_id)
        if route is None:
            return None
        my_line = route.line_at(rt.s)
        my_heading = route.departure_corridor
        here = _through_position(route, rt.s)

        best: tuple[float, str] | None = None
        for other_id, other in self.trains.items():
            if other_id == rt.train_id or other.finished or not other.admitted:
                continue
            other_route = self.routes.get(other_id)
            if other_route is None:
                continue
            other_s = other.sample_s(now)
            # Same road AND the same way along it. The goods chord is worked in
            # both directions, so matching on the line alone treated a head-on
            # pair as a following pair. Opposing movements are kept apart by
            # block occupancy instead - they can never hold the same block.
            if (other_route.line_at(other_s) != my_line
                    or other_route.departure_corridor != my_heading):
                continue
            gap = _through_position(other_route, other_s) - here
            if gap <= 0:
                continue                 # behind us, or alongside
            if best is None or gap < best[0]:
                best = (gap, other.service_class)
        return best

    def _separation_limited_ms(self, rt: TrainRuntime, proposed_ms: float,
                               now: float) -> float:
        """Cap a proposed speed so the safe following distance is never broken.

        This is what stops two trains ever occupying the same piece of track.
        Block occupancy alone is not enough: with 1.3 km blocks, the train
        leaving one and the train entering the next can be almost touching.
        """
        ahead = self._train_ahead(rt, now)
        if ahead is None:
            return proposed_ms
        gap, ahead_class = ahead
        f = fleet_by_id[rt.train_id]
        permitted = safe_speed_ms(gap, f.traction, ahead_class)
        return max(0.0, min(proposed_ms, permitted))

    def _travel(self, rt: TrainRuntime, target_s: float, v_exit_ms: float):
        """Run to `target_s`, re-checking the road ahead as we go.

        Signalling is continuous: a driver reacts to the aspect in front at all
        times, not only when leaving the last one. Checking separation once per
        hop was not enough - a hop can be 1.3 km, and if the movement ahead
        stopped part-way through it the follower ran the whole way and closed to
        40 m. So the movement is stepped, and the road ahead is re-read on every
        step.
        """
        env = self.env
        f = fleet_by_id[rt.train_id]
        total = target_s - rt.s
        if total <= EPS:
            rt.s = max(rt.s, target_s)
            return

        nominal_limit = self._limit_ms(rt, rt.s)
        elapsed = 0.0
        while rt.s < target_s - EPS:
            remaining = target_s - rt.s
            limit = self._limit_ms(rt, rt.s)
            checked = False

            ahead = self._train_ahead(rt, env.now)
            step = remaining
            if ahead is not None:
                gap, ahead_class = ahead
                permitted = safe_speed_ms(gap, f.traction, ahead_class)
                if permitted < limit - 0.1:
                    checked = True
                    limit = max(permitted, 0.5)
                # Never move further than the room in front allows.
                room = max(0.0, gap - safe_gap_m(0.0, f.traction, ahead_class))
                step = min(step, room)
                if step <= 1.0:
                    # Standing at the signal behind the movement in front.
                    rt.status = TrainStatus.WAITING
                    rt.speed_ms = 0.0
                    rt.profile = None
                    yield env.timeout(SEPARATION_CHECK_SEC)
                    rt.delays.add("headway_wait", SEPARATION_CHECK_SEC)
                    elapsed += SEPARATION_CHECK_SEC
                    continue

            # Bound each step by time so the check is frequent regardless of speed.
            step = min(step, max(60.0, limit * SEPARATION_CHECK_SEC))
            last_step = step >= remaining - EPS
            exit_speed = min(v_exit_ms, limit) if last_step else limit

            profile = build_profile(step, rt.speed_ms, limit, exit_speed, f.traction)
            rt.profile = profile
            rt.profile_t0 = env.now
            rt.profile_s0 = rt.s
            rt.status = (TrainStatus.REGULATED
                         if (rt.regulated_kmh is not None or checked)
                         else TrainStatus.RUNNING)
            yield env.timeout(profile.duration)
            rt.s = min(target_s, rt.s + step)
            rt.speed_ms = profile.v_exit
            rt.profile = None
            elapsed += profile.duration

        if rt.regulated_kmh is not None:
            nominal = build_profile(total, nominal_limit, self._nominal_ms(rt, rt.s),
                                    v_exit_ms, f.traction)
            rt.delays.add("regulation", max(0.0, elapsed - nominal.duration))

    # ------------------------------------------------------- train process
    def _train_process(self, tid: str):
        env = self.env
        rt = self.trains[tid]
        route = self.routes[tid]
        f = fleet_by_id[tid]

        # A controller can act on a service that has not entered the section
        # yet - a conflict is predicted before either train arrives. Interrupting
        # this wait must not kill the process, or the train silently never runs.
        while rt.entry_at_sec > env.now:
            try:
                rt.status = TrainStatus.SCHEDULED
                yield env.timeout(rt.entry_at_sec - env.now)
            except simpy.Interrupt:
                continue
        # A plan may have re-platformed this service while it was still in the
        # admission wait.  That interrupt is consumed by the loop above, so the
        # process must explicitly refresh its local route before taking the
        # first resource.  Otherwise it continues over the withdrawn face even
        # though snapshots and predictions show the replacement route.
        route = self.routes[tid]
        rt.admitted = True
        rt.status = TrainStatus.APPROACHING

        while rt.next_use_index < len(route.uses):
            try:
                use = route.uses[rt.next_use_index]
                yield from self._consume_hold(rt, use.resource_id)
                is_platform = use.resource_id == route.platform_id
                v_exit = 0.0 if is_platform else self._limit_ms(rt, use.enter_s)
                if use.enter_s > rt.s + EPS:
                    yield from self._travel(rt, use.enter_s, v_exit)
                yield from self._pass_resource(rt, route, use, is_platform)
                rt.next_use_index += 1
            except simpy.Interrupt:
                # A controller action changed the plan; re-evaluate from here.
                rt.profile = None
                route = self.routes[tid]
                continue

        try:
            if route.length_m > rt.s + EPS:
                yield from self._travel(rt, route.length_m, self._limit_ms(rt, rt.s))
        except simpy.Interrupt:
            pass
        rt.finished = True
        rt.actual_exit_sec = self.service_epoch_sec + env.now
        rt.status = TrainStatus.COMPLETED
        rt.profile = None
        rt.speed_ms = 0.0

    def _consume_hold(self, rt: TrainRuntime, resource_id: str):
        if (rt.controller_hold_resource_id is not None
                and rt.controller_hold_resource_id != resource_id):
            return
        absolute = max(0.0, rt.controller_hold_until_sec - self.env.now)
        hold = absolute if rt.controller_hold_until_sec > 0 else rt.pending_hold_sec
        if hold <= 0:
            if rt.controller_hold_until_sec > 0:
                rt.pending_hold_sec = 0.0
                rt.controller_hold_until_sec = 0.0
                rt.controller_hold_resource_id = None
            return
        env = self.env
        event_part = min(hold, rt.pending_event_hold)
        rt.pending_hold_sec = 0.0
        rt.controller_hold_until_sec = 0.0
        rt.controller_hold_resource_id = None
        rt.pending_event_hold = max(0.0, rt.pending_event_hold - event_part)
        rt.status = TrainStatus.HELD
        rt.speed_ms = 0.0
        rt.profile = None
        rt.hold_end_t = env.now + hold
        # Credited up front so live KPIs reflect the decision immediately; the
        # event portion was already credited when the event was injected.
        rt.delays.add("hold", hold - event_part)
        yield env.timeout(hold)

    def _pass_resource(self, rt: TrainRuntime, route: RailRoute, use, is_platform: bool):
        env = self.env
        mr = self.resources.get(use.resource_id)
        if mr is None:
            return
        spec = net_resources[use.resource_id]
        bucket = RESOURCE_WAIT_BUCKET.get(spec.kind, "block_wait")

        req = mr.request(priority=rt.priority)
        granted = False
        rec = None
        rt.queued_resource_id = use.resource_id
        try:
            rt.status = TrainStatus.ARRIVING if is_platform else TrainStatus.WAITING
            yield req
            granted = True
            rt.queued_resource_id = None
            waited = 0.0
            while mr.blocked:
                rt.status = TrainStatus.WAITING
                rt.wait_end_t = None
                rt.speed_ms = 0.0
                yield env.timeout(2.0)
                waited += 2.0
            gate = mr.gate_wait(env.now)
            if gate > 0:
                rt.status = TrainStatus.WAITING
                rt.wait_end_t = env.now + gate
                rt.speed_ms = 0.0
                rt.profile = None
                prev = mr.snapshot_occupant(env.now) or (
                    mr.occupancy[-1].train_id if mr.occupancy else "")
                yield env.timeout(gate)
                waited += gate
                self._causal(f"{spec.kind}_OCCUPANCY", prev or use.resource_id,
                             rt.train_id, use.resource_id, gate)
            if waited > 0:
                rt.delays.add(bucket, waited)

            rec = mr.on_enter(rt.train_id, env.now)
            rt.current_resource_id = use.resource_id
            if is_platform:
                yield from self._dwell(rt, route)
            else:
                rt.status = TrainStatus.RUNNING
                yield from self._travel(rt, use.exit_s, self._limit_ms(rt, use.exit_s))
        finally:
            # SimPy does not automatically release a granted Resource request
            # when its process is interrupted.  Every exit path, including a
            # controller re-plan, must close occupancy and return the lease.
            rt.queued_resource_id = None
            rt.current_resource_id = None
            if rec is not None and rec.exit is None:
                mr.on_exit(rec, env.now)
            if granted:
                mr.res.release(req)
            else:
                req.cancel()
            if rt.regulation_expires_after_resource_id == use.resource_id:
                rt.regulated_kmh = None
                rt.regulated_until_sec = None
                rt.regulation_expires_after_resource_id = None

    def _dwell(self, rt: TrainRuntime, route: RailRoute):
        env = self.env
        f = fleet_by_id[rt.train_id]
        stop = route.stops[0] if route.stops else None
        booked = stop.dwell_sec if stop else f.dwell_sec
        dwell = booked
        if self.stochastic:
            # Real dwell overruns are one-sided: boarding can take longer than
            # booked, essentially never less.
            dwell_rng = random.Random(
                f"{self.seed}:{rt.train_id}:dwell:{rt.next_stop_index}")
            dwell = booked + max(
                0.0, dwell_rng.gauss(booked * 0.12, booked * 0.28))
        rt.status = TrainStatus.DWELLING
        rt.speed_ms = 0.0
        rt.profile = None
        rt.s = stop.s if stop else rt.s
        rt.dwell_end_t = env.now + dwell
        yield env.timeout(dwell)
        if dwell > booked:
            rt.delays.add("dwell", dwell - booked)
        rt.next_stop_index = 1
        rt.departed_platform = True
        rt.actual_dep_sec = self.service_epoch_sec + env.now
        rt.status = TrainStatus.DEPARTED

    # ----------------------------------------------------------- causal
    def _causal(self, cause_type: str, cause_entity: str, affected: str,
                resource: str, seconds: float) -> None:
        if seconds <= 0:
            return
        self.causal_links.append(CausalLink(
            cause_type=cause_type, cause_entity=cause_entity, affected_train=affected,
            resource=resource, added_delay_seconds=seconds, timestamp=self.env.now))
        if len(self.causal_links) > 400:
            del self.causal_links[:-400]

    # ----------------------------------------------------------- advance
    def advance(self, dt: float) -> None:
        if dt <= 0:
            return
        self.env.run(until=self.env.now + dt)
        now = self.env.now
        for rt in self.trains.values():
            if not rt.finished and rt.profile is not None:
                rt.s, rt.speed_ms = rt.sample(now)

    def seek(self, target_sim_time: float, step: float = 5.0) -> None:
        while self.env.now < target_sim_time - EPS:
            self.advance(min(step, target_sim_time - self.env.now))

    # ----------------------------------------------------------- actions
    def apply_action(self, action: AppliedAction) -> bool:
        # A shadow twin ignores controller actions by construction - that is
        # exactly what makes it a counterfactual.
        if not self.accept_actions:
            return False
        if action.action_id and action.action_id in self.applied_action_ids:
            return False
        rt = self.trains.get(action.train_id)
        if rt is None or rt.finished:
            return False
        if action.kind in {"HOLD", "PLATFORM_REASSIGNMENT", "ALTERNATE_ROUTE"}:
            if rt.current_resource_id is not None or rt.queued_resource_id is not None:
                return False
        applied = False
        if action.kind == "SPEED_REGULATION" and action.speed_kmh:
            rt.regulated_kmh = max(5.0, float(action.speed_kmh))
            rt.regulated_until_sec = action.expires_at_sec
            rt.regulation_expires_after_resource_id = action.expires_after_resource_id
            applied = True
        elif action.kind == "HOLD" and action.hold_sec:
            route = self.routes.get(rt.train_id)
            target = action.effective_resource_id
            if target and (route is None or not any(
                    use.resource_id == target
                    for use in route.uses[rt.next_use_index:])):
                return False
            release = (float(action.release_at_sec)
                       if action.release_at_sec is not None
                       else self.env.now + float(action.hold_sec))
            rt.controller_hold_until_sec = max(rt.controller_hold_until_sec, release)
            rt.pending_hold_sec = max(rt.pending_hold_sec, float(action.hold_sec))
            rt.controller_hold_resource_id = target
            applied = True
        elif action.kind == "PLATFORM_REASSIGNMENT" and action.platform_id:
            applied = self._reassign(rt, platform_id=action.platform_id)
        elif action.kind == "ALTERNATE_ROUTE" and action.route_id:
            applied = self._reassign(rt, line_hint=action.route_id)
        if not applied:
            return False
        action.lifecycle = "ACTIVE"
        self.applied_actions.append(action)
        if action.action_id:
            self.applied_action_ids.add(action.action_id)
        return True

    def _reassign(self, rt: TrainRuntime, platform_id: str | None = None,
                  line_hint: str | None = None) -> bool:
        """Re-route a train that has not yet reached the station throat.

        Chainage makes this safe: the new route is the same physical geometry
        with a different platform road, so the train keeps its position instead
        of being remapped onto a different-length polyline (which is what used
        to let a reroute finish *earlier* than the original).
        """
        f = fleet_by_id[rt.train_id]
        current = self.routes[rt.train_id]
        stop_s = current.stops[0].s if current.stops else None
        approach_turnouts = [
            use.enter_s for use in current.uses
            if (net_resources[use.resource_id].kind == "JUNCTION"
                and (stop_s is None or use.enter_s < stop_s))
        ]
        safe_control_s = max(approach_turnouts) if approach_turnouts else stop_s
        if safe_control_s is not None and rt.s >= safe_control_s - EPS:
            return False  # the approach route is already locked through the throat
        new_route = route_template(f.arrival_corridor, f.departure_corridor,
                                   f.service_class,
                                   platform_id or current.platform_id, f.dwell_sec)
        if new_route.id == current.id:
            return False
        self.routes[rt.train_id] = new_route
        rt.route_id = new_route.id
        # Position is chainage, so it is unchanged; only the resource list ahead
        # of the train changes. Re-point the cursor at the first unpassed use.
        rt.next_use_index = next(
            (i for i, u in enumerate(new_route.uses) if u.enter_s > rt.s), len(new_route.uses))
        self._interrupt(rt.train_id)
        return True

    def set_headway_multiplier(self, multiplier: float) -> None:
        value = max(0.5, min(4.0, float(multiplier)))
        self.headway_multiplier = value
        for resource in self.resources.values():
            resource.headway_multiplier = value

    def clear_resource(self, resource_id: str) -> None:
        if resource_id in self.resources:
            self.blocked_resources.discard(resource_id)
            self.resources[resource_id].blocked = False

    def _interrupt(self, tid: str) -> None:
        rt = self.trains.get(tid)
        if rt is not None and rt.profile is not None:
            rt.s, rt.speed_ms = rt.sample(self.env.now)
            rt.profile = None
        proc = self.procs.get(tid)
        if proc is not None and proc.is_alive:
            try:
                proc.interrupt()
            except RuntimeError:
                pass

    def observe_speed(self, number: str, kmh: float) -> None:
        """Adopt an observed section speed as this service's line speed.

        RailRadar reports `speedToNextStationKmph` per stop - what the train is
        actually doing on the ground, rather than what the class is booked for.
        """
        if kmh <= 0:
            return
        self.observed_speed_kmh[number] = kmh
        for tid, rt in self.trains.items():
            f = fleet_by_id.get(tid)
            if f and f.number == number and not rt.finished:
                rt.line_speed_kmh = max(15.0, min(rt.line_speed_kmh * 1.5, kmh))

    def observe_platform(self, number: str, platform: str) -> None:
        """Adopt the platform the train is actually being worked into."""
        face = platform if platform.startswith("PF") else f"PF{platform}"
        self.observed_platform[number] = face
        for tid, rt in self.trains.items():
            f = fleet_by_id.get(tid)
            if f and f.number == number and not rt.finished and not rt.departed_platform:
                route = self.routes.get(tid)
                if route is not None and route.platform_id != face:
                    self._reassign(rt, platform_id=face)

    def observe_location(self, number: str, station_code: str) -> None:
        """Anchor an admitted train to a detailed RailRadar station report."""
        code = station_code.strip().upper()
        candidates = [(corridor_id, chainage)
                      for corridor_id, corridor in corridors.items()
                      for station, _name, chainage in corridor.stations
                      if station.upper() == code]
        if code == "BSR":
            candidates.extend((corridor_id, 0.0) for corridor_id in corridors)
        if not candidates:
            return
        for tid, rt in self.trains.items():
            entry = fleet_by_id.get(tid)
            route = self.routes.get(tid)
            if (entry is None or entry.number != number or route is None
                    or rt.finished or not rt.admitted
                    or rt.current_resource_id or rt.queued_resource_id):
                continue
            positions = [route.path.s_at(corridor_id, chainage)
                         for corridor_id, chainage in candidates]
            positions = [position for position in positions if position is not None]
            if not positions:
                continue
            here = rt.sample_s(self.env.now)
            anchored = min(positions, key=lambda position: abs(position - here))
            self._interrupt(tid)
            rt.s = max(0.0, min(route.length_m, anchored))
            rt.next_use_index = next(
                (index for index, use in enumerate(route.uses)
                 if use.exit_s > rt.s + EPS), len(route.uses))

    def observe(self, number: str, lateness_sec: float) -> None:
        """Record a live lateness observation for a train number."""
        self.observed_delay_sec[number] = lateness_sec
        for rt in self.trains.values():
            f = fleet_by_id.get(rt.train_id)
            if f and f.number == number and not rt.finished:
                # Live observations are anchors, not merely initial conditions.
                # Correct the remaining entry-delay bucket for an admitted
                # service without erasing delay already attributed to queues,
                # dwell, events or controller actions.
                other = rt.delays.total - rt.delays.entry
                rt.delays.entry = max(0.0, lateness_sec - other)

    def observe_metadata(self, number: str, source: str,
                         observed_at: str | None = None) -> None:
        self.observed_source[number] = source
        if observed_at:
            try:
                self.observed_at_epoch[number] = datetime.fromisoformat(
                    observed_at.replace("Z", "+00:00")).timestamp()
            except ValueError:
                pass

    def inject_event(self, event: DelayEvent) -> None:
        event.timestamp = self.env.now
        self.events.append(event)
        if event.target_type == "TRAIN":
            rt = self.trains.get(event.target_id)
            if rt and not rt.finished:
                rt.pending_event_hold += event.delay_seconds
                rt.pending_hold_sec += event.delay_seconds
                rt.delays.event += event.delay_seconds
                self._interrupt(event.target_id)
                self._causal("EVENT", event.reason or event.event_id, rt.train_id,
                             event.target_id, event.delay_seconds)
        elif event.target_type in ("PLATFORM", "JUNCTION", "BLOCK"):
            self.blocked_resources.add(event.target_id)
            if event.target_id in self.resources:
                self.resources[event.target_id].blocked = True
        elif event.target_type == "SPEED":
            rt = self.trains.get(event.target_id)
            if rt:
                rt.regulated_kmh = max(5.0, event.delay_seconds or rt.line_speed_kmh)
                self._interrupt(event.target_id)

    # ----------------------------------------------------- analytic view
    def analytic_state(self) -> "AnalyticState":
        from .predict import AnalyticResource, AnalyticState, AnalyticTrain
        now = self.env.now
        trains: dict[str, AnalyticTrain] = {}
        for tid, rt in self.trains.items():
            if rt.finished:
                continue
            s, speed = rt.sample(now)
            entry = fleet_by_id.get(tid)
            number = entry.number if entry else tid
            trains[tid] = AnalyticTrain(
                train_id=tid, route_id=rt.route_id, s=s, speed_ms=speed,
                line_speed_kmh=rt.line_speed_kmh, regulated_kmh=rt.regulated_kmh,
                lateness_sec=rt.lateness_sec(now, self.service_epoch_sec),
                next_use_index=rt.next_use_index,
                dwell_remaining=rt.dwell_remaining(now),
                # A hold the controller has just accepted has not been consumed
                # yet - it bites when the train next reaches a signal. The
                # projection must still account for it, or an accepted decision
                # looks as though it changed nothing.
                hold_remaining=rt.hold_remaining(now),
                finished=rt.finished, admitted=rt.admitted, priority=rt.priority,
                category=rt.category, service_class=rt.service_class,
                booked_dep_sec=rt.booked_dep_sec, entry_at_sec=rt.entry_at_sec,
                departed_platform=rt.departed_platform,
                source=rt.source, provenance=rt.provenance,
                current_resource_id=rt.current_resource_id,
                queued_resource_id=rt.queued_resource_id,
                scheduled_hold_sec=rt.pending_hold_sec,
                scheduled_hold_resource_id=rt.controller_hold_resource_id,
                scheduled_release_at_sec=(rt.controller_hold_until_sec
                                          if rt.controller_hold_until_sec > 0 else None),
                data_source=(
                    "SYNTHETIC_FREIGHT" if rt.category in ("FREIGHT", "SHUNT")
                    else ("RAILRADAR_REPLAY" if self.observed_source.get(
                        number, "") in ("replay", "cache")
                          else "RAILRADAR_LIVE" if number
                          in self.observed_source else "TIMETABLE_ESTIMATE")),
                observed_at_epoch=self.observed_at_epoch.get(number),
            )
        resource_state = {}
        for rid, mr in self.resources.items():
            queued = tuple(
                tid for tid, rt in self.trains.items()
                if rt.queued_resource_id == rid and not rt.finished)
            resource_state[rid] = AnalyticResource(
                resource_id=rid,
                free_at_sec=max(0.0, mr.free_at - now),
                owner=mr.snapshot_occupant(now), queued=queued,
                blocked=mr.blocked,
            )
        return AnalyticState(
            sim_time=now, service_epoch_sec=self.service_epoch_sec, trains=trains,
            routes={tid: self.routes[tid] for tid in trains if tid in self.routes},
            blocked_resources=set(self.blocked_resources),
            headway_multiplier=self.headway_multiplier,
            unavailable_routes=set(self.unavailable_routes),
            resources=resource_state,
        )
