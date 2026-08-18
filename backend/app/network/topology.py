"""Vasai Road Junction — operational topology. Port of src/twin/topology.ts.

Along-track axis is X (left = Churchgate/Mumbai, right = Dahanu/Surat), cross-track
axis is Y (top = WEST, bottom = EAST). PF1-PF5 are Western line faces; PF6/PF7 the
eastern island working the long-distance expresses and the Vasai Road - Diva branch.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .geometry import P, Point, crossing_distance

MAP_W = 1620
MAP_H = 940

STATION_X0 = 480
STATION_X1 = 1080
PLATFORM_X0 = 505
PLATFORM_X1 = 1055
STOP_X = 790  # nominal stopping point of a train on any platform

LINE_Y = {
    "WSD": 210, "WSU": 283, "WFD": 353, "WFU": 426,
    "THD": 496, "THU": 545, "EX1": 620, "EX2": 690, "FRC": 735,
}


# ---------------------------------------------------------------- dataclasses
@dataclass(frozen=True)
class Track:
    id: str
    name: str
    kind: str          # WESTERN_SLOW | WESTERN_FAST | DIVA_BRANCH | FREIGHT_CHORD | YARD
    direction: str     # UP | DOWN | BRANCH_OUT | BRANCH_IN
    path: list[Point]
    through: bool = False


@dataclass(frozen=True)
class Platform:
    id: str
    label: str
    x: float
    y: float
    w: float
    h: float
    serves: list[str]
    usage: str
    side: str          # WEST | ISLAND | EAST


@dataclass(frozen=True)
class Resource:
    id: str
    label: str
    kind: str          # JUNCTION | PLATFORM | BLOCK
    at: Point
    radius: float
    headway_sec: float
    capacity: int


@dataclass(frozen=True)
class SignalPost:
    id: str
    at: Point
    facing: float
    aspect: str        # GREEN | YELLOW | RED


@dataclass(frozen=True)
class Junction:
    id: str
    label: str
    at: Point


@dataclass(frozen=True)
class StationTick:
    name: str
    at: Point
    km: float
    corridor: str      # NORTH | SOUTH | DIVA | STATION


@dataclass(frozen=True)
class RouteStop:
    s: float
    platform_id: str
    dwell_sec: float


@dataclass(frozen=True)
class RailRoute:
    id: str
    label: str
    path: list[Point]
    tracks: list[str]
    stops: list[RouteStop]
    corridor_from: str
    corridor_to: str


# ---------------------------------------------------------------- shared geometry
BRANCH_OUT = [P(400, 780), P(330, 830), P(250, 862), P(20, 880)]
BRANCH_IN = [P(20, 898), P(250, 880), P(340, 846), P(380, 800)]
EX1_TO_JB = [P(430, 620), P(410, 680), P(400, 730), P(400, 780)]
JB_TO_EX2 = [P(380, 800), P(378, 740), P(392, 706), P(430, 690)]
CHORD_TO_JB = [P(1600, LINE_Y["FRC"]), P(520, LINE_Y["FRC"]), P(450, 752), P(410, 770), P(400, 780)]


tracks: list[Track] = [
    Track("T-WSD", "Western Slow DN", "WESTERN_SLOW", "DOWN", [P(1600, LINE_Y["WSD"]), P(20, LINE_Y["WSD"])]),
    Track("T-WSU", "Western Slow UP", "WESTERN_SLOW", "UP", [P(20, LINE_Y["WSU"]), P(1600, LINE_Y["WSU"])]),
    Track("T-WFD", "Western Fast DN", "WESTERN_FAST", "DOWN", [P(1600, LINE_Y["WFD"]), P(20, LINE_Y["WFD"])]),
    Track("T-WFU", "Western Fast UP", "WESTERN_FAST", "UP", [P(20, LINE_Y["WFU"]), P(1600, LINE_Y["WFU"])]),
    Track("T-THD", "Through DN", "WESTERN_FAST", "DOWN", [P(1600, LINE_Y["THD"]), P(20, LINE_Y["THD"])], through=True),
    Track("T-THU", "Through UP", "WESTERN_FAST", "UP", [P(20, LINE_Y["THU"]), P(1600, LINE_Y["THU"])], through=True),
    Track("T-EX1", "East Line 1 (Diva bound)", "DIVA_BRANCH", "BRANCH_OUT", [P(1600, LINE_Y["EX1"]), *EX1_TO_JB], through=True),
    Track("T-EX2", "East Line 2 (Vasai bound)", "DIVA_BRANCH", "BRANCH_IN", [*JB_TO_EX2, P(1600, LINE_Y["EX2"])], through=True),
    Track("T-BR", "Vasai Road - Diva branch (Diva bound)", "DIVA_BRANCH", "BRANCH_OUT", list(BRANCH_OUT), through=True),
    Track("T-BR2", "Vasai Road - Diva branch (Vasai bound)", "DIVA_BRANCH", "BRANCH_IN", list(BRANCH_IN), through=True),
    Track("T-FRC", "Goods chord", "FREIGHT_CHORD", "BRANCH_OUT", list(CHORD_TO_JB)),
    Track("T-YD1", "North yard road 1", "YARD", "UP", [P(1160, 110), P(1500, 110)]),
    Track("T-YD2", "North yard road 2", "YARD", "UP", [P(1160, 140), P(1500, 140)]),
    Track("T-YDN", "Yard neck", "YARD", "UP", [P(1160, 110), P(1120, 150), P(1120, 200), P(1140, LINE_Y["WSD"])]),
    Track("T-GY1", "Goods yard road 1", "YARD", "BRANCH_OUT", [P(600, 800), P(848, 800)]),
    Track("T-GY2", "Goods yard road 2", "YARD", "BRANCH_OUT", [P(620, 826), P(848, 826)]),
]


def _pf(pid: str, label: str, line: str, usage: str, side: str, serves: list[str]) -> Platform:
    return Platform(pid, label, PLATFORM_X0, LINE_Y[line] - 36, PLATFORM_X1 - PLATFORM_X0, 24, serves, usage, side)


platforms: list[Platform] = [
    _pf("PF1", "PF 1", "WSD", "Slow / Fast DN — Churchgate", "WEST", ["T-WSD"]),
    _pf("PF2", "PF 2", "WSU", "Slow / Fast UP — Virar", "ISLAND", ["T-WSU"]),
    _pf("PF3", "PF 3", "WFD", "Fast DN — Churchgate", "ISLAND", ["T-WFD"]),
    _pf("PF4", "PF 4", "WFU", "Fast UP — Virar / Dahanu", "ISLAND", ["T-WFU"]),
    _pf("PF5", "PF 5", "THD", "Slow / Fast DN — through", "ISLAND", ["T-THD"]),
    _pf("PF6", "PF 6", "EX1", "Express / MEMU — Diva bound", "EAST", ["T-EX1"]),
    _pf("PF7", "PF 7", "EX2", "Express / MEMU — Vasai bound", "EAST", ["T-EX2"]),
]

junctions: list[Junction] = [
    Junction("J1", "J1 North crossover", P(1120, LINE_Y["WFD"])),
    Junction("J2", "J2 South crossover", P(440, LINE_Y["WFD"])),
    Junction("J3", "J3 East approach", P(1120, LINE_Y["EX1"])),
    Junction("JB", "JB Diva branch turnout", P(400, 780)),
    Junction("JY", "JY Yard neck", P(1130, 170)),
]

resources: list[Resource] = [
    Resource("JB", "JB — Diva branch turnout", "JUNCTION", P(400, 780), 40, 180, 1),
    Resource("J3", "J3 — East approach", "JUNCTION", P(1120, LINE_Y["EX1"]), 40, 120, 1),
    Resource("J1", "J1 — North crossover", "JUNCTION", P(1120, LINE_Y["WFD"]), 34, 120, 1),
    Resource("J2", "J2 — South crossover", "JUNCTION", P(440, LINE_Y["WFD"]), 34, 120, 1),
    Resource("BLK-DV1", "Branch block BR-1 (Diva bound)", "BLOCK", P(250, 862), 10, 180, 1),
    Resource("BLK-DV2", "Branch block BR-2 (Vasai bound)", "BLOCK", P(250, 880), 10, 180, 1),
    Resource("BLK-N1", "Block N1 — Vasai / Nalla Sopara", "BLOCK", P(1240, LINE_Y["WFD"]), 40, 90, 1),
    *[
        Resource(p.id, f"{p.label} — platform road", "PLATFORM", P(STOP_X, p.y + 36), 34, 90, 1)
        for p in platforms
    ],
]

signals: list[SignalPost] = [
    SignalPost("S-N1", P(1105, LINE_Y["WSD"] - 14), 180, "GREEN"),
    SignalPost("S-N2", P(1105, LINE_Y["WFD"] - 14), 180, "GREEN"),
    SignalPost("S-N3", P(1105, LINE_Y["EX1"] - 14), 180, "YELLOW"),
    SignalPost("S-S1", P(462, LINE_Y["WSU"] - 14), 0, "GREEN"),
    SignalPost("S-S2", P(462, LINE_Y["WFU"] - 14), 0, "GREEN"),
    SignalPost("S-S3", P(462, LINE_Y["THD"] - 14), 180, "GREEN"),
    SignalPost("S-B1", P(414, 742), 110, "YELLOW"),
    SignalPost("S-B2", P(352, 812), 145, "RED"),
    SignalPost("S-F1", P(640, LINE_Y["FRC"] - 14), 180, "GREEN"),
]

station_ticks: list[StationTick] = [
    StationTick("Nalla Sopara", P(1245, 0), 5.1, "NORTH"),
    StationTick("Virar", P(1420, 0), 12.4, "NORTH"),
    StationTick("Naigaon", P(300, 0), 5.6, "SOUTH"),
    StationTick("Bhayandar", P(140, 0), 13.2, "SOUTH"),
    StationTick("Juchandra", P(340, 825), 6.0, "DIVA"),
    StationTick("Kaman Road", P(288, 848), 11.4, "DIVA"),
    StationTick("Kharbao", P(232, 865), 17.2, "DIVA"),
    StationTick("Bhiwandi Road", P(176, 869), 23.5, "DIVA"),
    StationTick("Kopar", P(120, 873), 31.0, "DIVA"),
    StationTick("Diva Jn", P(64, 877), 36.8, "DIVA"),
]


# ---------------------------------------------------------------- routes
def _stop_at(path: list[Point], y: float) -> float:
    return crossing_distance(path, P(STOP_X, y), 40) or 0.0


def _route(rid: str, label: str, path: list[Point], track_ids: list[str],
           corridor_from: str, corridor_to: str,
           stops: list[tuple[str, float, float]]) -> RailRoute:
    return RailRoute(
        id=rid, label=label, path=path, tracks=track_ids,
        corridor_from=corridor_from, corridor_to=corridor_to,
        stops=[RouteStop(s=_stop_at(path, y), platform_id=pid, dwell_sec=dwell) for pid, y, dwell in stops],
    )


routes: list[RailRoute] = [
    _route("RT-EXP-DIVA", "Surat / Virar → PF 6 → Diva branch",
           [P(1600, LINE_Y["EX1"]), *EX1_TO_JB, *BRANCH_OUT[1:]], ["T-EX1", "T-BR"],
           "NORTH", "DIVA", [("PF6", LINE_Y["EX1"], 60)]),
    _route("RT-FRT-DIVA", "North goods chord → Diva branch",
           [*CHORD_TO_JB, *BRANCH_OUT[1:]], ["T-FRC", "T-BR"], "NORTH", "DIVA", []),
    _route("RT-MEMU-IN", "Diva branch → PF 7 → Virar",
           [*BRANCH_IN, *JB_TO_EX2[1:], P(1600, LINE_Y["EX2"])], ["T-BR2", "T-EX2"],
           "DIVA", "NORTH", [("PF7", LINE_Y["EX2"], 90)]),
    _route("RT-SLOW-DN", "Virar → PF 1 → Churchgate (slow)",
           [P(1600, LINE_Y["WSD"]), P(20, LINE_Y["WSD"])], ["T-WSD"], "NORTH", "SOUTH",
           [("PF1", LINE_Y["WSD"], 40)]),
    _route("RT-SLOW-UP", "Churchgate → PF 2 → Virar (slow)",
           [P(20, LINE_Y["WSU"]), P(1600, LINE_Y["WSU"])], ["T-WSU"], "SOUTH", "NORTH",
           [("PF2", LINE_Y["WSU"], 40)]),
    _route("RT-FAST-DN", "Dahanu → PF 3 → Churchgate (fast)",
           [P(1600, LINE_Y["WFD"]), P(20, LINE_Y["WFD"])], ["T-WFD"], "NORTH", "SOUTH",
           [("PF3", LINE_Y["WFD"], 35)]),
    _route("RT-FAST-UP", "Churchgate → PF 4 → Dahanu (fast)",
           [P(20, LINE_Y["WFU"]), P(1600, LINE_Y["WFU"])], ["T-WFU"], "SOUTH", "NORTH",
           [("PF4", LINE_Y["WFU"], 35)]),
    _route("RT-THRU-DN", "Surat → PF 5 → Mumbai Central (through)",
           [P(1600, LINE_Y["THD"]), P(20, LINE_Y["THD"])], ["T-THD"], "NORTH", "SOUTH",
           [("PF5", LINE_Y["THD"], 60)]),
    _route("RT-THRU-UP", "Mumbai → through UP → Surat",
           [P(20, LINE_Y["THU"]), P(1600, LINE_Y["THU"])], ["T-THU"], "SOUTH", "NORTH", []),
    _route("RT-SHUNT", "North yard shunt move",
           [P(1500, 110), P(1160, 110), P(1120, 150), P(1120, 200), P(1140, LINE_Y["WSD"]), P(1320, LINE_Y["WSD"])],
           ["T-YD1", "T-YDN"], "STATION", "STATION", []),
    _route("RT-FRT-LOOP", "Goods chord → goods loop → Diva branch",
           [P(1600, LINE_Y["FRC"]), P(880, LINE_Y["FRC"]), P(848, 766), P(848, 800),
            P(600, 800), P(520, 792), P(440, 784), P(400, 780), *BRANCH_OUT[1:]],
           ["T-FRC", "T-GY1", "T-BR"], "NORTH", "DIVA", [("GY1", 800, 150)]),
]


# ---------------------------------------------------------------- lookups
route_by_id: dict[str, RailRoute] = {r.id: r for r in routes}
platform_by_id: dict[str, Platform] = {p.id: p for p in platforms}
resource_by_id: dict[str, Resource] = {r.id: r for r in resources}
junction_by_id: dict[str, Junction] = {j.id: j for j in junctions}


@dataclass(frozen=True)
class RouteResourceUse:
    resource_id: str
    s: float


def _build_route_resource_use() -> dict[str, list[RouteResourceUse]]:
    out: dict[str, list[RouteResourceUse]] = {}
    for r in routes:
        uses: list[RouteResourceUse] = []
        for res in resources:
            s = crossing_distance(r.path, res.at, res.radius)
            if s is not None:
                uses.append(RouteResourceUse(res.id, s))
        uses.sort(key=lambda u: u.s)
        out[r.id] = uses
    return out


route_resource_use: dict[str, list[RouteResourceUse]] = _build_route_resource_use()


# Conflicting route pairs at each junction (routes whose movements foul each other).
# Derived from which routes physically consume each JUNCTION resource.
def _build_junction_conflicts() -> dict[str, set[frozenset[str]]]:
    out: dict[str, set[frozenset[str]]] = {}
    for res in resources:
        if res.kind != "JUNCTION":
            continue
        users = [rid for rid, uses in route_resource_use.items()
                 if any(u.resource_id == res.id for u in uses)]
        pairs: set[frozenset[str]] = set()
        for i in range(len(users)):
            for j in range(i + 1, len(users)):
                if users[i] != users[j]:
                    pairs.add(frozenset({users[i], users[j]}))
        out[res.id] = pairs
    return out


junction_conflicts: dict[str, set[frozenset[str]]] = _build_junction_conflicts()
