"""Exactness gate: the Python network port must reproduce the frontend TS
geometry/topology numbers so train dots land where the SVG expects.

geo_ref.json was emitted from the REAL frontend TS (src/twin/*) via tsx.
"""
import json
from pathlib import Path

import pytest

from app.network.geometry import point_at, path_length, crossing_distance
from app.network.topology import routes, route_resource_use, route_by_id
from app.network.fleet import fleet, fleet_by_id

REF = json.loads((Path(__file__).parent / "geo_ref.json").read_text())
TOL = 1e-6


def test_route_lengths_match_ts():
    for rid, expected in REF["routeLengths"].items():
        got = path_length(route_by_id[rid].path)
        assert got == pytest.approx(expected, abs=TOL), f"{rid}: {got} != {expected}"


def test_point_samples_match_ts():
    for rid, samples in REF["pointSamples"].items():
        path = route_by_id[rid].path
        for smp in samples:
            p = point_at(path, smp["s"])
            assert p.x == pytest.approx(smp["x"], abs=1e-4), f"{rid}@{smp['s']} x"
            assert p.y == pytest.approx(smp["y"], abs=1e-4), f"{rid}@{smp['s']} y"


def test_route_resource_use_matches_ts():
    for rid, uses in REF["routeResourceUse"].items():
        got = [(u.resource_id, u.s) for u in route_resource_use[rid]]
        exp = [(u["id"], u["s"]) for u in uses]
        assert [g[0] for g in got] == [e[0] for e in exp], f"{rid} resource set/order"
        for (gid, gs), (eid, es) in zip(got, exp):
            assert gs == pytest.approx(es, abs=1e-4), f"{rid} {gid} s"


def test_fleet_start_distances_match_ts():
    for fid, expected in REF["fleetStartS"].items():
        f = fleet_by_id[fid]
        got = crossing_distance(route_by_id[f.route_id].path, f.start, 80)
        assert got == pytest.approx(expected, abs=1e-4), f"{fid}: {got} != {expected}"


def test_demo_bottleneck_shared_junction():
    """The freight and the Diva express must both consume the JB turnout — the
    heart of the demo bottleneck. If this fails the network is mis-wired."""
    exp_uses = {u.resource_id for u in route_resource_use["RT-EXP-DIVA"]}
    frt_uses = {u.resource_id for u in route_resource_use["RT-FRT-DIVA"]}
    assert "JB" in exp_uses
    assert "JB" in frt_uses
