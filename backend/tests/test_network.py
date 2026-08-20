"""The network must match the real Vasai Road, not a drawing.

The old test suite only proved the Python port matched the TypeScript one, which
locked in a geometry where every Western route was 15.80 km, the Diva branch was
17.4 km against its own 36.8 km label, and platforms were 5.5 km long. These
tests assert against the published railway instead.
"""
from __future__ import annotations

import pytest

from app.network.chainage import Leg, Position, path_of
from app.network.net import (MODELLED_REACH_M, STATION_LIMIT_M, corridors,
                             lines, platforms, resources)
from app.network.routes import build_route


# --------------------------------------------------------------- real distances
def _station(corridor: str, code: str) -> float:
    return dict((c, m) for c, _n, m in corridors[corridor].stations)[code]


def test_diva_junction_is_36_8_km():
    assert _station("DIVA", "DIVA") == pytest.approx(36_800, abs=100)


def test_virar_is_8_km_and_bhayandar_9_km():
    assert _station("NORTH", "VR") == pytest.approx(8_000, abs=100)
    assert _station("SOUTH", "BYR") == pytest.approx(9_000, abs=100)


def test_branch_stations_are_ordered_and_monotonic():
    chain = [m for _c, _n, m in corridors["DIVA"].stations]
    assert chain == sorted(chain)
    assert chain[0] < chain[-1] <= 40_000


def test_the_seven_faces_have_their_real_lengths():
    """From the OSM platform polygons, not a flat 600 m for everything."""
    assert len(platforms) == 7
    expected = {"PF1": 350, "PF2": 368, "PF3": 368,
                "PF4": 605, "PF5": 605, "PF6": 623, "PF7": 623}
    for pid, length in expected.items():
        assert platforms[pid].length_m == pytest.approx(length, abs=2), (
            f"{pid} is {platforms[pid].length_m} m, expected about {length} m")


def test_vasai_road_is_a_side_platform_plus_three_islands():
    """OSM tags them `2;3`, `4;5`, `6;7` - the layout the console must draw."""
    sides = {}
    for p in platforms.values():
        sides.setdefault(p.side, []).append(p.id)
    assert sorted(sides["ISLAND-A"]) == ["PF2", "PF3"]
    assert sorted(sides["ISLAND-B"]) == ["PF4", "PF5"]
    assert sorted(sides["ISLAND-C"]) == ["PF6", "PF7"]
    assert sides["SIDE"] == ["PF1"]


def test_the_real_track_geometry_is_carried_for_the_map():
    from app.network.net import network_pack
    geo = network_pack.get("geo") or {}
    assert geo.get("available") is True, "run tools/ingest/fetch_osm.py"
    assert "OpenStreetMap" in geo["licence"]
    assert len(geo["ways"]) > 30
    assert len(geo["switches"]) >= 6, "the six real turnouts"
    assert len(geo["signals"]) >= 5
    # Every way carries both real coordinates and local metres.
    for way in geo["ways"][:5]:
        assert way["points"] and way["local"]
        assert -1.0 < way["points"][0]["lat"] - 19.38 < 1.0


def test_station_limits_are_plausible_for_a_junction():
    assert 800 <= STATION_LIMIT_M <= 2_000
    assert MODELLED_REACH_M > STATION_LIMIT_M


# ------------------------------------------------------------------- resources
def test_every_resource_has_a_line_and_a_positive_headway():
    for r in resources.values():
        assert r.lines, f"{r.id} is used by no running line"
        assert r.headway_sec > 0
        assert r.to_m > r.from_m


def test_blocks_are_per_line_so_opposing_moves_never_contend():
    down = {r.id for r in resources.values()
            if r.kind == "BLOCK" and "SLD" in r.lines}
    up = {r.id for r in resources.values()
          if r.kind == "BLOCK" and "SLU" in r.lines}
    assert down and up
    assert not (down & up), "Up and Down blocks must be distinct track"


def test_branch_headway_is_coarser_than_the_suburban_main():
    branch = [r.headway_sec for r in resources.values()
              if r.kind == "BLOCK" and r.corridor == "DIVA"]
    main = [r.headway_sec for r in resources.values()
            if r.kind == "BLOCK" and r.corridor == "NORTH"]
    assert min(branch) > max(main), "absolute block must be coarser than automatic block"


# ---------------------------------------------------------------------- routes
def test_a_through_route_is_twice_the_modelled_reach():
    route = build_route("T", "SOUTH", "NORTH", "LOCAL_FAST", "PF4", 30)
    assert route.length_m == pytest.approx(2 * MODELLED_REACH_M, abs=1)


def test_branch_service_works_over_the_branch_turnout():
    route = build_route("T", "NORTH", "DIVA", "SUBURBAN", "PF6", 45)
    used = {u.resource_id for u in route.uses}
    assert "J-B" in used, "a Diva-bound movement must take the branch turnout"
    assert "PF6" in used


def test_freight_to_the_branch_uses_the_goods_chord_not_a_platform():
    route = build_route("T", "NORTH", "DIVA", "FREIGHT", None, 0)
    assert route.platform_id is None
    assert route.leg_lines == ("GDC", "GDC")
    assert "J-G" in {u.resource_id for u in route.uses}


def test_branch_and_freight_contend_for_the_same_turnout():
    memu = build_route("A", "NORTH", "DIVA", "SUBURBAN", "PF6", 45)
    goods = build_route("B", "NORTH", "DIVA", "FREIGHT", None, 0)
    assert "J-B" in {u.resource_id for u in memu.uses}
    assert "J-B" in {u.resource_id for u in goods.uses}


def test_resource_uses_are_ordered_and_inside_the_route():
    route = build_route("T", "DIVA", "NORTH", "PREMIUM", "PF6", 120)
    entries = [u.enter_s for u in route.uses]
    assert entries == sorted(entries)
    for u in route.uses:
        assert 0 <= u.enter_s <= route.length_m + 1
        assert u.exit_s >= u.enter_s


# -------------------------------------------------------------------- chainage
def test_path_length_is_the_sum_of_absolute_chainage_change():
    path = path_of([Leg("NORTH", 8000, 0), Leg("SOUTH", 0, 8000)])
    assert path.length_m == pytest.approx(16_000)
    assert path.position_at(0) == Position("NORTH", 8000)
    assert path.position_at(8000).m == pytest.approx(0, abs=1e-6)
    assert path.position_at(16_000) == Position("SOUTH", 8000)


def test_slice_keeps_the_corner_between_corridors():
    path = path_of([Leg("NORTH", 8000, 0), Leg("SOUTH", 0, 8000)])
    pts = path.slice(4000, 12000)
    assert [p.corridor for p in pts] == ["NORTH", "NORTH", "SOUTH"]
    assert pts[1].m == pytest.approx(0, abs=1e-6)


def test_occupies_finds_the_span_over_a_resource():
    path = path_of([Leg("NORTH", 8000, 0), Leg("SOUTH", 0, 8000)])
    span = path.occupies("NORTH", 990, 1310)
    assert span is not None
    enter, exit_s = span
    assert enter == pytest.approx(6690, abs=1)
    assert exit_s == pytest.approx(7010, abs=1)
