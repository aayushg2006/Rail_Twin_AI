"""Geometry helpers — a faithful port of src/twin/geometry.ts.

Map scale: 1 map unit = 10 metres (METRES_PER_UNIT). All train positions are a
distance `s` (in map units) along a route polyline; the frontend renders each
train at pointAt(route.path, s), so these functions must match the TS ones
exactly for the dots to land where the SVG expects.
"""
from __future__ import annotations

import math
from typing import NamedTuple

METRES_PER_UNIT = 10.0


class Point(NamedTuple):
    x: float
    y: float

    def as_dict(self) -> dict[str, float]:
        return {"x": self.x, "y": self.y}


def P(x: float, y: float) -> Point:
    return Point(float(x), float(y))


def kmh_to_units_per_sec(kmh: float) -> float:
    return (kmh * 1000.0) / 3600.0 / METRES_PER_UNIT


def units_to_km(units: float) -> float:
    return (units * METRES_PER_UNIT) / 1000.0


def dist(a: Point, b: Point) -> float:
    return math.hypot(b.x - a.x, b.y - a.y)


def path_length(path: list[Point]) -> float:
    total = 0.0
    for i in range(1, len(path)):
        total += dist(path[i - 1], path[i])
    return total


def cumulative(path: list[Point]) -> list[float]:
    acc = [0.0]
    for i in range(1, len(path)):
        acc.append(acc[i - 1] + dist(path[i - 1], path[i]))
    return acc


def point_at(path: list[Point], s: float) -> Point:
    if not path:
        return Point(0.0, 0.0)
    acc = cumulative(path)
    total = acc[-1]
    d = max(0.0, min(total, s))
    for i in range(1, len(path)):
        if d <= acc[i]:
            prev = path[i - 1]
            nxt = path[i]
            seg = (acc[i] - acc[i - 1]) or 1.0
            t = (d - acc[i - 1]) / seg
            return Point(prev.x + (nxt.x - prev.x) * t, prev.y + (nxt.y - prev.y) * t)
    return path[-1]


def heading_at(path: list[Point], s: float) -> float:
    a = point_at(path, max(0.0, s - 4))
    b = point_at(path, s + 4)
    return math.degrees(math.atan2(b.y - a.y, b.x - a.x))


def path_slice(path: list[Point], frm: float, to: float) -> list[Point]:
    acc = cumulative(path)
    total = acc[-1]
    a = max(0.0, min(total, frm))
    b = max(0.0, min(total, to))
    if b <= a:
        return [point_at(path, a)]
    out: list[Point] = [point_at(path, a)]
    for i in range(len(path)):
        if a < acc[i] < b:
            out.append(path[i])
    out.append(point_at(path, b))
    return out


def crossing_distance(path: list[Point], target: Point, radius: float) -> float | None:
    """Distance along `path` where it comes closest to `target`, or None when it
    never enters `radius`. Used to derive which resources a route consumes."""
    acc = cumulative(path)
    best: tuple[float, float] | None = None  # (distance, s)
    for i in range(1, len(path)):
        a = path[i - 1]
        b = path[i]
        vx = b.x - a.x
        vy = b.y - a.y
        len2 = vx * vx + vy * vy
        if len2 == 0:
            t = 0.0
        else:
            t = max(0.0, min(1.0, ((target.x - a.x) * vx + (target.y - a.y) * vy) / len2))
        px = a.x + vx * t
        py = a.y + vy * t
        d = math.hypot(target.x - px, target.y - py)
        if best is None or d < best[0]:
            best = (d, acc[i - 1] + math.sqrt(len2) * t)
    if best is None or best[0] > radius:
        return None
    return best[1]
