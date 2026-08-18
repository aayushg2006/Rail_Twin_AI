"""Fleet projection from the canonical Vasai Road data pack."""
from __future__ import annotations

from dataclasses import dataclass, field

from .data import data_pack
from .geometry import P, Point


# Economic weight = relative revenue/importance the optimizer protects. Higher
# means a delay to this movement costs the network more. Used as the fallback
# when a service omits an explicit `economicWeight` in the data pack. Freight is
# a genuine profit source on Indian Railways, so ordinary freight outranks a
# shunt and premium (container/POL/parcel) freight can outrank suburban locals.
DEFAULT_CLASS_WEIGHTS: dict[str, float] = {
    "EXPRESS": 5.0,
    "PASSENGER": 4.0,
    "LOCAL": 3.0,
    "MEMU": 3.0,
    "FREIGHT": 2.0,
    "SHUNT": 0.5,
}


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
    economic_weight: float = 1.0
    service_class: str = ""
    alternate_route_ids: list[str] = field(default_factory=list)
    alternate_platform_ids: list[str] = field(default_factory=list)
    scheduled_departure_sec: int = 0
    operating_days: str = "SMTWTFS"
    snapshot_date: str = ""
    source: str = ""
    provenance: str = ""


def _point(raw: dict) -> Point:
    return P(float(raw["x"]), float(raw["y"]))


def _weight(s: dict) -> float:
    if s.get("economicWeight") is not None:
        return float(s["economicWeight"])
    return DEFAULT_CLASS_WEIGHTS.get(str(s.get("type", "")), 1.0)


fleet: list[FleetEntry] = [
    FleetEntry(
        id=s["id"], number=s["number"], name=s["name"], type=s["type"],
        priority=int(s["priority"]), route_id=s["routeId"], origin=s["origin"],
        destination=s["destination"], entry_delay_min=float(s["entryDelayMin"]),
        start=_point(s["start"]), nominal_speed_kmh=float(s["nominalSpeedKmh"]),
        economic_weight=_weight(s), service_class=str(s.get("serviceClass", "")),
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


def economic_weight(train_id: str) -> float:
    """Revenue/importance weight for a train; 1.0 for unknown ids."""
    f = fleet_by_id.get(train_id)
    return f.economic_weight if f else 1.0
