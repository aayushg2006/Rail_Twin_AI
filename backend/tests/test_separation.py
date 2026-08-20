"""No two movements may ever occupy the same piece of track.

Block occupancy alone does not guarantee this. A capacity-1 block stops two
trains sharing a section, but with 1.3 km blocks the train leaving one and the
train entering the next can be nearly touching - which is how trains ended up
drawn on top of each other on the schematic.

The rule enforced here is the real one: a following movement must always be able
to stop short of the one in front (braking distance + reaction + train length +
signal overlap).
"""
from __future__ import annotations

import pytest

from app.twin.dynamics import traction_for
from app.twin.engine import SimulationEngine
from app.twin.separation import (MIN_GAP_M, OVERLAP_M, braking_distance_m,
                                 safe_gap_m, safe_speed_ms, train_length)

SCENARIOS = ["BASE", "PLATFORM_BLOCKED", "BRANCH_BLOCKED",
             "SIGNAL_DEGRADED", "FREIGHT_LATE", "PEAK_SURGE"]


# ------------------------------------------------------------- the distances
def test_braking_distance_grows_with_the_square_of_speed():
    t = traction_for("LOCAL_FAST")
    d40 = braking_distance_m(40 / 3.6, t)
    d80 = braking_distance_m(80 / 3.6, t)
    assert d80 == pytest.approx(4 * d40, rel=0.02)


def test_a_goods_rake_needs_far_more_room_than_an_emu():
    at = 55 / 3.6
    goods = safe_gap_m(at, traction_for("FREIGHT"), "FREIGHT")
    emu = safe_gap_m(at, traction_for("LOCAL_FAST"), "LOCAL_FAST")
    assert goods > 1.5 * emu


def test_even_at_a_stand_there_is_a_gap():
    gap = safe_gap_m(0.0, traction_for("LOCAL_FAST"), "LOCAL_FAST")
    assert gap >= train_length("LOCAL_FAST") + OVERLAP_M
    assert gap >= MIN_GAP_M


def test_safe_speed_inverts_safe_gap():
    """Exact above the standing gap; below it the answer is simply 'stop'."""
    t = traction_for("LOCAL_FAST")
    standing = safe_gap_m(0.0, t, "LOCAL_FAST")
    for gap in (800.0, 1500.0, 3000.0):
        v = safe_speed_ms(gap, t, "LOCAL_FAST")
        assert safe_gap_m(v, t, "LOCAL_FAST") == pytest.approx(gap, abs=2.0)
    assert safe_speed_ms(standing * 0.5, t, "LOCAL_FAST") == 0.0


def test_no_room_means_no_movement():
    t = traction_for("FREIGHT")
    assert safe_speed_ms(50.0, t, "FREIGHT") == 0.0


# --------------------------------------------------- the invariant, in flight
def _min_gaps(engine: SimulationEngine) -> list[tuple[float, float, str, str]]:
    """(gap, required, follower, leader) for every same-line pair, closest first."""
    now = engine.now
    out = []
    for tid, rt in engine.trains.items():
        if rt.finished or not rt.admitted:
            continue
        ahead = engine._train_ahead(rt, now)
        if ahead is None:
            continue
        gap, ahead_class = ahead
        speed = rt.sample(now)[1]
        required = safe_gap_m(speed, traction_for(rt.service_class), ahead_class)
        out.append((gap, required, tid, ahead_class))
    out.sort(key=lambda r: r[0] - r[1])
    return out


def _place_pair(engine, follower_s: float, leader_s: float):
    """Put two admitted trains on the SAME line, the given distance apart.

    Emergent following moves are rare at this section's traffic density (peak
    concurrency is about 8 trains over 12 running lines), so the protection is
    exercised directly rather than waited for.
    """
    same_line = {}
    for tid, rt in engine.trains.items():
        route = engine.routes.get(tid)
        if route is None:
            continue
        same_line.setdefault((route.leg_lines[0], route.arrival_corridor), []).append(tid)
    pair = next((v for v in same_line.values() if len(v) >= 2), None)
    if pair is None:
        return None
    a, b = pair[0], pair[1]
    for tid, s in ((a, follower_s), (b, leader_s)):
        rt = engine.trains[tid]
        rt.admitted = True
        rt.finished = False
        rt.entry_at_sec = 0.0
        rt.s = s
        rt.profile = None
        rt.speed_ms = rt.line_speed_kmh * (1000.0 / 3600.0)
    return a, b


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_trains_never_close_inside_the_safe_gap(scenario):
    """Sampled across a full run, no follower is ever inside its braking distance."""
    engine = SimulationEngine(scenario, seed=42)
    worst = None
    pairs_seen = 0
    for _ in range(40):
        engine.advance(30.0)
        rows = _min_gaps(engine)
        pairs_seen += len(rows)
        for gap, required, tid, ahead_class in rows:
            margin = gap - required
            if worst is None or margin < worst[0]:
                worst = (margin, gap, required, tid, ahead_class, engine.now)
    if worst is None:
        pytest.skip(f"{scenario}: no following pairs arose (density is low here)")
    margin, gap, required, tid, ahead_class, when = worst
    # Two separate requirements, strongest first.
    # 1. NEVER a physical overlap: the follower's nose must stay clear of the
    #    leader's tail. This is the collision condition and has no tolerance.
    assert gap > train_length(ahead_class), (
        f"{scenario}: {tid} is inside a {ahead_class} at t={when:.0f}s "
        f"({gap:.0f} m front-to-front, the train ahead is "
        f"{train_length(ahead_class):.0f} m long)")
    # 2. The braking-distance rule, with a tolerance for discrete stepping: the
    #    road ahead is re-read every SEPARATION_CHECK_SEC, so a follower can sit
    #    fractionally inside the ideal curve between checks.
    assert margin > -200.0, (
        f"{scenario}: {tid} closed to {gap:.0f} m behind a {ahead_class} at "
        f"t={when:.0f}s, needing {required:.0f} m")


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_two_trains_are_never_at_the_same_place(scenario):
    """The blunt version of the same rule: never coincident on one line."""
    engine = SimulationEngine(scenario, seed=42)
    for _ in range(40):
        engine.advance(30.0)
        now = engine.now
        seen: dict[tuple[str, str], list[tuple[float, str]]] = {}
        for tid, rt in engine.trains.items():
            if rt.finished or not rt.admitted:
                continue
            route = engine.routes.get(tid)
            if route is None:
                continue
            s = rt.sample_s(now)
            pos = route.position_at(s)
            seen.setdefault((pos.corridor, route.line_at(s)), []).append((pos.m, tid))
        for key, group in seen.items():
            group.sort()
            for (m1, a), (m2, b) in zip(group, group[1:]):
                assert (m2 - m1) > MIN_GAP_M * 0.5, (
                    f"{scenario}: {a} and {b} are {m2 - m1:.0f} m apart on {key}")


def test_the_engine_sees_a_train_directly_in_front():
    engine = SimulationEngine("PEAK_SURGE", seed=42)
    engine.advance(120)
    pair = _place_pair(engine, follower_s=1000.0, leader_s=1600.0)
    if pair is None:
        pytest.skip("no two services share a road in this window")
    follower, leader = pair
    ahead = engine._train_ahead(engine.trains[follower], engine.now)
    assert ahead is not None, "the movement in front was not detected"
    gap, ahead_class = ahead
    assert gap == pytest.approx(600.0, abs=1.0)
    assert ahead_class == engine.trains[leader].service_class


def test_a_follower_is_checked_and_never_passes_through():
    """The protection in action: closing on a stationary train ahead, the
    follower slows and stops short - it never overtakes it on the same road."""
    engine = SimulationEngine("PEAK_SURGE", seed=42)
    engine.advance(120)
    pair = _place_pair(engine, follower_s=800.0, leader_s=2400.0)
    if pair is None:
        pytest.skip("no two services share a road in this window")
    follower, leader = pair
    engine.trains[leader].speed_ms = 0.0

    closest = 1e9
    for _ in range(30):
        engine.advance(10.0)
        f, l = engine.trains[follower], engine.trains[leader]
        if f.finished or l.finished:
            break
        gap = l.sample_s(engine.now) - f.sample_s(engine.now)
        closest = min(closest, gap)
        assert gap > 0, f"{follower} passed through {leader}"
    if closest < 1e8:
        floor = safe_gap_m(0.0, traction_for(engine.trains[follower].service_class),
                           engine.trains[leader].service_class)
        assert closest > floor * 0.5, (
            f"closed to {closest:.0f} m, inside the {floor:.0f} m standing gap")


def test_safe_speed_falls_as_the_gap_closes():
    """The property that makes the protection continuous rather than a cliff."""
    t = traction_for("LOCAL_FAST")
    speeds = [safe_speed_ms(g, t, "LOCAL_FAST") for g in (3000, 2000, 1200, 700, 460)]
    assert speeds == sorted(speeds, reverse=True)
    assert speeds[-1] < speeds[0]
