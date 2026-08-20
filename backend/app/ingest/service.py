"""Ingestion loop: poll a small watchlist and assimilate the readings.

Only a handful of services can be polled on the free tier, so the watchlist is
whichever passenger services are closest to their booked departure from Vasai
Road - those are the ones whose lateness actually matters to the next fifteen
minutes of the junction. Each reading sets that service's entry lateness in the
live twin AND in both shadow twins, so the counterfactual stays honest.
"""
from __future__ import annotations

import asyncio
import logging
import time

from ..config import settings
from ..network.fleet import fleet
from .collector import ObservationCollector
from .railradar import Observation, RailRadarClient

logger = logging.getLogger("railtwin.ingest")


class IngestionService:
    def __init__(self, orchestrator, client: RailRadarClient | None = None):
        self.orchestrator = orchestrator
        self.client = client or RailRadarClient()
        self.collector = ObservationCollector(settings.database_url)
        self.observations: dict[str, Observation] = {}
        self._task: asyncio.Task | None = None
        self.last_poll_at: float | None = None
        self.polls = 0

    @property
    def enabled(self) -> bool:
        return self.client.mode in ("live", "replay")

    # ------------------------------------------------------------- watchlist
    def watchlist(self) -> list[str]:
        """Passenger services nearest their booked time at Vasai Road."""
        now = self.orchestrator.engine.service_seconds
        weekday = self.orchestrator.engine.weekday_sun0
        candidates = [
            f for f in fleet
            if not f.is_freight and f.runs_on(weekday)
            and -900 <= (f.booked_dep_sec - now) <= 3600
        ]
        candidates.sort(key=lambda f: abs(f.booked_dep_sec - now))
        seen: list[str] = []
        for f in candidates:
            if f.number not in seen:
                seen.append(f.number)
            if len(seen) >= settings.railradar_watchlist_size:
                break
        return seen

    # ------------------------------------------------------------------ loop
    async def start(self) -> None:
        if not self.enabled:
            logger.info("RailRadar ingestion disabled (mode=%s)", self.client.mode)
            return
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None
        await self.client.aclose()
        await self.collector.close()

    async def _run(self) -> None:
        # A first pass immediately so the console is not empty on launch.
        await self.poll_once()
        while True:
            await asyncio.sleep(settings.railradar_poll_seconds)
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("RailRadar poll failed")

    async def poll_once(self) -> list[Observation]:
        now = time.time()
        fresh: list[Observation] = []
        for number in self.watchlist():
            obs = await self.client.live_status(number, now)
            if obs is None:
                continue
            self.observations[number] = obs
            await self.collector.record(obs)
            # A service that has not left its origin reports 0 minutes late.
            # That is not evidence it is running to time, so it is recorded but
            # never assimilated.
            if obs.running:
                fresh.append(obs)
        if fresh:
            self._assimilate(fresh)
        self.last_poll_at = now
        self.polls += 1
        return fresh

    def _assimilate(self, observations: list[Observation]) -> None:
        """Feed readings into the live twin and both shadows alike - a baseline
        that did not see the same disruptions would not be a counterfactual."""
        orch = self.orchestrator
        engines = [orch.engine, orch.shadow_nothing, orch.shadow_priority]
        for obs in observations:
            for engine in engines:
                engine.observe(obs.number, obs.lateness_sec)

    async def status(self) -> dict:
        base = await self.client.status()
        return {
            **base,
            "enabled": self.enabled,
            "watchlist": self.watchlist() if self.enabled else [],
            "polls": self.polls,
            "lastPollAt": self.last_poll_at,
            "observations": [o.as_dict() for o in self.observations.values()],
            "collected": self.collector.count,
            "pollSeconds": settings.railradar_poll_seconds,
        }
