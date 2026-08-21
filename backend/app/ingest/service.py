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
import datetime as dt
import logging
import time

from ..config import settings
from ..network.fleet import FleetEntry, fleet, fleet_by_id
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
        self.last_poll_at: float | None = None
        self.last_live_at: float | None = None
        self.last_detail_at: float | None = None
        self.board_polls = 0
        self.detail_polls = 0
        self.degraded = ""
        self.matched = 0
        self.unmatched: list[str] = []
        self.requested_mode = self.client.mode
        self.consecutive_failures = 0
        self.retry_live_at = 0.0
        self.budget_remaining: int | None = None

    @property
    def enabled(self) -> bool:
        return self.client.mode in ("live", "replay")

    # ------------------------------------------------------------- cadence
    async def _cadence(self) -> tuple[float, float]:
        """(board interval, detail interval), widened as the budget runs down."""
        if self.client.mode == "replay":
            if (self.requested_mode == "live" and self.client.api_key
                    and time.time() >= self.retry_live_at
                    and await self.client.budget.remaining()
                    > settings.railradar_reserve // 5):
                self.client.mode = "live"
                self.consecutive_failures = 0
            else:
                if not self.degraded and self.requested_mode != "live":
                    self.degraded = "Configured replay feed"
                return settings.railradar_board_seconds, 0.0
        remaining = await self.client.budget.remaining()
        self.budget_remaining = remaining
        reserve = settings.railradar_reserve
        if remaining <= reserve // 5:
            self.degraded = (f"only {remaining} requests left this month - serving the "
                             "recorded feed instead")
            if self.client.mode == "live":
                self.client.mode = "replay"
                self.client._load_replay()
                self.retry_live_at = time.time() + 300.0
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
        attempted_live = self.client.mode == "live"
        rows = await self.client.station_board()
        self._ensure_live_services(rows)
        fresh = [o for o in rows if self._record(o)]
        if fresh:
            self._assimilate(fresh)
        now = time.time()
        self.last_poll_at = now
        self.last_board_at = now
        if attempted_live:
            if rows:
                self.last_live_at = now
                self.consecutive_failures = 0
            elif self.client.last_error:
                self.consecutive_failures += 1
                if self.consecutive_failures >= 3:
                    self.degraded = (
                        "RailRadar unavailable after three polls; serving timestamped replay")
                    self.client.mode = "replay"
                    self.client._load_replay()
                    self.retry_live_at = now + 300.0
        self.board_polls += 1
        self._match_report(rows)
        return rows

    def feed_state(self) -> str:
        if not self.enabled:
            return "OFF"
        if self.client.mode == "replay":
            return ("REPLAY_FALLBACK" if self.requested_mode == "live"
                    else "REPLAY")
        if self.last_live_at is None:
            return "STALE"
        stale_after = max(180.0, settings.railradar_board_seconds * 3)
        if time.time() - self.last_live_at > stale_after:
            return "STALE"
        return "DEGRADED" if self.degraded else "LIVE"

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
        matched = {observation.number for observation in rows
                   if self._matched_entries(observation)}
        seen = {observation.number for observation in rows}
        self.matched = len(matched)
        self.unmatched = sorted(seen - matched)[:20]

    def _matched_entries(self, observation: Observation) -> list[FleetEntry]:
        """Match identity *and* this run of the service at Vasai Road."""
        booked = self._service_seconds(
            observation.scheduled_departure or observation.scheduled_arrival)
        day_names = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")
        run_days = {day[:3].casefold() for day in observation.run_days}
        out = []
        for entry in fleet_by_id.values():
            if entry.number != observation.number:
                continue
            if run_days and day_names[self.orchestrator.engine.weekday_sun0] not in run_days:
                continue
            if booked is not None:
                delta = abs(entry.booked_dep_sec - booked)
                delta = min(delta, 86_400 - delta)
                if delta > 15 * 60:
                    continue
            out.append(entry)
        return out

    def _ensure_live_services(self, rows: list[Observation]) -> None:
        """Instantiate defensible unmatched passenger services.

        A station board does not contain enough geometry to invent a route.  We
        only admit a temporary service when the published timetable contains an
        existing passenger movement with the exact same origin and destination;
        that template supplies its corridor, class and operating limits.
        """
        templates: dict[tuple[str, str], FleetEntry] = {}
        for entry in fleet:
            if entry.is_freight:
                continue
            key = (entry.origin.strip().casefold(), entry.destination.strip().casefold())
            if all(key):
                templates.setdefault(key, entry)

        for obs in rows:
            if self._matched_entries(obs):
                continue
            template = templates.get((obs.origin.strip().casefold(),
                                      obs.destination.strip().casefold()))
            booked = self._service_seconds(obs.scheduled_departure
                                           or obs.scheduled_arrival)
            if template is None or booked is None:
                continue
            platform = str(obs.platform or "").strip().upper().replace("PLATFORM", "")
            if platform and not platform.startswith("PF"):
                platform = f"PF{platform}"
            if platform not in {f"PF{i}" for i in range(1, 8)}:
                platform = template.booked_platform or ""
            service_id = f"LIVE-{obs.number}-{booked}"
            if service_id in fleet_by_id:
                continue
            entry = FleetEntry(
                id=service_id, number=obs.number, name=obs.name or template.name,
                category=template.category, service_class=template.service_class,
                priority=template.priority, economic_weight=template.economic_weight,
                typical_load=template.typical_load,
                line_speed_kmh=template.line_speed_kmh,
                dwell_sec=template.dwell_sec, booked_dep_sec=booked,
                booked_platform=platform or None, operating_days="*******",
                arrival_corridor=template.arrival_corridor,
                departure_corridor=template.departure_corridor,
                corridor_confidence="INFERRED_EXACT_OD",
                origin=obs.origin, destination=obs.destination, gross_tonnes=0.0,
                source="RailRadar station board",
                provenance=("Temporary live service; physical route copied from a "
                            "published train with identical origin/destination"),
            )
            fleet_by_id[service_id] = entry
            for engine in (self.orchestrator.engine,
                           self.orchestrator.shadow_nothing,
                           self.orchestrator.shadow_priority):
                engine._create(entry)

    @staticmethod
    def _service_seconds(value: str) -> int | None:
        if not value:
            return None
        text = value.strip()
        for fmt in ("%H:%M:%S", "%H:%M"):
            try:
                parsed = dt.datetime.strptime(text[-8:] if fmt == "%H:%M:%S"
                                              else text[-5:], fmt)
                return parsed.hour * 3600 + parsed.minute * 60 + parsed.second
            except ValueError:
                continue
        try:
            parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
            local = parsed.astimezone(dt.timezone(dt.timedelta(hours=5, minutes=30)))
            return local.hour * 3600 + local.minute * 60 + local.second
        except ValueError:
            return None

    def _assimilate(self, observations: list[Observation]) -> None:
        """Feed readings into the live twin AND both shadows - a baseline that
        did not see the same disruptions would not be a counterfactual."""
        orch = self.orchestrator
        engines = [orch.engine, orch.shadow_nothing, orch.shadow_priority]
        for obs in observations:
            for engine in engines:
                engine.observe_metadata(obs.number, obs.source, obs.observed_at)
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
            if obs.source == "detail" and obs.last_station:
                for engine in engines:
                    engine.observe_location(obs.number, obs.last_station)

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
            "lastPollAt": self.last_poll_at,
            "lastLiveAt": self.last_live_at,
            "sourceState": self.feed_state(),
            "lastDetailAt": self.last_detail_at,
            "observedTrains": len(self.observations),
            "usableObservations": len(usable),
            "matchedToTimetable": self.matched,
            "unmatched": self.unmatched,
            "inSection": self.in_section(),
            "observations": [o.as_dict() for o in list(self.observations.values())[:60]],
            "collected": self.collector.count,
        }
