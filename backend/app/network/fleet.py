"""The fleet: every booked service and goods path through Vasai Road Jn.

Loaded from the two generated packs - `timetable-bsr.json` (530 published
passenger services parsed from the India Rail Info exports) and
`freight-paths.json` (150 synthetic goods paths). The old hand-written 25-train
list is gone, and with it `entryDelayMin`: nothing in the data can inject a
delay any more, so every delay the console reports is either an observation or
something the simulation produced.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property

from ..twin.dynamics import Traction, build_profile, traction_for
from .net import freight_pack, timetable_pack
from .routes import RailRoute, route_template

DAY_INDEX = "SMTWTFS"          # index 0 = Sunday, matching operatingDays


@dataclass(frozen=True)
class FleetEntry:
    id: str
    number: str
    name: str
    category: str               # EXPRESS | LOCAL | MEMU | PASSENGER | FREIGHT | SHUNT
    service_class: str
    priority: int
    economic_weight: float
    typical_load: int           # passengers on board; 0 for freight
    line_speed_kmh: float
    dwell_sec: float
    booked_dep_sec: int
    booked_platform: str | None
    operating_days: str
    arrival_corridor: str
    departure_corridor: str
    corridor_confidence: str
    origin: str
    destination: str
    gross_tonnes: float
    source: str
    provenance: str

    @property
    def is_freight(self) -> bool:
        return self.category in ("FREIGHT", "SHUNT")

    @property
    def traction(self) -> Traction:
        return traction_for(self.service_class)

    @cached_property
    def route(self) -> RailRoute:
        return route_template(self.arrival_corridor, self.departure_corridor,
                              self.service_class, self.booked_platform, self.dwell_sec)

    @cached_property
    def approach_sec(self) -> float:
        """Run time over the arrival leg, entering at line speed and stopping
        (or running through, for freight) at the platform line."""
        route = self.route
        if not route.path.legs:
            return 0.0
        leg = route.path.legs[0]
        v_line = self.line_speed_kmh * 1000 / 3600
        v_exit = 0.0 if route.platform_id else v_line * 0.6
        return build_profile(leg.length_m, v_line, v_line, v_exit, self.traction).duration

    @cached_property
    def depart_sec(self) -> float:
        """Run time over the departure leg, from the platform to the far boundary."""
        route = self.route
        if len(route.path.legs) < 2:
            return 0.0
        leg = route.path.legs[-1]
        v_line = self.line_speed_kmh * 1000 / 3600
        v_start = 0.0 if route.platform_id else v_line * 0.6
        return build_profile(leg.length_m, v_start, v_line, v_line, self.traction).duration

    @cached_property
    def free_run_sec(self) -> float:
        """Total time to work the whole modelled route with no contention."""
        return self.approach_sec + self.dwell_sec + self.depart_sec

    @property
    def entry_sec(self) -> float:
        """When this service must enter the modelled area to keep its booked time."""
        return self.booked_dep_sec - self.approach_sec - self.dwell_sec

    @property
    def clear_sec(self) -> float:
        """When it clears the far boundary if it runs to time."""
        return self.entry_sec + self.free_run_sec

    def runs_on(self, weekday_sun0: int) -> bool:
        days = self.operating_days
        if len(days) != 7:
            return True
        return days[weekday_sun0 % 7] != "."

    def platform_stop_s(self) -> float | None:
        stops = self.route.stops
        return stops[0].s if stops else None


def _entry(raw: dict) -> FleetEntry:
    platform = raw.get("bookedPlatform")
    platform_id = f"PF{platform}" if isinstance(platform, int) else platform
    return FleetEntry(
        id=raw["id"], number=raw["number"], name=raw["name"],
        category=raw["category"], service_class=raw["serviceClass"],
        priority=int(raw["priority"]), economic_weight=float(raw["economicWeight"]),
        typical_load=int(raw.get("typicalLoad", 0)),
        line_speed_kmh=float(raw["lineSpeedKmh"]), dwell_sec=float(raw["dwellSec"]),
        booked_dep_sec=int(raw["bookedDepSec"]), booked_platform=platform_id,
        operating_days=raw["operatingDays"],
        arrival_corridor=raw["arrivalCorridor"],
        departure_corridor=raw["departureCorridor"],
        corridor_confidence=raw.get("corridorConfidence", "HIGH"),
        origin=str(raw.get("origin", "")), destination=str(raw.get("destination", "")),
        gross_tonnes=float(raw.get("grossTonnes", 0) or 0),
        source=raw.get("source", ""), provenance=raw.get("provenance", ""),
    )


fleet: list[FleetEntry] = [
    *(_entry(s) for s in timetable_pack["services"]),
    *(_entry(p) for p in freight_pack["paths"]),
]
fleet.sort(key=lambda f: (f.booked_dep_sec, f.number))
fleet_by_id: dict[str, FleetEntry] = {f.id: f for f in fleet}


def economic_weight(train_id: str) -> float:
    f = fleet_by_id.get(train_id)
    return f.economic_weight if f else 1.0


def passenger_load(train_id: str) -> int:
    f = fleet_by_id.get(train_id)
    return f.typical_load if f else 0


def active_window(centre_sec: float, before_sec: float, after_sec: float,
                  weekday_sun0: int | None = None) -> list[FleetEntry]:
    """Services whose modelled run overlaps [centre-before, centre+after].

    A service is in the window from the moment it enters the approach area until
    it has cleared the far side, so the twin only ever holds the few dozen trains
    actually on the ground rather than all 680 of them.
    """
    lo = centre_sec - before_sec
    hi = centre_sec + after_sec
    out: list[FleetEntry] = []
    for f in fleet:
        if weekday_sun0 is not None and not f.runs_on(weekday_sun0):
            continue
        if f.clear_sec >= lo and f.entry_sec <= hi:
            out.append(f)
    return out
