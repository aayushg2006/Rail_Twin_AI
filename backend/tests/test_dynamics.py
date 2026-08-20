"""Train movement must be physics, not a constant.

The old engine ran every train at `nominalSpeedKmh` with no acceleration or
braking, which is why the console looked like it was showing a hardcoded speed.
"""
from __future__ import annotations

import random

import pytest

from app.twin.dynamics import build_profile, traction_for

KMH = 1000.0 / 3600.0


def _profile(distance, v0_kmh, limit_kmh, v1_kmh, cls):
    return build_profile(distance, v0_kmh * KMH, limit_kmh * KMH, v1_kmh * KMH,
                         traction_for(cls))


def test_speed_varies_continuously_across_a_run():
    p = _profile(8000, 0, 100, 0, "LOCAL_FAST")
    speeds = [p.sample(t)[1] * 3.6 for t in (0, 10, 20, 40, 90, 200)]
    assert speeds[0] == pytest.approx(0, abs=0.1)
    assert all(b > a for a, b in zip(speeds, speeds[1:])) or speeds[-1] > 50
    assert len(set(round(s) for s in speeds)) > 3, "speed must not be a constant"


def test_emu_reaches_line_speed_in_a_realistic_time():
    p = _profile(3000, 0, 100, 100, "LOCAL_FAST")
    assert 25 <= p.phases[0].duration <= 55, "EMU should reach 100 km/h in ~35 s"


def test_freight_accelerates_far_more_slowly_than_an_emu():
    emu = _profile(6000, 0, 55, 55, "LOCAL_FAST").phases[0].duration
    goods = _profile(6000, 0, 55, 55, "FREIGHT").phases[0].duration
    assert goods > 2.5 * emu


def test_a_heavier_class_takes_longer_over_the_same_distance():
    fast = _profile(8000, 0, 80, 0, "LOCAL_FAST").duration
    goods = _profile(8000, 0, 80, 0, "FREIGHT").duration
    assert goods > fast


def test_profile_covers_exactly_the_requested_distance():
    p = _profile(5400, 20, 90, 0, "MAIL_EXPRESS")
    assert sum(ph.distance for ph in p.phases) == pytest.approx(5400, abs=1.0)
    assert p.sample(p.duration)[0] == pytest.approx(5400, abs=1.0)


def test_cannot_stop_in_less_than_braking_distance():
    """A train closing on a signal it cannot clear arrives slower, never sooner."""
    p = _profile(50, 100, 100, 0, "LOCAL_FAST")
    assert p.v_exit > 0, "50 m is not enough to stop from 100 km/h"
    assert p.duration > 0


def test_time_at_is_consistent_with_sample():
    p = _profile(4000, 0, 90, 0, "SUPERFAST")
    for frac in (0.1, 0.35, 0.6, 0.9):
        d = p.distance * frac
        t = p.time_at(d)
        assert p.sample(t)[0] == pytest.approx(d, abs=15.0)


def test_profiles_are_always_well_formed():
    rng = random.Random(7)
    for _ in range(4000):
        p = build_profile(rng.uniform(0.5, 9000), rng.uniform(0, 35),
                          rng.uniform(4, 36), rng.uniform(0, 35),
                          traction_for(rng.choice(
                              ["LOCAL_FAST", "PREMIUM", "FREIGHT", "SHUNT", "SUBURBAN"])))
        assert p.duration > 0
        assert all(ph.duration >= 0 and ph.distance >= 0 for ph in p.phases)
        assert sum(ph.distance for ph in p.phases) == pytest.approx(p.distance, abs=1.0)
