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
from ..optimize.candidates import Candidate
from ..optimize.whatif import delay_profile, evaluate, evaluate_do_nothing
from ..network.net import network_pack, timetable_pack, freight_pack

VALID_SPEEDS = {1, 2, 5, 10, 20}
SHADOW_EVERY_N_TICKS = 8          # shadows only feed KPI trends, not the map
TREND_POINTS = 180

logger = logging.getLogger("railtwin.orchestrator")


class SimulationOrchestrator:
    def __init__(self, scenario_id: str = "BASE"):
        self.live_locked = (
            settings.railradar_mode.lower() == "live"
            and bool(settings.railradar_api_key)
        )
        self.clock_mode = "LIVE" if self.live_locked else settings.clock_mode.upper()
        self.playing = True
        self.speed = 1 if self.clock_mode == "LIVE" else settings.default_speed
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
        self._comparable_ai: dict = {}
        self._common_count = 0
        self._lateness_budget_breaches = 0
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
        self._last_validation: dict | None = None
        self._conflict_seen: dict[str, tuple[float, float]] = {}
        # Conflicts that already have a command against them. Re-issuing one
        # stacks a second hold on a train that is already being held.
        self._actioned: set[str] = set()
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
        self._actioned = set()
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
        active_ids = set()
        for conflict in self._cached_prediction.conflicts:
            active_ids.add(conflict.episode_id or conflict.id)
            key = conflict.episode_id or conflict.id
            first, _ = self._conflict_seen.get(key, (self.engine.now, self.engine.now))
            self._conflict_seen[key] = (first, self.engine.now)
            conflict.first_seen_sec = first
            conflict.last_seen_sec = self.engine.now
        self._conflict_seen = {
            key: value for key, value in self._conflict_seen.items()
            if key in active_ids or self.engine.now - value[1] <= self.horizon
        }
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

    def _shadow_kpi(self, engine: SimulationEngine, common: set[str] | None) -> dict:
        astate = engine.analytic_state()
        return dto.kpis_dict(
            compute_kpis(engine, astate, predict(astate, self.horizon), common))

    def _common_trains(self) -> set[str]:
        """Services admitted and running in ALL THREE tracks right now.

        Comparing tracks over different train sets is meaningless - that is why
        "average lateness" could show the AI worse while it was in fact ahead.
        """
        sets = []
        for engine in (self.engine, self.shadow_nothing, self.shadow_priority):
            sets.append({tid for tid, rt in engine.trains.items()
                         if rt.admitted and not rt.finished})
        return set.intersection(*sets) if sets else set()

    def _refresh_shadows(self) -> None:
        common = self._common_trains()
        self._common_count = len(common)
        self._shadow_kpis = {
            "doNothing": self._shadow_kpi(self.shadow_nothing, common),
            "priorityRule": self._shadow_kpi(self.shadow_priority, common),
        }
        astate = self.engine.analytic_state()
        live = dto.kpis_dict(compute_kpis(
            self.engine, astate, self._cached_prediction or predict(astate, self.horizon),
            common))
        self._comparable_ai = live
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
            "budgetBreachesBlocked": self._lateness_budget_breaches,
            "comparableTrains": self._common_count,
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
            "baselines": {**self._shadow_kpis,
                          "ai": self._comparable_ai or kpis_map,
                          "comparableTrains": self._common_count},
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
            "proposedValidation": self._last_validation,
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
        feed_state = ingest.feed_state() if ingest else "OFF"
        last_live_at = getattr(ingest, "last_live_at", None)
        age = max(0.0, time.time() - last_live_at) if last_live_at else None
        return {
            "mode": getattr(getattr(ingest, "client", None), "mode", "off"),
            "sourceState": feed_state,
            "enabled": bool(ingest and ingest.enabled),
            "observedTrains": len(observations),
            "observations": [o.as_dict() for o in observations.values()][:12],
            "lastPollAt": getattr(ingest, "last_poll_at", None),
            "lastLiveAt": last_live_at,
            "observationAgeSec": round(age, 1) if age is not None else None,
            "matchedToTimetable": getattr(ingest, "matched", 0),
            "unmatched": getattr(ingest, "unmatched", []),
            "fallbackReason": getattr(ingest, "degraded", ""),
            "budgetRemaining": getattr(ingest, "budget_remaining", None),
            "passengerProvenance": (
                "RailRadar running-status observations assimilated into the twin"
                if feed_state == "LIVE" else "Timestamped RailRadar replay/fallback"),
            "freightProvenance": "SYNTHETIC FREIGHT · no public live freight feed",
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
                if self.clock_mode != "LIVE":
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
                if self.clock_mode == "LIVE":
                    return
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
            elif cmd == "validate_action":
                self._validate_proposed(msg)
            elif cmd == "observe":
                for eng in (self.engine, self.shadow_nothing, self.shadow_priority):
                    eng.observe(str(msg.get("number", "")), float(msg.get("latenessSec", 0)))

    def _set_clock_mode(self, mode: str) -> None:
        if self.live_locked and mode != "LIVE":
            return
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
        mode = "CONTAINMENT" if msg.get("responseMode") == "CONTAINMENT" else "RESOLUTION"
        projection, projection_reason = self._live_projection_check(
            before, action, current, mode)
        record.update(projection)
        safety = projection["safety"]
        if not safety.get("passed"):
            self._last_decision_status = {"status": "REJECTED",
                                          "reason": "Failed safety re-validation"}
            record.update(outcome="REJECTED", reason=self._last_decision_status["reason"])
            self._commit(record)
            return
        if projection_reason is not None:
            self._last_decision_status = {
                "status": "REJECTED", "reason": projection_reason}
            record.update(outcome="REJECTED", reason=projection_reason)
            self._commit(record)
            return

        # Cumulative guard. Each decision can pass its own test and the SET of
        # them still push the section past the do-nothing shadow - which is
        # exactly what happened when three freight holds stacked up. Project
        # this action forward and refuse it if it would breach the budget.
        if conflict_id in self._actioned:
            reason = "A command has already been issued for this conflict."
            self._last_decision_status = {"status": "REJECTED", "reason": reason}
            record.update(outcome="REJECTED", reason=reason)
            self._commit(record)
            return

        breach = self._would_breach_budget(action)
        if breach is not None:
            self._last_decision_status = {"status": "REJECTED", "reason": breach}
            self._lateness_budget_breaches += 1
            record.update(outcome="REJECTED", reason=breach)
            self._commit(record)
            return

        lateness_before = dto.kpis_dict(self._cached_kpis).get("totalLatenessSec", 0.0)
        self._actioned.add(conflict_id or "")
        applied = self.engine.apply_action(action)
        if not applied:
            self._actioned.discard(conflict_id or "")
            reason = ("The command is duplicate, expired, or the train has already "
                      "entered the protected resource.")
            self._last_decision_status = {"status": "REJECTED", "reason": reason}
            record.update(outcome="REJECTED", reason=reason)
            self._commit(record)
            return
        self._refresh_derived()
        record["latenessBeforeSec"] = lateness_before
        record["latenessAfterSec"] = dto.kpis_dict(self._cached_kpis).get("totalLatenessSec", 0.0)
        record["conflictCleared"] = not any(
            c.id == conflict_id for c in self._cached_prediction.conflicts)
        self._last_decision_status = {"status": outcome,
                                      "reason": "Applied after live safety re-validation"}
        self._commit(record)

    def _validate_proposed(self, msg: dict) -> None:
        """Check a controller's MODIFIED action without applying it.

        The console asks on every adjustment, so the Apply control can be
        disabled with a reason rather than letting a command through and
        rejecting it afterwards. Safety is never advisory: an action the
        interlocking would refuse cannot be issued at all.
        """
        action = _action(msg.get("action", {}))
        conflict_id = msg.get("conflictId")
        current = next((c for c in (self._cached_prediction.conflicts
                                    if self._cached_prediction else [])
                        if c.id == conflict_id), None)
        if current is None:
            self._last_validation = {
                "conflictId": conflict_id, "passed": False,
                "reason": "That conflict is no longer predicted.",
                "checks": [], "clears": False,
            }
            return

        before = self.engine.analytic_state()
        mode = "CONTAINMENT" if msg.get("responseMode") == "CONTAINMENT" else "RESOLUTION"
        projection, projection_reason = self._live_projection_check(
            before, action, current, mode)
        safety = projection["safety"]
        budget = self._would_breach_budget(action)

        failed = [c for c in safety.get("checks", []) if not c.get("passed")]
        passed = (bool(safety.get("passed")) and budget is None
                  and projection_reason is None)
        reason = ""
        if not safety.get("passed"):
            # A speed or route breach is the direct cause; SEP is usually a
            # consequence of it, so it should not be what the controller reads.
            order = {"SPD": 0, "RTE": 1, "PLT": 2, "PROTECT": 3, "SEP": 4}
            failed.sort(key=lambda c: order.get(c.get("id", ""), 9))
            reason = failed[0]["detail"] if failed else "Fails interlocking validation."
        elif budget is not None:
            reason = budget
        elif projection_reason is not None:
            reason = projection_reason
        self._last_validation = {
            "conflictId": conflict_id,
            "action": action.as_dict(),
            "passed": passed,
            "clears": projection["clears"],
            "reason": reason,
            "checks": safety.get("checks", []),
            **{key: value for key, value in projection.items()
               if key not in {"safety", "clears"}},
        }

    def _live_projection_check(self, before, action: AppliedAction, conflict,
                               mode: str) -> tuple[dict, str | None]:
        """Re-run a selected What-if against the current authoritative frame.

        A displayed option can be seconds old by the time it is accepted.
        Safety alone is insufficient because a safe route change can join a
        newly formed downstream queue.  This uses the same literal no-command
        rollout and action evaluator that produced the What-if result.
        """
        base = delay_profile(before)
        reference = evaluate_do_nothing(before, base, conflict, self.horizon)
        candidate = Candidate(
            "LIVE-REVALIDATION", "L", "Live operator selection", action,
            "LOW" if action.kind in {"PLATFORM_REASSIGNMENT", "ALTERNATE_ROUTE"}
            else "NONE")
        result = evaluate(
            before, base, candidate, conflict, self.horizon,
            response_class=mode, reference=reference)
        saving = reference.network_delay_sec - result.network_delay_sec
        projection = {
            "safety": result.safety,
            "clears": result.conflict_resolved,
            "projectedDelayNoActionSec": round(reference.network_delay_sec, 1),
            "projectedDelayWithActionSec": round(result.network_delay_sec, 1),
            "projectedDelaySavingSec": round(saving, 1),
            "projectedResidualCriticalConflicts": result.residual_conflicts,
        }
        if not result.feasible or not result.safety.get("passed"):
            return projection, "The live queue rollout no longer permits this action."
        if mode == "RESOLUTION" and not result.conflict_resolved:
            return projection, "The live queue rollout no longer clears this conflict."
        if result.residual_conflicts > 0:
            return projection, "The action would create an additional critical conflict."
        # A resolution must improve the whole-section finish profile. Safety
        # containment may tie FIFO, but it may never make the queue later.
        required_saving = 1.0 if mode == "RESOLUTION" else 0.0
        if saving < required_saving - 1e-6:
            if mode == "RESOLUTION":
                return projection, (
                    "The live queue no longer shows at least one second of whole-section "
                    "delay reduction versus taking no action.")
            return projection, "The containment action would make the live queue later."
        return projection, None

    def _would_breach_budget(self, action: AppliedAction) -> str | None:
        """Would applying this push AI total lateness past the do-nothing shadow?

        The shadow has seen the same timetable, seed and disruptions and has
        never taken a decision, so its total lateness is the ceiling the
        AI-assisted twin must stay under. Returns a reason when it would.
        """
        shadow = self._shadow_kpis.get("doNothing")
        if not shadow:
            return None
        ceiling = float(shadow.get("totalLatenessSec", 0.0))
        before = self.engine.analytic_state()
        after = apply_action(before, action)
        added = 0.0
        for tid, st in after.trains.items():
            prior = before.trains.get(tid)
            if prior is None or st.finished:
                continue
            added += max(0.0, st.hold_remaining - prior.hold_remaining)
        projected = float(
            (self._comparable_ai or dto.kpis_dict(self._cached_kpis)).get(
                "totalLatenessSec", 0.0)) + added
        if projected > ceiling + 1.0:
            return (f"Would take the section to {projected / 60:.1f} min late, past the "
                    f"{ceiling / 60:.1f} min it reaches with no action at all. "
                    "The twin will not issue a command that makes the section later.")
        return None

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
        route_id=d.get("routeId"), platform_id=d.get("platformId"),
        action_id=d.get("actionId"), plan_id=d.get("planId"),
        effective_resource_id=d.get("effectiveResourceId"),
        release_at_sec=d.get("releaseAtSec"),
        expires_after_resource_id=d.get("expiresAfterResourceId"),
        expires_at_sec=d.get("expiresAtSec"),
        lifecycle=d.get("lifecycle", "PROPOSED"),
        supersedes_action_id=d.get("supersedesActionId"),
        reason_conflict_id=d.get("reasonConflictId"))


def _event(d: dict) -> DelayEvent:
    return DelayEvent(
        event_id=d.get("eventId", "EV"),
        target_type=d.get("targetType", "TRAIN"),
        target_id=d.get("targetId", ""),
        delay_seconds=float(d.get("delaySeconds", 0) or 0),
        reason=d.get("reason", ""), severity=d.get("severity", "MEDIUM"),
        scenario_id=d.get("scenarioId", ""))
