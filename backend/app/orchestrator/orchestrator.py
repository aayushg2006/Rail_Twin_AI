"""SimulationOrchestrator (Phase 6).

Owns one live twin, advances it on an independent clock, assembles the snapshot
bundle every tick and broadcasts it to WebSocket clients. All simulation logic
lives here and in the domain layers — never in the FastAPI route handlers.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Any, Callable

from ..config import settings
from ..domain import dto
from ..twin.engine import DelayEvent, SimulationEngine
from ..twin.metrics import compute_kpis
from ..twin.predict import predict
from ..twin.predict import apply_action
from ..twin.state import AppliedAction
from ..optimize.safety import validate
from ..network.data import data_pack

VALID_SPEEDS = {1, 2, 5, 10, 20}


class SimulationOrchestrator:
    def __init__(self, scenario_id: str = "BASE"):
        self.clock_mode = settings.clock_mode.upper()
        epoch = settings.demo_epoch_start_ms if self.clock_mode == "DEMO" else int(time.time() * 1000)
        self.engine = SimulationEngine(scenario_id, seed=settings.seed, epoch_start_ms=epoch)
        self.playing = True
        self.speed = settings.default_speed
        self.horizon = settings.default_horizon_sec
        self._clients: set[Any] = set()
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._tick_count = 0
        self._cached_prediction = None
        self._cached_kpis = None
        self._cached_options: dict = {}
        # hooks populated by later phases (options/recommendation/ml)
        self.decision_hook: Callable[[dict], None] | None = None
        self.options_provider: Callable[[SimulationEngine, Any], dict] | None = None
        self.ml_provider: Callable[[SimulationEngine, Any], dict] | None = None
        self._baseline_kpis: dict | None = None
        self._suggestion_revision = 0
        self._suggestion_generated_at = 0.0
        self._last_decision_status: dict = {"status": "READY"}
        self.pending_scenario_events: list[dict] = []
        self.active_scenario_payload: dict | None = None
        self.persistence_status = "IN_MEMORY"
        self.scenario_store = None
        self._refresh_derived()

    # ------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None

    async def _run_loop(self) -> None:
        interval = settings.tick_seconds
        while True:
            t0 = time.perf_counter()
            async with self._lock:
                if self.playing:
                    self.engine.advance(self.speed * interval)
                self._apply_pending_scenario_events()
                self._tick_count += 1
                # Predictions/recommendations refresh twice per wall-clock
                # second; commands and disruptions refresh immediately.
                if self._tick_count % 2 == 0:
                    self._refresh_derived()
                bundle = self._build_bundle()
            await self._broadcast(bundle)
            elapsed = time.perf_counter() - t0
            await asyncio.sleep(max(0.0, interval - elapsed))

    # ------------------------------------------------------------- derived
    def _refresh_derived(self) -> None:
        self._suggestion_revision += 1
        self._suggestion_generated_at = self.engine.now
        astate = self.engine.analytic_state()
        self._cached_prediction = predict(astate, self.horizon)
        self._cached_kpis = compute_kpis(astate, self._cached_prediction)
        if self._baseline_kpis is None:
            self._baseline_kpis = dto.kpis_dict(self._cached_kpis)
        # CP-SAT options + ML predictions on the (coarser) derived cadence.
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

    def _build_bundle(self) -> dict:
        pred = self._cached_prediction
        kpis = self._cached_kpis
        bundle: dict = {
            "type": "snapshot",
            "connection": "CONNECTED",
            "playing": self.playing,
            "speed": self.speed,
            "simState": dto.sim_state_dict(self.engine),
            "prediction": dto.prediction_dict(pred),
            "kpis": dto.kpis_dict(kpis),
            "baselineKpis": self._baseline_kpis,
            "causalChain": dto.causal_chain_list(self.engine),
            "delayBuckets": dto.delay_buckets_map(self.engine),
            "dataPackId": data_pack["id"],
            "dataProvenance": "passenger snapshots + synthetic freight demo; FOIS not connected",
            "snapshotDate": data_pack["snapshotDate"],
            "clockMode": self.clock_mode,
            "wallClockMs": int(self.engine.epoch_start_ms + self.engine.now * 1000),
            "serviceDate": datetime.fromtimestamp(
                (self.engine.epoch_start_ms + self.engine.now * 1000) / 1000,
                tz=ZoneInfo("Asia/Kolkata"),
            ).date().isoformat(),
            "connectionStatus": "CONNECTED",
            "persistenceStatus": getattr(self, "persistence_status", "IN_MEMORY"),
            "suggestionRevision": self._suggestion_revision,
            "suggestionGeneratedAt": self._suggestion_generated_at,
            "lastDecisionStatus": self._last_decision_status,
        }
        bundle.update(self._cached_options)
        return bundle

    # ------------------------------------------------------------- clients
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

    # ------------------------------------------------------------- commands
    async def handle_command(self, msg: dict) -> None:
        cmd = msg.get("cmd")
        async with self._lock:
            if cmd == "pause":
                self.playing = False
            elif cmd == "resume" or cmd == "play":
                self.playing = True
            elif cmd == "set_speed":
                sp = int(msg.get("speed", self.speed))
                if self.clock_mode == "LIVE":
                    self.speed = 1
                elif sp in VALID_SPEEDS:
                    self.speed = sp
            elif cmd == "set_clock_mode":
                self._set_clock_mode(str(msg.get("mode", "LIVE")).upper())
            elif cmd == "seek":
                target = float(msg.get("simTimeSec", self.engine.now))
                self.playing = False
                self.engine.seek(target)
                self._refresh_derived()
            elif cmd == "set_horizon":
                self.horizon = int(msg.get("horizonSec", self.horizon))
                self._refresh_derived()
            elif cmd == "apply_action":
                self.engine.apply_action(_action(msg.get("action", {})))
                self._refresh_derived()
            elif cmd == "inject_event":
                self.engine.inject_event(_event(msg.get("event", {})))
                self._refresh_derived()
            elif cmd == "load_scenario":
                self._load(msg.get("scenario", "BASE"))
            elif cmd == "load_custom_scenario":
                self._load_custom(msg.get("scenario", {}))
            elif cmd == "reset":
                self._load(self.engine.scenario_id)
            elif cmd == "decide":
                self._decide(msg)

    def _load(self, scenario_id: str) -> None:
        epoch = settings.demo_epoch_start_ms if self.clock_mode == "DEMO" else int(time.time() * 1000)
        self.engine = SimulationEngine(scenario_id, seed=settings.seed, epoch_start_ms=epoch)
        self._baseline_kpis = None
        self.playing = True
        self.pending_scenario_events = []
        self.active_scenario_payload = None
        self._refresh_derived()
        if self.scenario_store and self.active_scenario_payload:
            self.scenario_store.save_scenario_sync(self.active_scenario_payload, self.clock_mode, data_pack["id"])

    def _set_clock_mode(self, mode: str) -> None:
        if mode not in ("LIVE", "DEMO") or mode == self.clock_mode:
            return
        self.clock_mode = mode
        self.speed = 1 if mode == "LIVE" else settings.default_speed
        self._load(self.engine.scenario_id)

    def _load_custom(self, payload: dict) -> None:
        """Load a bounded, validated scenario assembled by the controller UI."""
        events = payload.get("events", [])
        if not isinstance(events, list) or len(events) > 16:
            self._last_decision_status = {"status": "REJECTED", "reason": "A scenario may contain at most 16 events"}
            return
        self._load("BASE")
        self.engine.scenario_id = str(payload.get("id", "CUSTOM"))[:80] or "CUSTOM"
        self.active_scenario_payload = {**payload, "events": []}
        known_trains = set(self.engine.trains)
        known_resources = set(self.engine.resources)
        for i, raw in enumerate(events):
            if not isinstance(raw, dict):
                continue
            kind = raw.get("kind")
            target = str(raw.get("targetId", ""))
            value = float(raw.get("value", 0) or 0)
            valid = (
                (kind in ("TRAIN_DELAY", "HOLD") and target in known_trains and 0 <= value <= 1800)
                or (kind in ("BREAKDOWN", "SPEED_RESTRICTION") and target in known_trains and 5 <= value <= 120)
                or (kind in ("BLOCK", "PLATFORM") and target in known_resources)
                or (kind == "HEADWAY" and 0.5 <= value <= 4)
            )
            if not valid:
                continue
            normalized = {**raw, "id": raw.get("id", f"CUSTOM-{i}"), "atSec": max(0.0, float(raw.get("atSec", 0) or 0))}
            self.active_scenario_payload["events"].append(normalized)
            if normalized["atSec"] > self.engine.now:
                self.pending_scenario_events.append(normalized)
            else:
                self._apply_scenario_event(normalized)
        self._refresh_derived()
        if self.scenario_store and self.active_scenario_payload:
            self.scenario_store.save_scenario_sync(self.active_scenario_payload, self.clock_mode, data_pack["id"])

    def _apply_scenario_event(self, raw: dict) -> None:
        kind, target = raw.get("kind"), str(raw.get("targetId", ""))
        value = float(raw.get("value", 0) or 0)
        if kind in ("TRAIN_DELAY", "HOLD"):
            self.engine.inject_event(DelayEvent(event_id=str(raw.get("id", "EV")), target_type="TRAIN", target_id=target, delay_seconds=value, reason=raw.get("reason", kind), scenario_id="CUSTOM"))
        elif kind in ("BREAKDOWN", "SPEED_RESTRICTION"):
            self.engine.inject_event(DelayEvent(event_id=str(raw.get("id", "EV")), target_type="SPEED", target_id=target, delay_seconds=value, reason=raw.get("reason", kind), scenario_id="CUSTOM"))
        elif kind in ("BLOCK", "PLATFORM"):
            self.engine.inject_event(DelayEvent(event_id=str(raw.get("id", "EV")), target_type=kind, target_id=target, reason=raw.get("reason", kind), scenario_id="CUSTOM"))
        elif kind == "HEADWAY":
            self.engine.set_headway_multiplier(value)

    def _apply_pending_scenario_events(self) -> None:
        due = [e for e in self.pending_scenario_events if float(e.get("atSec", 0)) <= self.engine.now]
        for event in due:
            self._apply_scenario_event(event)
            self.pending_scenario_events.remove(event)

    def _decide(self, msg: dict) -> None:
        outcome = msg.get("outcome", "REJECTED")
        action = _action(msg.get("action", {}))
        expected = msg.get("expectedRevision")
        if expected is not None and int(expected) != self._suggestion_revision:
            self._last_decision_status = {"status": "STALE", "reason": "Recommendation was superseded by a newer live frame"}
            if self.decision_hook:
                self.decision_hook({**msg, "outcome": "REJECTED", "reason": "STALE", "simTimeSec": self.engine.now})
            return
        current = next((c for c in (self._cached_prediction.conflicts if self._cached_prediction else [])
                        if c.id == msg.get("conflictId")), None)
        if current is None and outcome != "REJECTED":
            self._last_decision_status = {"status": "REJECTED", "reason": "Conflict is no longer active"}
            return
        if current is not None and outcome != "REJECTED":
            after = apply_action(self.engine.analytic_state(), action)
            projected = predict(after, self.horizon)
            residual = any(
                c.severity == "CRITICAL" and c.resource_id == current.resource_id
                and (c.train_a in {current.train_a, current.train_b, action.train_id}
                     or c.train_b in {current.train_a, current.train_b, action.train_id})
                for c in projected.conflicts
            )
            safety = validate(action, after, current, not residual)
            if not safety.get("passed"):
                self._last_decision_status = {"status": "REJECTED", "reason": "Safety validation failed"}
                if self.decision_hook:
                    self.decision_hook({**msg, "outcome": "REJECTED", "reason": "UNSAFE", "simTimeSec": self.engine.now})
                return
        if outcome != "REJECTED" and action.kind != "NO_ACTION":
            self.engine.apply_action(action)
            self._refresh_derived()
            self._last_decision_status = {"status": outcome, "reason": "Applied after live safety revalidation"}
        elif outcome == "REJECTED":
            self._last_decision_status = {"status": "REJECTED", "reason": msg.get("note", "Rejected by controller")}
        if self.decision_hook:
            self.decision_hook({**msg, "simTimeSec": self.engine.now})


def _action(d: dict) -> AppliedAction:
    return AppliedAction(
        kind=d.get("kind", "NO_ACTION"), train_id=d.get("trainId", ""),
        speed_kmh=d.get("speedKmh"), hold_sec=d.get("holdSec"),
        route_id=d.get("routeId"), platform_id=d.get("platformId"))


def _event(d: dict) -> DelayEvent:
    return DelayEvent(
        event_id=d.get("event_id", d.get("eventId", "EV")),
        target_type=d.get("target_type", d.get("targetType", "TRAIN")),
        target_id=d.get("target_id", d.get("targetId", "")),
        delay_seconds=float(d.get("delay_seconds", d.get("delaySeconds", 0)) or 0),
        reason=d.get("reason", ""), severity=d.get("severity", "MEDIUM"),
        scenario_id=d.get("scenario_id", d.get("scenarioId", "")))
