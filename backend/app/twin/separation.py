"""Minimum safe separation between movements - the rule that prevents collisions.

Block occupancy alone does not guarantee safety on screen or on the ground. A
capacity-1 block stops two trains sharing a section, but with 1.3 km blocks the
train at the exit of one block and the train at the entry of the next can be
almost touching. A twin that draws them overlapping is not modelling a railway.

The real rule is the braking distance. A following movement must always be able
to stop short of the one in front:

    safe gap = braking distance at current speed
             + distance covered during driver reaction / signal sighting
             + the length of the train in front
             + a fixed operating margin

This module supplies that distance and the speed a follower must be regulated to
in order to respect it. `SimulationEngine._travel` consults it before every
movement, so a train physically cannot close inside the gap - it is checked and
brakes, exactly as it would under signalling.
"""
from __future__ import annotations

from .dynamics import Traction

# Time between a restrictive aspect coming into view and the brake taking effect.
REACTION_SEC = 4.0
# Fixed margin on top of the computed stopping distance (overlap / signal
# spacing tolerance). Indian Railways works a 180 m overlap beyond a stop signal.
OVERLAP_M = 180.0
# Absolute floor, so trains never render on top of each other even at a stand.
MIN_GAP_M = 120.0

# Formation lengths. A 24-coach express is ~600 m, a 12-car EMU ~270 m, and a
# loaded goods rake can exceed 700 m.
TRAIN_LENGTH_M: dict[str, float] = {
    "PREMIUM": 600.0, "SUPERFAST": 600.0, "MAIL_EXPRESS": 600.0,
    "PASSENGER": 500.0, "SUBURBAN": 270.0,
    "LOCAL_FAST": 270.0, "LOCAL_SEMIFAST": 270.0, "LOCAL_SLOW": 270.0,
    "LOCAL_AC": 270.0,
    "FREIGHT": 700.0, "PREMIUM_FREIGHT": 700.0, "FREIGHT_EMPTY": 650.0,
    "SHUNT": 150.0,
}


def train_length(service_class: str) -> float:
    return TRAIN_LENGTH_M.get(service_class, 300.0)


def braking_distance_m(speed_ms: float, traction: Traction) -> float:
    """Distance to a stand under service braking from `speed_ms`."""
    rate = max(0.05, traction.max_brake_ms2)
    return (speed_ms * speed_ms) / (2.0 * rate)


def safe_gap_m(speed_ms: float, traction: Traction, ahead_class: str) -> float:
    """Minimum permissible distance between this train and the one in front."""
    return (braking_distance_m(speed_ms, traction)
            + speed_ms * REACTION_SEC
            + train_length(ahead_class)
            + OVERLAP_M)


def safe_speed_ms(gap_m: float, traction: Traction, ahead_class: str) -> float:
    """Fastest speed at which `gap_m` is still a safe following distance.

    Inverts `safe_gap_m` for speed: solves
        v^2 / (2b) + v*t + L + overlap = gap
    which is a quadratic in v.
    """
    usable = gap_m - train_length(ahead_class) - OVERLAP_M
    if usable <= 0:
        return 0.0
    b = max(0.05, traction.max_brake_ms2)
    # v^2/(2b) + t*v - usable = 0  ->  v = -t*b + sqrt((t*b)^2 + 2*b*usable)
    tb = REACTION_SEC * b
    return max(0.0, -tb + (tb * tb + 2.0 * b * usable) ** 0.5)


def clearance(gap_m: float, speed_ms: float, traction: Traction,
              ahead_class: str) -> float:
    """How much room is left beyond the safe gap. Negative means unsafe."""
    return gap_m - safe_gap_m(speed_ms, traction, ahead_class)
