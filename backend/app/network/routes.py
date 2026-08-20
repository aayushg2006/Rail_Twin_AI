"""Build a physical route through Vasai Road Jn for every booked service.

A route is what the train actually does on the ground:

    arrive on one corridor, over a particular running line
      -> stand at a platform face (or run through / take the goods chord)
      -> depart on another corridor, over another running line

Line selection follows the operating rules of the section, and the booked
platform from the timetable overrides them when it is published - a slow local
uses the slow road, a fast local the fast road, long distance the through roads,
and anything touching the branch works over the branch roads via PF6/PF7.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from .chainage import Leg, Path, Position, path_of
from .net import (MODELLED_REACH_M, Resource, corridor_lines, lines,
                  lines_serving, platforms, resources)

# Direction of travel over the Western main. A movement towards Mumbai is Down,
# towards Virar is Up; the branch has its own pair.
DOWN_BY_CLASS = {"SLOW": "SLD", "FAST": "FSD", "THROUGH": "THD"}
UP_BY_CLASS = {"SLOW": "SLU", "FAST": "FSU", "THROUGH": "THU"}

# Which road class a service is entitled to.
ROAD_CLASS = {
    "LOCAL_SLOW": "SLOW", "LOCAL_SEMIFAST": "SLOW", "LOCAL_FAST": "FAST",
    "LOCAL_AC": "FAST", "SUBURBAN": "FAST",
    "PREMIUM": "THROUGH", "SUPERFAST": "THROUGH", "MAIL_EXPRESS": "THROUGH",
    "PASSENGER": "THROUGH",
}

FREIGHT_CLASSES = {"FREIGHT", "FREIGHT_EMPTY", "PREMIUM_FREIGHT", "SHUNT"}


@dataclass(frozen=True)
class RouteStop:
    s: float
    platform_id: str
    dwell_sec: float


@dataclass(frozen=True)
class ResourceUse:
    resource_id: str
    enter_s: float
    exit_s: float

    @property
    def length_m(self) -> float:
        return self.exit_s - self.enter_s


@dataclass(frozen=True)
class RailRoute:
    id: str
    label: str
    path: Path
    leg_lines: tuple[str, ...]          # running line per leg, index-aligned
    arrival_corridor: str
    departure_corridor: str
    platform_id: str | None
    stops: tuple[RouteStop, ...]
    uses: tuple[ResourceUse, ...]

    @property
    def length_m(self) -> float:
        return self.path.length_m

    def position_at(self, s: float) -> Position:
        return self.path.position_at(s)

    def leg_at(self, s: float) -> Leg | None:
        """The leg the train is on at distance `s`, which carries its direction."""
        run = 0.0
        for leg in self.path.legs:
            if s <= run + leg.length_m:
                return leg
            run += leg.length_m
        return self.path.legs[-1] if self.path.legs else None

    def line_at(self, s: float) -> str:
        run = 0.0
        for leg, line_id in zip(self.path.legs, self.leg_lines):
            if s <= run + leg.length_m:
                return line_id
            run += leg.length_m
        return self.leg_lines[-1] if self.leg_lines else ""


def _road(service_class: str) -> str:
    return ROAD_CLASS.get(service_class, "THROUGH")


def _line_for(corridor: str, towards_station: bool, arrival: str, departure: str,
              road: str, freight: bool, platform_id: str | None) -> str:
    """Pick the running line for one leg."""
    if corridor == "YARD":
        return "GYL"
    if corridor == "DIVA":
        # Freight between the north end and the branch keeps to the goods chord.
        if freight and "NORTH" in (arrival, departure):
            return "GDC"
        return "BRU" if towards_station else "BRD"
    if freight and corridor == "NORTH" and "DIVA" in (arrival, departure):
        return "GDC"

    # Western main: Down runs towards Mumbai, Up towards Virar.
    if towards_station:
        down = arrival == "NORTH"
    else:
        down = departure == "SOUTH"
    table = DOWN_BY_CLASS if down else UP_BY_CLASS
    line_id = table[road]

    # A published platform outranks the class rule, provided a line serving that
    # platform runs on this corridor in this direction.
    if platform_id:
        candidates = [
            lid for lid in lines_serving(platform_id)
            if lid in corridor_lines.get(corridor, ())
            and lines[lid].direction in (
                ("DOWN", "BRANCH_OUT", "BOTH") if down else ("UP", "BRANCH_IN", "BOTH"))
        ]
        if candidates:
            line_id = candidates[0]
    return line_id


def _uses_for_leg(path: Path, leg_index: int, line_id: str) -> list[ResourceUse]:
    leg = path.legs[leg_index]
    base = sum(l.length_m for l in path.legs[:leg_index])
    out: list[ResourceUse] = []
    for res in resources.values():
        if res.kind == "PLATFORM" or line_id not in res.lines:
            continue
        if res.corridor != leg.corridor:
            continue
        span = leg.overlap(res.corridor, res.from_m, res.to_m)
        if span is None:
            continue
        out.append(ResourceUse(res.id, base + span[0], base + span[1]))
    return out


def build_route(route_id: str, arrival: str, departure: str, service_class: str,
                platform_id: str | None, dwell_sec: float,
                reach_m: float = MODELLED_REACH_M) -> RailRoute:
    freight = service_class in FREIGHT_CLASSES
    road = _road(service_class)

    if arrival == departure == "YARD":
        # A shunt runs out along the yard neck and back; it never leaves the yard.
        legs = [Leg("YARD", 0.0, 1800.0), Leg("YARD", 1800.0, 0.0)]
        leg_lines = ["GYL", "GYL"]
    else:
        in_reach = 1800.0 if arrival == "YARD" else reach_m
        out_reach = 1800.0 if departure == "YARD" else reach_m
        legs = [Leg(arrival, in_reach, 0.0), Leg(departure, 0.0, out_reach)]
        leg_lines = [
            _line_for(arrival, True, arrival, departure, road, freight, platform_id),
            _line_for(departure, False, arrival, departure, road, freight, platform_id),
        ]

    path = path_of(legs)
    leg_lines = leg_lines[:len(path.legs)]

    # The platform a train actually stands at is the one served by its departure
    # line, unless the timetable published a face.
    stop_platform: str | None = None
    if not freight:
        stop_platform = platform_id
        if stop_platform not in platforms:
            stop_platform = None
        if stop_platform is None and leg_lines:
            stop_platform = lines[leg_lines[-1]].platform_id

    uses: list[ResourceUse] = []
    for i, line_id in enumerate(leg_lines):
        uses.extend(_uses_for_leg(path, i, line_id))

    stops: list[RouteStop] = []
    if stop_platform:
        centre = path.s_at(departure, 0.0)
        if centre is None:
            centre = path.length_m / 2
        half = platforms[stop_platform].length_m / 2
        stops.append(RouteStop(s=centre, platform_id=stop_platform, dwell_sec=dwell_sec))
        uses.append(ResourceUse(stop_platform, max(0.0, centre - half), centre + half))

    uses.sort(key=lambda u: u.enter_s)
    label = f"{arrival} - {stop_platform or 'through'} - {departure}"
    return RailRoute(
        id=route_id, label=label, path=path, leg_lines=tuple(leg_lines),
        arrival_corridor=arrival, departure_corridor=departure,
        platform_id=stop_platform, stops=tuple(stops), uses=tuple(uses),
    )


@lru_cache(maxsize=None)
def route_template(arrival: str, departure: str, service_class: str,
                   platform_id: str | None, dwell_sec: float) -> RailRoute:
    """Routes repeat heavily across 680 services, so build each shape once."""
    rid = f"RT-{arrival}-{departure}-{service_class}-{platform_id or 'NA'}"
    return build_route(rid, arrival, departure, service_class, platform_id, dwell_sec)


def alternate_platforms(route: RailRoute) -> list[str]:
    """Other faces that could take this movement - candidates for re-platforming."""
    if route.platform_id is None:
        return []
    out: list[str] = []
    for line_id in route.leg_lines:
        for pid, pf in platforms.items():
            if pid == route.platform_id:
                continue
            if line_id in pf.serves:
                out.append(pid)
    # Same-direction faces on the same road class are also usable.
    current = platforms[route.platform_id]
    for pid, pf in platforms.items():
        if pid != route.platform_id and pf.side == current.side and pid not in out:
            out.append(pid)
    return out
