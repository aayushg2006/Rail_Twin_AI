"""Ingestion loop: read the station board, assimilate it, enrich what matters.

Two cadences, because the two endpoints buy very different things:

  BOARD   `/v1/stations/BSR/live`, one request, every train due at Vasai Road
          with its live delay. This is the RTIS-like picture and it is cheap.
  DETAIL  `/v1/trains/{n}/live`, one request per train, but it carries the real
          platform and the real section speed. Only spent on services actually
          inside the modelled area.

The free tier is 1,000 requests a month, so the rate DEGRADES as the allowance
runs down instead of failing mid-demonstration: below `reserve` the board slows
to five minutes and detail stops; below a fifth of it, the client falls back to
the recorded feed and keeps working with no network at all.
"""
from __future__ import annotations

import asyncio
import logging
import time

from ..config import settings
from ..network.fleet import fleet, fleet_by_id
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
        self.last_board_at: float | None = None
        self.last_detail_at: float | None = None
        self.board_polls = 0
        self.detail_polls = 0
        self.degraded = ""
        self.matched = 0
        self.unmatched: list[str] = []

    @property
    def enabled(self) -> bool:
        return self.client.mode in ("live", "replay")

    # ------------------------------------------------------------- cadence
    async def _cadence(self) -> tuple[float, float]:
        """(board interval, detail interval), widened as the budget runs down."""
        if self.client.mode == "replay":
            self.degraded = ""
            return settings.railradar_board_seconds, settings.railradar_detail_seconds
        remaining = await self.client.budget.remaining()
        reserve = settings.railradar_reserve
        if remaining <= reserve // 5:
            self.degraded = (f"only {remaining} requests left this month - serving the "
                             "recorded feed instead")
            if self.client.mode == "live":
                self.client.mode = "replay"
                self.client._load_replay()
            return settings.railradar_board_seconds, 0.0
        if remaining <= reserve:
            self.degraded = (f"{remaining} requests left this month - board slowed to "
                             "5 minutes, per-train detail paused")
            return 300.0, 0.0
        self.degraded = ""
        return settings.railradar_board_seconds, settings.railradar_detail_seconds

    # ------------------------------------------------------------- watchlist
    def in_section(self) -> list[str]:
        """Train numbers actually inside the modelled area right now.

        These are the only ones worth a per-train request: the platform and
        section speed matter for a movement the twin is simulating, and for
        nothing else.
        """
        engine = self.orchestrator.engine
        numbers: list[str] = []
        for tid, rt in engine.trains.items():
            if rt.finished or not rt.admitted:
                continue
            entry = fleet_by_id.get(tid)
            if entry is None or entry.is_freight:
                continue          # goods have no live feed
            if entry.number not in numbers:
                numbers.append(entry.number)
        # The twin's clock need not agree with the wall clock, so prefer numbers
        # the board says are actually moving. Spending a call on a service that
        # has not left its origin buys a delay reading of zero.
        moving = [n for n in numbers
                  if (o := self.observations.get(n)) is not None and o.running]
        ordered = moving + [n for n in numbers if n not in moving]
        return ordered[:settings.railradar_detail_max]

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
        await self.poll_board()
        while True:
            board_every, detail_every = await self._cadence()
            await asyncio.sleep(max(15.0, board_every))
            try:
                await self.poll_board()
                if detail_every > 0 and (
                        self.last_detail_at is None
                        or time.time() - self.last_detail_at >= detail_every):
                    await self.poll_detail()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("RailRadar poll failed")

    async def poll_board(self) -> list[Observation]:
        rows = await self.client.station_board()
        fresh = [o for o in rows if self._record(o)]
        if fresh:
            self._assimilate(fresh)
        self.last_board_at = time.time()
        self.board_polls += 1
        self._match_report(rows)
        return rows

    async def poll_detail(self) -> list[Observation]:
        fresh: list[Observation] = []
        for number in self.in_section():
            obs = await self.client.train_detail(number)
            if obs is None:
                continue
            if self._record(obs):
                fresh.append(obs)
        if fresh:
            self._assimilate(fresh)
        self.last_detail_at = time.time()
        self.detail_polls += 1
        return fresh

    def _record(self, obs: Observation) -> bool:
        """Store the reading; return True when it is safe to assimilate."""
        previous = self.observations.get(obs.number)
        # A board reading must not overwrite the richer detail reading.
        if previous is not None and previous.source == "detail" and obs.source == "board":
            merged = Observation(**{**previous.__dict__,
                                    "lateness_sec": obs.lateness_sec,
                                    "status": obs.status})
            self.observations[obs.number] = merged
        else:
            self.observations[obs.number] = obs
        asyncio.get_running_loop().create_task(self.collector.record(obs))
        # `running` gates the DELAY only. A not-started service still carries a
        # real platform allocation and a real section speed, and those are worth
        # having in advance - it is exactly what a controller plans against.
        return obs.running or bool(obs.platform) or bool(obs.speed_to_next_kmph)

    def _match_report(self, rows: list[Observation]) -> None:
        """How much of the live board the twin's timetable actually knows."""
        known = {f.number for f in fleet}
        seen = {o.number for o in rows}
        self.matched = len(seen & known)
        self.unmatched = sorted(seen - known)[:20]

    def _assimilate(self, observations: list[Observation]) -> None:
        """Feed readings into the live twin AND both shadows - a baseline that
        did not see the same disruptions would not be a counterfactual."""
        orch = self.orchestrator
        engines = [orch.engine, orch.shadow_nothing, orch.shadow_priority]
        for obs in observations:
            if obs.running:
                # Lateness is only meaningful once the train has left its origin.
                for engine in engines:
                    engine.observe(obs.number, obs.lateness_sec)
            if obs.speed_to_next_kmph:
                for engine in engines:
                    engine.observe_speed(obs.number, obs.speed_to_next_kmph)
            if obs.platform:
                for engine in engines:
                    engine.observe_platform(obs.number, obs.platform)

    async def status(self) -> dict:
        base = await self.client.status()
        board_every, detail_every = await self._cadence()
        usable = [o for o in self.observations.values() if o.running]
        return {
            **base,
            "enabled": self.enabled,
            "boardSeconds": board_every,
            "detailSeconds": detail_every,
            "degraded": self.degraded,
            "boardPolls": self.board_polls,
            "detailPolls": self.detail_polls,
            "lastBoardAt": self.last_board_at,
            "lastDetailAt": self.last_detail_at,
            "observedTrains": len(self.observations),
            "usableObservations": len(usable),
            "matchedToTimetable": self.matched,
            "unmatched": self.unmatched,
            "inSection": self.in_section(),
            "observations": [o.as_dict() for o in list(self.observations.values())[:60]],
            "collected": self.collector.count,
        }
