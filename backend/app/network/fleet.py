"""The 10-train Vasai Road fleet. Port of the fleet in src/twin/scenario.ts."""
from __future__ import annotations

from dataclasses import dataclass, field

from .geometry import P, Point


@dataclass(frozen=True)
class FleetEntry:
    id: str
    number: str
    name: str
    type: str            # EXPRESS | PASSENGER | LOCAL | MEMU | FREIGHT | SHUNT
    priority: int        # 1 = highest
    route_id: str
    origin: str
    destination: str
    entry_delay_min: float
    start: Point
    nominal_speed_kmh: float
    alternate_route_ids: list[str] = field(default_factory=list)
    alternate_platform_ids: list[str] = field(default_factory=list)


fleet: list[FleetEntry] = [
    FleetEntry("E-12928", "12928", "Surat — Panvel Express", "EXPRESS", 1, "RT-EXP-DIVA",
               "Surat", "Panvel via Diva", 4, P(1020, 620), 80, [], []),
    FleetEntry("F-4271", "4271", "Goods — BCN rake", "FREIGHT", 5, "RT-FRT-DIVA",
               "Vasai Road Goods", "Kalyan via Diva", 12, P(930, 735), 40, ["RT-FRT-LOOP"], []),
    FleetEntry("M-90701", "90701", "Diva — Vasai Road MEMU", "MEMU", 3, "RT-MEMU-IN",
               "Diva Jn", "Virar", 2, P(600, 690), 50, [], []),
    FleetEntry("L-90312", "90312", "Virar — Churchgate Slow", "LOCAL", 2, "RT-SLOW-DN",
               "Virar", "Churchgate", 1, P(1450, 210), 65, [], []),
    FleetEntry("L-90455", "90455", "Churchgate — Virar Slow", "LOCAL", 2, "RT-SLOW-UP",
               "Churchgate", "Virar", 0, P(180, 283), 65, [], []),
    FleetEntry("K-91007", "91007", "Dahanu Road — Churchgate Fast", "LOCAL", 2, "RT-FAST-DN",
               "Dahanu Road", "Churchgate", 3, P(1300, 353), 85, [], []),
    FleetEntry("K-91120", "91120", "Churchgate — Dahanu Road Fast", "LOCAL", 2, "RT-FAST-UP",
               "Churchgate", "Dahanu Road", 0, P(90, 426), 85, [], []),
    FleetEntry("T-12951", "12951", "Surat — Mumbai Central Express", "EXPRESS", 1, "RT-THRU-DN",
               "Surat", "Mumbai Central", 0, P(1560, 496), 95, [], ["PF3"]),
    FleetEntry("T-19024", "19024", "Mumbai Central — Surat Express", "PASSENGER", 2, "RT-THRU-UP",
               "Mumbai Central", "Surat", 6, P(60, 545), 90, [], []),
    FleetEntry("S-VR01", "VR01", "North yard shunt", "SHUNT", 6, "RT-SHUNT",
               "North yard", "North yard", 0, P(1480, 110), 15, [], []),
]

fleet_by_id: dict[str, FleetEntry] = {f.id: f for f in fleet}
