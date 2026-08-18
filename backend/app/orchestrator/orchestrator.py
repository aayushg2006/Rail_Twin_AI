"""SimulationOrchestrator (Phase 6).

Owns one live twin, advances it on an independent clock, assembles the snapshot
bundle every tick and broadcasts it to WebSocket clients. All simulation logic
lives here and in the domain layers — never in the FastAPI route handlers.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Callable

from ..config import settings
from ..domain import dto
from ..twin.engine import DelayEvent, SimulationEngine
from ..twin.metrics import compute_kpis
from ..twin.predict import predict
from ..twin.state import AppliedAction

VALID_SPEEDS = {1, 2, 5, 10, 20}


class SimulationOrchestrator:
    def __init__(self, scenario_id: str = "BASE"):
        self.engine = SimulationEngine(scenario_id, seed=settings.seed)
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
                self._tick_count += 1
                if self._tick_count % 4 == 0:
                    self._refresh_derived()
                bundle = self._build_bundle()
            await self._broadcast(bundle)
            elapsed = time.perf_counter() - t0
            await asyncio.sleep(max(0.0, interval - elapsed))

    # ------------------------------------------------------------- derived
    def _refresh_derived(self) -> None:
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
                if sp in VALID_SPEEDS:
                    self.speed = sp
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
            elif cmd == "reset":
                self._load(self.engine.scenario_id)
            elif cmd == "decide":
                self._decide(msg)

    def _load(self, scenario_id: str) -> None:
        self.engine = SimulationEngine(scenario_id, seed=settings.seed)
        self._baseline_kpis = None
        self.playing = True
        self._refresh_derived()

    def _decide(self, msg: dict) -> None:
        outcome = msg.get("outcome", "REJECTED")
        action = _action(msg.get("action", {}))
        if outcome != "REJECTED" and action.kind != "NO_ACTION":
            self.engine.apply_action(action)
            self._refresh_derived()
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
