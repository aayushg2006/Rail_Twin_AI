"""Loader for the metric network, timetable and freight packs.

All three JSON packs live in `data/` outside the backend package so the console
and the twin consume exactly the same numbers.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any


def _load(name: str) -> dict[str, Any]:
    candidates = [
        Path(__file__).resolve().parents[3] / "data" / name,
        Path("/srv/data") / name,
        Path("data") / name,
    ]
    for path in candidates:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    raise FileNotFoundError(f"data pack {name} not found in {[str(c) for c in candidates]}")


network_pack: dict[str, Any] = _load("network-bsr.json")
timetable_pack: dict[str, Any] = _load("timetable-bsr.json")
freight_pack: dict[str, Any] = _load("freight-paths.json")


@dataclass(frozen=True)
class Corridor:
    id: str
    label: str
    short_label: str
    screen_dir: str
    reach_m: float
    line_speed_kmh: float
    stations: tuple[tuple[str, str, float], ...] = ()


@dataclass(frozen=True)
class Line:
    id: str
    label: str
    kind: str
    direction: str          # UP | DOWN | BRANCH_OUT | BRANCH_IN | BOTH
    platform_id: str | None
    speed_limit_kmh: float


@dataclass(frozen=True)
class Platform:
    id: str
    label: str
    side: str
    usage: str
    serves: tuple[str, ...]
    length_m: float


@dataclass(frozen=True)
class Resource:
    id: str
    label: str
    kind: str               # JUNCTION | PLATFORM | BLOCK
    corridor: str
    lines: frozenset[str]
    from_m: float
    to_m: float
    centre_m: float
    length_m: float
    headway_sec: float
    capacity: int = 1


corridors: dict[str, Corridor] = {
    cid: Corridor(
        id=cid, label=c["label"], short_label=c["shortLabel"],
        screen_dir=c["screenDir"], reach_m=float(c["reachM"]),
        line_speed_kmh=float(c["lineSpeedKmh"]),
        stations=tuple((s["code"], s["name"], float(s["chainageM"]))
                       for s in c.get("stations", [])),
    )
    for cid, c in network_pack["corridors"].items()
}

lines: dict[str, Line] = {
    ln["id"]: Line(ln["id"], ln["label"], ln["kind"], ln["direction"],
                   ln.get("platformId"), float(ln["speedLimitKmh"]))
    for ln in network_pack["lines"]
}

platforms: dict[str, Platform] = {
    p["id"]: Platform(p["id"], p["label"], p["side"], p["usage"],
                      tuple(p["serves"]), float(p["lengthM"]))
    for p in network_pack["platforms"]
}

resources: dict[str, Resource] = {
    r["id"]: Resource(
        id=r["id"], label=r["label"], kind=r["kind"], corridor=r["corridor"],
        lines=frozenset(r.get("lines", ())), from_m=float(r["fromM"]),
        to_m=float(r["toM"]), centre_m=float(r["centreM"]),
        length_m=float(r["lengthM"]), headway_sec=float(r["headwaySec"]),
        capacity=int(r.get("capacity", 1)),
    )
    for r in network_pack["resources"]
}

corridor_lines: dict[str, list[str]] = {
    k: list(v) for k, v in network_pack["corridorLines"].items()
}

STATION_LIMIT_M: float = float(network_pack["stationLimitM"])
MODELLED_REACH_M: float = float(network_pack["modelledReachM"])


@lru_cache(maxsize=None)
def lines_serving(platform_id: str) -> tuple[str, ...]:
    p = platforms.get(platform_id)
    return p.serves if p else ()


@lru_cache(maxsize=None)
def platform_for_line(line_id: str) -> str | None:
    ln = lines.get(line_id)
    return ln.platform_id if ln else None


def resources_on(corridor: str, line_id: str) -> list[Resource]:
    """Resources a movement on `line_id` can occupy while on `corridor`."""
    return [
        r for r in resources.values()
        if line_id in r.lines and (r.corridor == corridor or r.kind == "PLATFORM")
    ]
