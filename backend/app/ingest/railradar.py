"""RailRadar client - live running status for passenger services.

Three things about this feed shape the design.

1. It is NOT a position stream. `/v1/trains/{number}/live` returns a delay in
   minutes and the last reported halt. The twin therefore treats each response
   as an OBSERVATION to assimilate, and keeps simulating between observations -
   which is what a digital twin is supposed to do.
2. The free tier allows 1,000 requests a MONTH (~33/day). Polling every train
   every few seconds is impossible, so a small watchlist of the services nearest
   their booked time is polled on a long interval, cached in Redis, and a hard
   budget counter refuses calls once the allowance is spent.
3. It covers passenger services only. Goods movements have no public feed, so
   they remain synthetic and are labelled as such everywhere.

Modes (RAILTWIN_RAILRADAR_MODE):
    off     never touches the network - the default
    replay  serves a recorded feed from disk, so a demo cannot be broken by a
            flaky connection or an exhausted quota
    live    calls the API, subject to the budget
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from ..config import settings

logger = logging.getLogger("railtwin.railradar")


@dataclass(frozen=True)
class Observation:
    """One live reading for a train number."""
    number: str
    lateness_sec: float
    last_station: str
    observed_at: str
    source: str                 # live | replay | cache
    status: str = "unknown"     # not-started | running | at-station | completed
    is_live: bool = True        # False when the feed projects rather than tracks

    @property
    def running(self) -> bool:
        """Only a train that has actually started carries a meaningful delay.

        A service reported as `not-started` has delayMinutes 0 because it has
        not left its origin yet. Assimilating that as "on time" would overwrite
        whatever the twin had legitimately inferred.
        """
        return self.status not in ("not-started", "cancelled", "completed")

    def as_dict(self) -> dict:
        return {
            "number": self.number,
            "latenessSec": round(self.lateness_sec, 1),
            "lastStation": self.last_station,
            "observedAt": self.observed_at,
            "source": self.source,
            "status": self.status,
            "isLive": self.is_live,
            "usable": self.running,
        }


class BudgetExhausted(RuntimeError):
    pass


class RequestBudget:
    """Monthly call counter, shared across restarts when Redis is reachable."""

    def __init__(self, limit: int, redis_url: str | None):
        self.limit = limit
        self._redis_url = redis_url
        self._redis = None
        self._local = 0

    @staticmethod
    def _key() -> str:
        return f"railtwin:railradar:calls:{dt.date.today():%Y-%m}"

    async def _client(self):
        if self._redis is None and self._redis_url:
            try:
                import redis.asyncio as aioredis
                self._redis = aioredis.from_url(self._redis_url,
                                                decode_responses=True)
                await self._redis.ping()
            except Exception:
                self._redis = None
                self._redis_url = None
        return self._redis

    async def used(self) -> int:
        client = await self._client()
        if client is None:
            return self._local
        try:
            return int(await client.get(self._key()) or 0)
        except Exception:
            return self._local

    async def remaining(self) -> int:
        return max(0, self.limit - await self.used())

    async def spend(self, n: int = 1) -> None:
        """Reserve `n` calls, or refuse. Counted BEFORE the request is made so a
        crash mid-flight cannot silently overspend the allowance."""
        client = await self._client()
        if client is None:
            if self._local + n > self.limit:
                raise BudgetExhausted(
                    f"monthly RailRadar budget of {self.limit} is spent")
            self._local += n
            return
        try:
            total = int(await client.incrby(self._key(), n))
            # Expire ~40 days out so the key rolls with the month by itself.
            await client.expire(self._key(), 60 * 60 * 24 * 40)
        except Exception:
            self._local += n
            return
        if total > self.limit:
            raise BudgetExhausted(
                f"monthly RailRadar budget of {self.limit} is spent")


class RailRadarClient:
    def __init__(self, mode: str | None = None, api_key: str | None = None):
        self.mode = (mode or settings.railradar_mode).lower()
        self.api_key = api_key if api_key is not None else settings.railradar_api_key
        self.base_url = settings.railradar_base_url.rstrip("/")
        self.budget = RequestBudget(settings.railradar_monthly_budget,
                                    settings.redis_url)
        self._replay: dict[str, dict] = {}
        self._cache: dict[str, tuple[float, Observation]] = {}
        self._http = None
        self.last_error: str = ""

        if self.mode == "live" and not self.api_key:
            self.mode = "off"
            self.last_error = ("RAILTWIN_RAILRADAR_API_KEY is not set; "
                               "live mode disabled")
        if self.mode == "replay":
            self._load_replay()

    # ------------------------------------------------------------------ setup
    def _load_replay(self) -> None:
        candidates = [
            Path(settings.railradar_replay_file),
            Path("/srv") / settings.railradar_replay_file,
            Path(__file__).resolve().parents[3] / settings.railradar_replay_file,
        ]
        for path in candidates:
            if path.exists():
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                    self._replay = {str(o["number"]): o
                                    for o in raw.get("observations", [])}
                    return
                except Exception as exc:
                    self.last_error = f"replay file unreadable: {exc}"
                    return
        self.last_error = f"replay file not found: {settings.railradar_replay_file}"

    async def _client(self):
        if self._http is None:
            import httpx
            self._http = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=settings.railradar_timeout_seconds,
                headers={"Authorization": f"Bearer {self.api_key}",
                         "Accept": "application/json"},
            )
        return self._http

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    # --------------------------------------------------------------- fetching
    async def live_status(self, number: str, now: float) -> Observation | None:
        """Latest reading for a train number, honouring cache and budget."""
        if self.mode == "off":
            return None

        cached = self._cache.get(number)
        if cached and now - cached[0] < settings.railradar_cache_ttl_seconds:
            return cached[1]

        if self.mode == "replay":
            raw = self._replay.get(number)
            if raw is None:
                return None
            obs = Observation(
                number=number,
                lateness_sec=float(raw.get("latenessSec", 0.0)),
                last_station=str(raw.get("lastStation", "")),
                observed_at=str(raw.get("observedAt", "")),
                source="replay",
                status=str(raw.get("status", "running")),
                is_live=bool(raw.get("isLive", True)))
            self._cache[number] = (now, obs)
            return obs

        try:
            await self.budget.spend(1)
        except BudgetExhausted as exc:
            self.last_error = str(exc)
            return None

        try:
            client = await self._client()
            response = await client.get(f"/v1/trains/{number}/live")
            response.raise_for_status()
            obs = self._parse(number, response.json())
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            logger.warning("RailRadar lookup for %s failed: %s", number, exc)
            return None

        if obs is not None:
            self._cache[number] = (now, obs)
        return obs

    @staticmethod
    def _parse(number: str, payload: dict) -> Observation | None:
        """Map a RailRadar response onto an Observation.

        The live shape, confirmed against the API, is::

            {"success": true, "data": {
                "delayMinutes": 0, "status": "not-started", "isLive": true,
                "lastUpdatedAt": "2026-08-21T01:36:36+05:30",
                "currentLocation": {"stationCode": "NZM", "status": "at-station",
                                    "delayMinutes": 0, ...},
                "route": [...], "train": {...}}}

        Field names are still read defensively - this is a third-party feed, and
        a rename upstream must degrade to "no observation" rather than to a
        confidently wrong one.
        """
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        if not isinstance(data, dict):
            return None
        location = data.get("currentLocation")
        location = location if isinstance(location, dict) else {}

        minutes = None
        for source in (data, location):
            for key in ("delayMinutes", "delay_minutes", "delay", "currentDelay"):
                value = source.get(key)
                if isinstance(value, (int, float)):
                    minutes = float(value)
                    break
            if minutes is not None:
                break
        if minutes is None:
            return None

        station = ""
        for source in (location, data):
            for key in ("stationCode", "lastStation", "currentStation", "last_station"):
                value = source.get(key)
                if isinstance(value, str) and value:
                    station = value
                    break
                if isinstance(value, dict) and value.get("code"):
                    station = str(value["code"])
                    break
            if station:
                break

        observed_at = data.get("lastUpdatedAt")
        if not isinstance(observed_at, str) or not observed_at:
            observed_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

        # `currentLocation.status` is the movement's own state; `data.status` is
        # the service-level one. Either tells us whether the reading is usable.
        status = str(data.get("status") or location.get("status") or "unknown")
        return Observation(
            number=number,
            lateness_sec=minutes * 60.0,
            last_station=station,
            observed_at=observed_at,
            source="live",
            status=status,
            is_live=bool(data.get("isLive", True)))

    async def station_board(self) -> list[dict]:
        """Every scheduled halt at the station - one call, used to backfill the
        long-distance rows the supplied PDF export was missing."""
        if self.mode != "live":
            return []
        try:
            await self.budget.spend(1)
            client = await self._client()
            response = await client.get(
                f"/v1/stations/{settings.railradar_station}/trains")
            response.raise_for_status()
            payload = response.json()
            rows = payload.get("data", payload)
            return rows if isinstance(rows, list) else rows.get("trains", [])
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return []

    async def status(self) -> dict:
        return {
            "mode": self.mode,
            "hasKey": bool(self.api_key),
            "budgetLimit": self.budget.limit,
            "budgetUsed": await self.budget.used(),
            "budgetRemaining": await self.budget.remaining(),
            "cached": len(self._cache),
            "replayEntries": len(self._replay),
            "lastError": self.last_error,
            "coverage": "passenger services only; goods movements are synthetic",
        }
