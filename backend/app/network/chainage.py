"""Linear reference model for Vasai Road Jn - everything in METRES.

This replaces the old `geometry.py`, where a train's position was a distance
along an SVG polyline and `METRES_PER_UNIT = 10` reinterpreted drawing units as
distance. Nothing here knows what a pixel is; the console projects chainage onto
the schematic at render time.

A `Position` is (corridor, chainage). Chainage is measured OUTWARD from the
centre of the Vasai Road platform line and is always >= 0, so a movement from the
north through the station to the south runs NORTH 8000 -> 0 then SOUTH 0 -> 8000.

A `Path` is an ordered list of `Leg`s. Distance along a path (`s`) is the sum of
absolute chainage changes, which is real ground distance.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, NamedTuple

EPS = 1e-6


class Position(NamedTuple):
    corridor: str
    m: float

    def as_dict(self) -> dict:
        return {"corridor": self.corridor, "m": round(self.m, 1)}


def kmh_to_ms(kmh: float) -> float:
    return kmh * 1000.0 / 3600.0


def ms_to_kmh(ms: float) -> float:
    return ms * 3600.0 / 1000.0


@dataclass(frozen=True)
class Leg:
    """A run along one corridor, from one chainage to another."""
    corridor: str
    from_m: float
    to_m: float

    @property
    def length_m(self) -> float:
        return abs(self.to_m - self.from_m)

    @property
    def outbound(self) -> bool:
        """True when chainage increases, i.e. the train is leaving the station."""
        return self.to_m >= self.from_m

    def position_at(self, d: float) -> Position:
        d = max(0.0, min(self.length_m, d))
        step = d if self.outbound else -d
        return Position(self.corridor, self.from_m + step)

    def d_at(self, m: float) -> float | None:
        """Distance into this leg at chainage `m`, or None if outside it."""
        lo, hi = min(self.from_m, self.to_m), max(self.from_m, self.to_m)
        if m < lo - EPS or m > hi + EPS:
            return None
        return (m - self.from_m) if self.outbound else (self.from_m - m)

    def overlap(self, corridor: str, from_m: float, to_m: float) -> tuple[float, float] | None:
        """Distances into this leg where it occupies [from_m, to_m] on `corridor`."""
        if corridor != self.corridor:
            return None
        lo = max(min(self.from_m, self.to_m), min(from_m, to_m))
        hi = min(max(self.from_m, self.to_m), max(from_m, to_m))
        if hi <= lo + EPS:
            return None
        a, b = self.d_at(lo), self.d_at(hi)
        if a is None or b is None:
            return None
        return (min(a, b), max(a, b))


@dataclass(frozen=True)
class Path:
    legs: tuple[Leg, ...]

    @property
    def length_m(self) -> float:
        return sum(leg.length_m for leg in self.legs)

    def position_at(self, s: float) -> Position:
        if not self.legs:
            return Position("", 0.0)
        s = max(0.0, min(self.length_m, s))
        run = 0.0
        for leg in self.legs:
            if s <= run + leg.length_m + EPS:
                return leg.position_at(s - run)
            run += leg.length_m
        return self.legs[-1].position_at(self.legs[-1].length_m)

    def s_at(self, corridor: str, m: float) -> float | None:
        """First distance along the path at chainage `m` on `corridor`."""
        run = 0.0
        for leg in self.legs:
            if leg.corridor == corridor:
                d = leg.d_at(m)
                if d is not None:
                    return run + d
            run += leg.length_m
        return None

    def occupies(self, corridor: str, from_m: float, to_m: float) -> tuple[float, float] | None:
        """(enter_s, exit_s) where this path occupies the given chainage span."""
        run = 0.0
        best: tuple[float, float] | None = None
        for leg in self.legs:
            span = leg.overlap(corridor, from_m, to_m)
            if span is not None:
                candidate = (run + span[0], run + span[1])
                if best is None or candidate[0] < best[0]:
                    best = candidate
            run += leg.length_m
        return best

    def slice(self, from_s: float, to_s: float) -> list[Position]:
        """Positions describing the path between two distances, corner-preserving.

        Used to draw a projected trajectory: the console needs the intermediate
        corridor changes, not just the endpoints, or the line cuts the corner.
        """
        lo = max(0.0, min(self.length_m, min(from_s, to_s)))
        hi = max(0.0, min(self.length_m, max(from_s, to_s)))
        out: list[Position] = [self.position_at(lo)]
        run = 0.0
        for leg in self.legs:
            end = run + leg.length_m
            if lo < end < hi:
                out.append(leg.position_at(leg.length_m))
            run = end
        out.append(self.position_at(hi))
        # Drop consecutive duplicates so a zero-length hop never becomes a point.
        deduped: list[Position] = []
        for p in out:
            if not deduped or abs(p.m - deduped[-1].m) > EPS or p.corridor != deduped[-1].corridor:
                deduped.append(p)
        return deduped or [self.position_at(lo)]


def path_of(legs: Iterable[Leg]) -> Path:
    return Path(tuple(leg for leg in legs if leg.length_m > EPS))
