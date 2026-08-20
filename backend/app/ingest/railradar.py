"""RailRadar client - live running status for Vasai Road.

Three things about this feed shape the design, all confirmed against the live API.

1. **The station board is the primary source.** One call to
   `/v1/stations/BSR/live` returns every train due at Vasai Road in the window -
   53 of them in a typical hour - each with its scheduled time, distance from
   origin and a live delay. Polling individual train numbers, which is what this
   client used to do, cost one request per train for a fraction of the data.

2. **`/legacy/trains/live-map` is useless here.** It carries 1,725 running trains
   nationwide but only 5 EMUs, and Mumbai suburban services are absent
   altogether. Vasai Road is 47/53 suburban, so that endpoint is not used.

3. **The per-train endpoint is where the detail lives.** `/v1/trains/{n}/live`
   returns a `route[]` with the actual platform, actual vs scheduled times and
   `speedToNextStationKmph` for each stop. That is worth a request for the
   handful of services actually inside the modelled section.

The free tier allows 1,000 requests a MONTH, so the budget is reserved before
every call and the poll rate degrades as the allowance runs down rather than
failing in the middle of a demonstration.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from ..config import settings

logger = logging.getLogger("railtwin.railradar")

STATION_BOARD = "/v1/stations/{code}/live"
TRAIN_LIVE = "/v1/trains/{number}/live"


@dataclass(frozen=True)
class Observation:
    """One live reading for a train number."""
    number: str
    lateness_sec: float
    last_station: str
    observed_at: str
    source: str                 # board | detail | replay | cache
    status: str = "unknown"     # not-started | upcoming | at-station | running | departed
    is_live: bool = True
    name: str = ""
    train_type: str = ""
    origin: str = ""
    destination: str = ""
    # From the station board: the booked time at Vasai Road and its distance
    # from the train's origin, which is what makes this a timetable and not just
    # a delay feed.
    scheduled_arrival: str = ""
    scheduled_departure: str = ""
    platform: str | None = None
    distance_km: float | None = None
    run_days: tuple[str, ...] = ()
    # From the per-train detail: real running speed on the section ahead.
    speed_to_next_kmph: float | None = None

    @property
    def running(self) -> bool:
        """Only a train that has actually started carries a meaningful delay.

        A service reported `not-started` has delayMinutes 0 because it has not
        left its origin yet. Assimilating that as "on time" would overwrite
        whatever the twin had legitimately inferred.
        """
        return self.status not in ("not-started", "cancelled", "completed", "scheduled")

    def as_dict(self) -> dict:
        return {
            "number": self.number,
            "name": self.name,
            "type": self.train_type,
            "latenessSec": round(self.lateness_sec, 1),
            "lastStation": self.last_station,
            "observedAt": self.observed_at,
            "source": self.source,
            "status": self.status,
            "isLive": self.is_live,
            "usable": self.running,
            "scheduledArrival": self.scheduled_arrival,
            "scheduledDeparture": self.scheduled_departure,
            "platform": self.platform,
            "distanceKm": self.distance_km,
            "speedToNextKmph": self.speed_to_next_kmph,
            "origin": self.origin,
            "destination": self.destination,
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
                self._redis = aioredis.from_url(self._redis_url, decode_responses=True)
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
        """Reserve `n` calls, or refuse. Counted BEFORE the request is made, so a
        crash mid-flight cannot silently overspend the allowance."""
        client = await self._client()
        if client is None:
            if self._local + n > self.limit:
                raise BudgetExhausted(f"monthly budget of {self.limit} is spent")
            self._local += n
            return
        try:
            total = int(await client.incrby(self._key(), n))
            await client.expire(self._key(), 60 * 60 * 24 * 40)
        except Exception:
            self._local += n
            return
        if total > self.limit:
            raise BudgetExhausted(f"monthly budget of {self.limit} is spent")


class RailRadarClient:
    def __init__(self, mode: str | None = None, api_key: str | None = None):
        self.mode = (mode or settings.railradar_mode).lower()
        self.api_key = api_key if api_key is not None else settings.railradar_api_key
        self.base_url = settings.railradar_base_url.rstrip("/")
        self.budget = RequestBudget(settings.railradar_monthly_budget, settings.redis_url)
        self._replay: dict[str, dict] = {}
        self._http = None
        self.last_error: str = ""
        self.calls_made = 0

        if self.mode == "live" and not self.api_key:
            self.mode = "off"
            self.last_error = "RAILTWIN_RAILRADAR_API_KEY is not set; live mode disabled"
        if self.mode == "replay":
            self._load_replay()

    # ------------------------------------------------------------------ setup
    def _load_replay(self) -> None:
        for path in (Path(settings.railradar_replay_file),
                     Path("/srv") / settings.railradar_replay_file,
                     Path(__file__).resolve().parents[3] / settings.railradar_replay_file):
            if path.exists():
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                    self._replay = {str(o["number"]): o for o in raw.get("observations", [])}
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

    async def _get(self, path: str) -> dict | None:
        try:
            await self.budget.spend(1)
        except BudgetExhausted as exc:
            self.last_error = str(exc)
            return None
        try:
            client = await self._client()
            response = await client.get(path)
            response.raise_for_status()
            self.calls_made += 1
            self.last_error = ""
            return response.json()
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            logger.warning("RailRadar %s failed: %s", path, exc)
            return None

    # ------------------------------------------------------------ station board
    async def station_board(self) -> list[Observation]:
        """Every train due at the station, with its live delay. One request."""
        if self.mode == "off":
            return []
        if self.mode == "replay":
            return [self._from_replay(number) for number in self._replay]

        payload = await self._get(STATION_BOARD.format(code=settings.railradar_station))
        if not payload:
            return []
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        rows = data.get("trains") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            return []
        now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        out: list[Observation] = []
        for row in rows:
            obs = self._from_board_row(row, now)
            if obs is not None:
                out.append(obs)
        return out

    @staticmethod
    def _from_board_row(row: dict, fallback_time: str) -> Observation | None:
        """Map one station-board entry. Shape confirmed against the API:

            {"train": {"number","name","type","source","destination","runDays"},
             "stop":  {"arrival","departure","distance","platform","isHalt"},
             "live":  {"type","delayMinutes","expectedArrivalTime", ...}}
        """
        if not isinstance(row, dict):
            return None
        train = row.get("train") or {}
        stop = row.get("stop") or {}
        live = row.get("live") or {}
        number = str(train.get("number") or "").strip()
        if not number:
            return None
        minutes = live.get("delayMinutes")
        if not isinstance(minutes, (int, float)):
            minutes = 0.0
        platform = stop.get("platform")
        return Observation(
            number=number,
            lateness_sec=float(minutes) * 60.0,
            last_station=str(live.get("stationCode") or ""),
            observed_at=str(live.get("expectedArrivalTime") or fallback_time),
            source="board",
            status=str(live.get("type") or "unknown"),
            is_live=bool(live),
            name=str(train.get("name") or ""),
            train_type=str(train.get("type") or ""),
            origin=str(train.get("source") or ""),
            destination=str(train.get("destination") or ""),
            scheduled_arrival=str(stop.get("arrival") or ""),
            scheduled_departure=str(stop.get("departure") or ""),
            platform=str(platform) if platform not in (None, "") else None,
            distance_km=(float(stop["distance"])
                         if isinstance(stop.get("distance"), (int, float)) else None),
            run_days=tuple(train.get("runDays") or ()),
        )

    # --------------------------------------------------------- per-train detail
    async def train_detail(self, number: str, station: str | None = None) -> Observation | None:
        """Full running status, including the real platform and section speed."""
        if self.mode == "off":
            return None
        if self.mode == "replay":
            return self._from_replay(number) if number in self._replay else None

        payload = await self._get(TRAIN_LIVE.format(number=number))
        if not payload:
            return None
        return self._from_detail(number, payload, station or settings.railradar_station)

    @staticmethod
    def _from_detail(number: str, payload: dict, station: str) -> Observation | None:
        """Map `/v1/trains/{n}/live`, pulling the station's own row out of route[]."""
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

        # The station's own stop carries the platform actually used and the
        # booked times - neither of which the board reports for suburban trains.
        here: dict = {}
        for stop in (data.get("route") or []):
            if isinstance(stop, dict) and str(stop.get("stationCode") or "") == station:
                here = stop
                break

        # Where the train is. Read defensively across both the location block and
        # the top level: this is a third-party feed, and a rename upstream must
        # degrade to "unknown station", never to a confidently wrong one.
        station_code = ""
        for source in (location, data):
            for key in ("stationCode", "lastStation", "currentStation", "last_station"):
                value = source.get(key)
                if isinstance(value, str) and value:
                    station_code = value
                    break
                if isinstance(value, dict) and value.get("code"):
                    station_code = str(value["code"])
                    break
            if station_code:
                break

        train = data.get("train") or {}
        platform = here.get("platform")
        speed = here.get("speedToNextStationKmph")
        observed_at = data.get("lastUpdatedAt")
        if not isinstance(observed_at, str) or not observed_at:
            observed_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

        return Observation(
            number=number,
            lateness_sec=float(minutes) * 60.0,
            last_station=station_code,
            observed_at=observed_at,
            source="detail",
            status=str(data.get("status") or location.get("status") or "unknown"),
            is_live=bool(data.get("isLive", True)),
            name=str(data.get("trainName") or train.get("name") or ""),
            train_type=str(train.get("type") or ""),
            origin=str((train.get("source") or {}).get("code") or ""),
            destination=str((train.get("destination") or {}).get("code") or ""),
            scheduled_arrival=str(here.get("scheduledArrival") or ""),
            scheduled_departure=str(here.get("scheduledDeparture") or ""),
            platform=str(platform) if platform not in (None, "") else None,
            distance_km=(float(here["distance"])
                         if isinstance(here.get("distance"), (int, float)) else None),
            speed_to_next_kmph=float(speed) if isinstance(speed, (int, float)) else None,
        )

    # ---------------------------------------------------------------- replay
    def _from_replay(self, number: str) -> Observation:
        raw = self._replay.get(number, {})
        return Observation(
            number=number,
            lateness_sec=float(raw.get("latenessSec", 0.0)),
            last_station=str(raw.get("lastStation", "")),
            observed_at=str(raw.get("observedAt", "")),
            source="replay",
            status=str(raw.get("status", "running")),
            is_live=bool(raw.get("isLive", True)),
            name=str(raw.get("name", "")),
            platform=raw.get("platform"),
        )

    async def status(self) -> dict:
        remaining = await self.budget.remaining()
        return {
            "mode": self.mode,
            "hasKey": bool(self.api_key),
            "budgetLimit": self.budget.limit,
            "budgetUsed": await self.budget.used(),
            "budgetRemaining": remaining,
            "callsThisSession": self.calls_made,
            "replayEntries": len(self._replay),
            "lastError": self.last_error,
            "coverage": ("Passenger services only. Goods movements have no public "
                         "live feed anywhere and remain synthetic."),
        }
