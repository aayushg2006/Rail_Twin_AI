"""SimulationOrchestrator.

Owns the live twin, advances it on an independent clock, assembles the snapshot
bundle every tick and broadcasts it to WebSocket clients.

It also runs two SHADOW twins on the same seed, scenario and disruptions, which
is how "delay avoided" is now measured:

    DO_NOTHING   no controller action ever applied - the section left to run
                 itself, first come first served at every signal
    PRIORITY     the traditional dispatching rule: always let the higher class
                 through, regardless of what it costs the network
    LIVE         the AI-assisted twin the controller is actually working

Delay avoided is LIVE measured against those, continuously. The previous version
reported the difference between the best and the WORST option the optimiser had
generated for itself, which flattered the result and was not a counterfactual at
all.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Any, Callable

from ..config import settings
from ..domain import dto
from ..twin.engine import DelayEvent, SimulationEngine
from ..twin.metrics import compute_kpis
from ..twin.predict import apply_action, predict, project_state_at
from ..twin.state import AppliedAction
from ..optimize.safety import validate
from ..network.net import network_pack, timetable_pack, freight_pack

VALID_SPEEDS = {1, 2, 5, 10, 20}
SHADOW_EVERY_N_TICKS = 8          # shadows only feed KPI trends, not the map
TREND_POINTS = 180

logger = logging.getLogger("railtwin.orchestrator")


class SimulationOrchestrator:
    def __init__(self, scenario_id: str = "BASE"):
        self.clock_mode = settings.clock_mode.upper()
        self.playing = True
        self.speed = settings.default_speed
        self.horizon = settings.default_horizon_sec
        self.horizon_offset = 0.0
        self._clients: set[Any] = set()
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._tick_count = 0
        self._tick_failures = 0
        self._tick_error = ""
        self._cached_prediction = None
        self._cached_kpis = None
        self._cached_options: dict = {}
        self._projected = None
        self._shadow_kpis: dict = {}
        self._trend: list[dict] = []
        self.decision_hook: Callable[[dict], None] | None = None
        self.options_provider: Callable[..., dict] | None = None
        self.ml_provider: Callable[..., dict] | None = None
        self.model_status: dict = {
            "optimizer": {"status": "UNAVAILABLE", "reason": "Optimizer still initialising"},
            "ml": {"status": "DETERMINISTIC_FALLBACK", "reason": "Trained artifacts not loaded"},
        }
        self._suggestion_revision = 0
        self._suggestion_fingerprint: tuple = ()
        self._suggestion_generated_at = 0.0
        self._last_decision_status: dict = {"status": "READY"}
        self.decisions: list[dict] = []
        self.persistence_status = "IN_MEMORY"
        self.scenario_store = None
        self.ingest = None
        self._build(scenario_id)

    # ------------------------------------------------------------- lifecycle
    def _epoch(self) -> int:
        return (settings.demo_epoch_start_ms if self.clock_mode == "DEMO"
                else int(time.time() * 1000))

    def _build(self, scenario_id: str) -> None:
        epoch = self._epoch()
        common = dict(seed=settings.seed, epoch_start_ms=epoch, clock_mode=self.clock_mode)
        self.engine = SimulationEngine(scenario_id, **common)
        # Same seed, same scenario, same disruptions - the only difference is
        # that neither shadow ever receives a controller decision.
        self.shadow_nothing = SimulationEngine(
            scenario_id, **common, policy="FIFO", accept_actions=False)
        self.shadow_priority = SimulationEngine(
            scenario_id, **common, policy="PRIORITY", accept_actions=False)
        self.decisions = []
        self._trend = []
        self.playing = True
        self._refresh_derived()
        self._refresh_shadows()

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None

    async def _run_loop(self) -> None:
        """The simulation clock.

        One bad tick must never stop the twin. Previously any exception in here
        killed the task silently while /api/health went on reporting
        `playing: true`, so the console looked alive with a frozen clock.
        """
        interval = settings.tick_seconds
        while True:
            t0 = time.perf_counter()
            bundle: dict | None = None
            try:
                async with self._lock:
                    if self.playing:
                        dt = self.speed * interval
                        self.engine.advance(dt)
                        self.shadow_nothing.advance(dt)
                        self.shadow_priority.advance(dt)
                    self._tick_count += 1
                    if self._tick_count % 2 == 0:
                        self._refresh_derived()
                    if self._tick_count % SHADOW_EVERY_N_TICKS == 0:
                        self._refresh_shadows()
                    bundle = self._build_bundle()
                self._tick_error = ""
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._tick_error = f"{type(exc).__name__}: {exc}"
                self._tick_failures += 1
                logger.exception("simulation tick failed (%d so far)", self._tick_failures)
            if bundle is not None:
                await self._broadcast(bundle)
            await asyncio.sleep(max(0.0, interval - (time.perf_counter() - t0)))

    # --------------------------------------------------------------- derived
    def _refresh_derived(self) -> None:
        self._suggestion_generated_at = self.engine.now
        astate = self.engine.analytic_state()
        self._cached_prediction = predict(astate, self.horizon)
        self._cached_kpis = compute_kpis(self.engine, astate, self._cached_prediction)
        # A scrubbed view is the same twin projected forward, not a second model.
        self._projected = (project_state_at(astate, self.horizon_offset)
                           if self.horizon_offset > 0 else None)
        opts: dict = {}
        if self.options_provider is not None:
            try:
                opts.update(self.options_provider(self.engine, self._cached_prediction))
            except Exception as exc:
                opts["optionsError"] = str(exc)
        if self.ml_provider is not None:
            try:
                opts.update(self.ml_provider(self.engine, self._cached_prediction))
            except Exception as exc:
                opts["mlError"] = str(exc)
        self._cached_options = opts

        # The revision exists so a controller cannot accept a recommendation
        # that has since been superseded. It must therefore change only when the
        # ADVICE changes - which conflicts are open and what is recommended for
        # them. Bumping it on every recompute (4 Hz) meant no human click could
        # ever be fresh, and every decision was silently dropped as stale.
        fingerprint = tuple(
            (cid, (rec or {}).get("optionId"))
            for cid, rec in sorted((opts.get("recommendationByConflict") or {}).items())
        )
        if fingerprint != self._suggestion_fingerprint:
            self._suggestion_fingerprint = fingerprint
            self._suggestion_revision += 1

    def _shadow_kpi(self, engine: SimulationEngine) -> dict:
        astate = engine.analytic_state()
        return dto.kpis_dict(compute_kpis(engine, astate, predict(astate, self.horizon)))

    def _refresh_shadows(self) -> None:
        self._shadow_kpis = {
            "doNothing": self._shadow_kpi(self.shadow_nothing),
            "priorityRule": self._shadow_kpi(self.shadow_priority),
        }
        live = dto.kpis_dict(self._cached_kpis) if self._cached_kpis else {}
        self._trend.append({
            "simTimeSec": round(self.engine.now, 1),
            "serviceSeconds": round(self.engine.service_seconds, 1),
            "ai": live.get("totalLatenessSec", 0.0),
            "doNothing": self._shadow_kpis["doNothing"].get("totalLatenessSec", 0.0),
            "priorityRule": self._shadow_kpis["priorityRule"].get("totalLatenessSec", 0.0),
            "aiPassengerMinutes": live.get("passengerMinutes", 0.0),
            "doNothingPassengerMinutes": self._shadow_kpis["doNothing"].get("passengerMinutes", 0.0),
        })
        if len(self._trend) > TREND_POINTS:
            del self._trend[:-TREND_POINTS]

    def _delay_avoided(self) -> dict:
        """Measured, not inferred: the shadow twins have run the same disruptions
        without ever taking a controller decision."""
        live = dto.kpis_dict(self._cached_kpis) if self._cached_kpis else {}
        nothing = self._shadow_kpis.get("doNothing", {})
        priority = self._shadow_kpis.get("priorityRule", {})
        applied = sum(1 for d in self.decisions if d.get("outcome") != "REJECTED")
        # A hold costs its time the moment it is issued, but only repays it when
        # the conflict it prevents would have bitten. While one is still being
        # served the comparison is mid-flight, and the console must say so
        # instead of showing a bare negative number that reads as a regression.
        in_flight = sum(
            rt.pending_hold_sec + rt.hold_remaining(self.engine.now)
            for rt in self.engine.trains.values() if not rt.finished
        )
        return {
            "decisionsApplied": applied,
            "decisionsRejected": len(self.decisions) - applied,
            "holdInFlightSec": round(in_flight, 1),
            "settling": in_flight > 1.0,
            "vsDoNothingSec": round(nothing.get("totalLatenessSec", 0.0)
                                    - live.get("totalLatenessSec", 0.0), 1),
            "vsPriorityRuleSec": round(priority.get("totalLatenessSec", 0.0)
                                       - live.get("totalLatenessSec", 0.0), 1),
            "vsDoNothingPassengerMinutes": round(
                nothing.get("passengerMinutes", 0.0) - live.get("passengerMinutes", 0.0), 1),
            "measured": bool(self._shadow_kpis),
        }

    def _build_bundle(self) -> dict:
        pred = self._cached_prediction
        kpis_map = dto.kpis_dict(self._cached_kpis)
        wall_ms = int(self.engine.epoch_start_ms + self.engine.now * 1000)
        bundle: dict = {
            "type": "snapshot",
            "connectionStatus": "CONNECTED",
            "playing": self.playing,
            "speed": self.speed,
            "simState": dto.sim_state_dict(self.engine),
            "horizonOffsetSec": self.horizon_offset,
            "projected": (dto.projected_dict(self.engine, self._projected)
                          if self._projected else None),
            "prediction": dto.prediction_dict(pred),
            "kpis": kpis_map,
            "baselines": {**self._shadow_kpis, "ai": kpis_map},
            "delayAvoided": self._delay_avoided(),
            "trend": self._trend[-90:],
            "decisions": self.decisions[-60:],
            "causalChain": dto.causal_chain_list(self.engine),
            "delayBuckets": dto.delay_buckets_map(self.engine),
            "dataPackId": network_pack["id"],
            "provenance": {
                "network": network_pack["id"],
                "timetable": timetable_pack["id"],
                "timetableServices": len(timetable_pack["services"]),
                "freight": freight_pack["id"],
                "freightPaths": len(freight_pack["paths"]),
                "freightNote": freight_pack["provenanceNote"],
                "timetableCoverage": [s.get("coverage") for s in timetable_pack["sources"]],
            },
            "clockMode": self.clock_mode,
            "wallClockMs": wall_ms,
            "serviceSeconds": round(self.engine.service_seconds, 1),
            "serviceDate": datetime.fromtimestamp(
                wall_ms / 1000, tz=ZoneInfo("Asia/Kolkata")).date().isoformat(),
            "persistenceStatus": self.persistence_status,
            "tickFailures": self._tick_failures,
            "tickError": self._tick_error,
            "suggestionRevision": self._suggestion_revision,
            "suggestionGeneratedAt": self._suggestion_generated_at,
            "lastDecisionStatus": self._last_decision_status,
            "modelStatus": self.model_status,
            "scenario": self.engine.scenario_id,
            "liveData": self._live_data_status(),
        }
        bundle.update(self._cached_options)
        return bundle

    def _live_data_status(self) -> dict:
        """What the console must say about where its numbers came from."""
        ingest = getattr(self, "ingest", None)
        observations = getattr(ingest, "observations", {}) if ingest else {}
        return {
            "mode": getattr(getattr(ingest, "client", None), "mode", "off"),
            "enabled": bool(ingest and ingest.enabled),
            "observedTrains": len(observations),
            "observations": [o.as_dict() for o in observations.values()][:12],
            "lastPollAt": getattr(ingest, "last_poll_at", None),
            "freightNote": "Goods movements are synthetic - no public live feed exists.",
        }

    # --------------------------------------------------------------- clients
    def add_client(self, ws: Any) -> None:
        self._clients.add(ws)

    def remove_client(self, ws: Any) -> None:
        self._clients.discard(ws)

    async def _broadcast(self, bundle: dict) -> None:
        dead = []
        for ws in list(self._clients):
            try:
                await ws.send_json(bundle)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._clients.discard(ws)

    async def snapshot_now(self, ws: Any) -> None:
        async with self._lock:
            bundle = self._build_bundle()
        await ws.send_json(bundle)

    # -------------------------------------------------------------- commands
    async def handle_command(self, msg: dict) -> None:
        cmd = msg.get("cmd")
        async with self._lock:
            if cmd in ("pause",):
                self.playing = False
            elif cmd in ("resume", "play"):
                self.playing = True
            elif cmd == "set_speed":
                sp = int(msg.get("speed", self.speed))
                self.speed = 1 if self.clock_mode == "LIVE" else (
                    sp if sp in VALID_SPEEDS else self.speed)
            elif cmd == "set_clock_mode":
                self._set_clock_mode(str(msg.get("mode", "LIVE")).upper())
            elif cmd == "seek":
                target = float(msg.get("simTimeSec", self.engine.now))
                self.playing = False
                delta = target - self.engine.now
                if delta > 0:
                    self.engine.seek(target)
                    self.shadow_nothing.seek(target)
                    self.shadow_priority.seek(target)
                self._refresh_derived()
                self._refresh_shadows()
            elif cmd == "set_horizon":
                self.horizon = int(msg.get("horizonSec", self.horizon))
                self._refresh_derived()
            elif cmd == "set_horizon_offset":
                # How far ahead the console is looking. The projection is done
                # HERE, by the authoritative twin - the console used to run its
                # own model for scrubbed frames, which could disagree with the
                # numbers printed beside them.
                self.horizon_offset = max(0.0, min(900.0, float(msg.get("offsetSec", 0))))
                self._refresh_derived()
            elif cmd == "inject_event":
                # Each engine needs its own event object; they mutate timestamps.
                for eng in (self.engine, self.shadow_nothing, self.shadow_priority):
                    eng.inject_event(_event(msg.get("event", {})))
                self._refresh_derived()
            elif cmd == "load_scenario":
                self._build(str(msg.get("scenario", "BASE")))
            elif cmd == "reset":
                self._build(self.engine.scenario_id)
            elif cmd == "decide":
                self._decide(msg)
            elif cmd == "observe":
                for eng in (self.engine, self.shadow_nothing, self.shadow_priority):
                    eng.observe(str(msg.get("number", "")), float(msg.get("latenessSec", 0)))

    def _set_clock_mode(self, mode: str) -> None:
        if mode not in ("LIVE", "DEMO") or mode == self.clock_mode:
            return
        self.clock_mode = mode
        self.speed = 1 if mode == "LIVE" else settings.default_speed
        self._build(self.engine.scenario_id)

    # -------------------------------------------------------------- decisions
    def _decide(self, msg: dict) -> None:
        outcome = msg.get("outcome", "REJECTED")
        action = _action(msg.get("action", {}))
        conflict_id = msg.get("conflictId")
        expected = msg.get("expectedRevision")

        stale = expected is not None and int(expected) != self._suggestion_revision
        if stale and outcome != "REJECTED":
            # The advice moved on while the controller was reading it. That is
            # only fatal if the conflict itself is gone; otherwise the action is
            # re-validated against live state below and applied on its merits.
            still_open = any(
                c.id == conflict_id
                for c in (self._cached_prediction.conflicts if self._cached_prediction else []))
            if not still_open:
                self._last_decision_status = {
                    "status": "STALE",
                    "reason": "That conflict was resolved before the command was sent"}
                return

        current = next((c for c in (self._cached_prediction.conflicts
                                    if self._cached_prediction else [])
                        if c.id == conflict_id), None)
        record = {
            "id": f"D-{int(time.time() * 1000)}",
            "simTimeSec": round(self.engine.now, 1),
            "serviceSeconds": round(self.engine.service_seconds, 1),
            "conflictId": conflict_id,
            "where": current.resource_label if current else "",
            "trains": [t for t in ((current.train_a, current.train_b) if current else ()) if t],
            "action": action.as_dict(),
            "optionTitle": msg.get("optionTitle", ""),
            "note": msg.get("note", ""),
            "outcome": outcome,
        }

        if outcome == "REJECTED":
            self._last_decision_status = {"status": "REJECTED",
                                          "reason": msg.get("note") or "Rejected by controller"}
            record["reason"] = self._last_decision_status["reason"]
            self._commit(record)
            return

        if current is None:
            self._last_decision_status = {"status": "REJECTED",
                                          "reason": "That conflict is no longer predicted"}
            record.update(outcome="REJECTED", reason=self._last_decision_status["reason"])
            self._commit(record)
            return

        if action.kind not in {"SPEED_REGULATION", "HOLD", "PLATFORM_REASSIGNMENT",
                               "ALTERNATE_ROUTE"}:
            self._last_decision_status = {"status": "REJECTED",
                                          "reason": "Unsupported action"}
            record.update(outcome="REJECTED", reason=self._last_decision_status["reason"])
            self._commit(record)
            return

        # Re-validate against the CURRENT state, not the state the option was
        # generated in - the recommendation may be seconds old.
        before = self.engine.analytic_state()
        after = apply_action(before, action)
        projected = predict(after, self.horizon)
        residual = any(
            c.severity == "CRITICAL" and c.resource_id == current.resource_id
            and {c.train_a, c.train_b} & {current.train_a, current.train_b, action.train_id}
            for c in projected.conflicts)
        mode = "CONTAINMENT" if msg.get("responseMode") == "CONTAINMENT" else "RESOLUTION"
        safety = validate(action, after, current, not residual, mode)
        record["safety"] = safety
        if not safety.get("passed"):
            self._last_decision_status = {"status": "REJECTED",
                                          "reason": "Failed safety re-validation"}
            record.update(outcome="REJECTED", reason=self._last_decision_status["reason"])
            self._commit(record)
            return

        lateness_before = dto.kpis_dict(self._cached_kpis).get("totalLatenessSec", 0.0)
        self.engine.apply_action(action)
        self._refresh_derived()
        record["latenessBeforeSec"] = lateness_before
        record["latenessAfterSec"] = dto.kpis_dict(self._cached_kpis).get("totalLatenessSec", 0.0)
        record["conflictCleared"] = not any(
            c.id == conflict_id for c in self._cached_prediction.conflicts)
        self._last_decision_status = {"status": outcome,
                                      "reason": "Applied after live safety re-validation"}
        self._commit(record)

    def _commit(self, record: dict) -> None:
        self.decisions.append(record)
        if self.decision_hook:
            try:
                self.decision_hook(record)
            except Exception:
                pass


def _action(d: dict) -> AppliedAction:
    return AppliedAction(
        kind=d.get("kind", "NO_ACTION"), train_id=d.get("trainId", ""),
        speed_kmh=d.get("speedKmh"), hold_sec=d.get("holdSec"),
        route_id=d.get("routeId"), platform_id=d.get("platformId"))


def _event(d: dict) -> DelayEvent:
    return DelayEvent(
        event_id=d.get("eventId", "EV"),
        target_type=d.get("targetType", "TRAIN"),
        target_id=d.get("targetId", ""),
        delay_seconds=float(d.get("delaySeconds", 0) or 0),
        reason=d.get("reason", ""), severity=d.get("severity", "MEDIUM"),
        scenario_id=d.get("scenarioId", ""))
