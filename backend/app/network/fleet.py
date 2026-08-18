"""Fleet projection from the canonical Vasai Road data pack."""
from __future__ import annotations

from dataclasses import dataclass, field

from .data import data_pack
from .geometry import P, Point


@dataclass(frozen=True)
class FleetEntry:
    id: str
    number: str
    name: str
    type: str
    priority: int
    route_id: str
    origin: str
    destination: str
    entry_delay_min: float
    start: Point
    nominal_speed_kmh: float
    alternate_route_ids: list[str] = field(default_factory=list)
    alternate_platform_ids: list[str] = field(default_factory=list)
    scheduled_departure_sec: int = 0
    operating_days: str = "SMTWTFS"
    snapshot_date: str = ""
    source: str = ""
    provenance: str = ""


def _point(raw: dict) -> Point:
    return P(float(raw["x"]), float(raw["y"]))


fleet: list[FleetEntry] = [
    FleetEntry(
        id=s["id"], number=s["number"], name=s["name"], type=s["type"],
        priority=int(s["priority"]), route_id=s["routeId"], origin=s["origin"],
        destination=s["destination"], entry_delay_min=float(s["entryDelayMin"]),
        start=_point(s["start"]), nominal_speed_kmh=float(s["nominalSpeedKmh"]),
        alternate_route_ids=list(s.get("alternateRouteIds", [])),
        alternate_platform_ids=list(s.get("alternatePlatformIds", [])),
        scheduled_departure_sec=int(s.get("scheduledDepartureSec", 0)),
        operating_days=s.get("operatingDays", "SMTWTFS"),
        snapshot_date=s.get("snapshotDate", ""), source=s.get("source", ""),
        provenance=s.get("provenance", ""),
    )
    for s in data_pack["services"]
]

fleet_by_id: dict[str, FleetEntry] = {f.id: f for f in fleet}
