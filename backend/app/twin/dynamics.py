"""Longitudinal train dynamics - acceleration, cruise and braking.

The old engine moved every train at a constant `nominalSpeedKmh`: a train was
either at 110 km/h or stopped, with nothing in between, which is why the console
appeared to show a hardcoded speed. Here a movement is a real trapezoidal
profile - accelerate under traction against Davis running resistance, cruise at
the permissible speed, brake at the service rate - so the speed shown at any
instant is computed, and so are the run times the conflict predictor depends on.

Davis running resistance R = A + B*v + C*v^2 (newtons per tonne, v in m/s) is
applied as a speed-dependent reduction in net acceleration, evaluated at the mean
speed of each phase. That is the standard engineering simplification; it costs a
fraction of a percent on the run time and keeps the profile closed-form, which
matters because the what-if layer evaluates thousands of these per second.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

G = 9.80665
MIN_SPEED_MS = 0.5


@dataclass(frozen=True)
class Traction:
    """Motive characteristics of a rake type."""
    label: str
    max_accel_ms2: float        # tractive acceleration at low speed
    max_brake_ms2: float        # service braking rate
    davis_a: float              # N/tonne, constant (journal + rolling)
    davis_b: float              # N/tonne per m/s
    davis_c: float              # N/tonne per (m/s)^2, aerodynamic
    max_speed_kmh: float


# EMU stock accelerates hard and brakes hard; a loco-hauled express is heavier
# and slower to get away; a loaded goods rake is an order of magnitude worse.
TRACTION: dict[str, Traction] = {
    "EMU": Traction("EMU / MEMU suburban", 0.80, 1.10, 8.0, 0.30, 0.045, 110.0),
    "EXPRESS": Traction("Loco-hauled express", 0.35, 0.60, 6.5, 0.25, 0.055, 130.0),
    "PREMIUM": Traction("Premium express", 0.45, 0.70, 6.0, 0.24, 0.050, 130.0),
    "FREIGHT": Traction("Loaded goods rake", 0.15, 0.30, 7.0, 0.20, 0.030, 75.0),
    "FREIGHT_EMPTY": Traction("Empty goods rake", 0.22, 0.38, 7.5, 0.22, 0.038, 80.0),
    "SHUNT": Traction("Yard shunt", 0.30, 0.50, 9.0, 0.30, 0.040, 25.0),
}

TRACTION_BY_CLASS = {
    "PREMIUM": "PREMIUM", "SUPERFAST": "EXPRESS", "MAIL_EXPRESS": "EXPRESS",
    "PASSENGER": "EXPRESS", "SUBURBAN": "EMU", "LOCAL_FAST": "EMU",
    "LOCAL_SEMIFAST": "EMU", "LOCAL_SLOW": "EMU", "LOCAL_AC": "EMU",
    "FREIGHT": "FREIGHT", "PREMIUM_FREIGHT": "FREIGHT",
    "FREIGHT_EMPTY": "FREIGHT_EMPTY", "SHUNT": "SHUNT",
}


def traction_for(service_class: str) -> Traction:
    return TRACTION[TRACTION_BY_CLASS.get(service_class, "EMU")]


def resistance_ms2(t: Traction, v_ms: float) -> float:
    """Davis resistance expressed as a deceleration (m/s^2)."""
    v = max(0.0, v_ms)
    return (t.davis_a + t.davis_b * v + t.davis_c * v * v) / 1000.0


@dataclass(frozen=True)
class Phase:
    """A constant-acceleration slice of a movement."""
    duration: float
    distance: float
    v0: float
    accel: float

    def sample(self, dt: float) -> tuple[float, float]:
        dt = max(0.0, min(self.duration, dt))
        return (self.v0 * dt + 0.5 * self.accel * dt * dt,
                max(0.0, self.v0 + self.accel * dt))


@dataclass(frozen=True)
class Profile:
    """A complete movement over a fixed distance: accelerate, cruise, brake."""
    phases: tuple[Phase, ...]
    distance: float
    duration: float
    v_entry: float
    v_exit: float
    v_peak: float

    def sample(self, t: float) -> tuple[float, float]:
        """(distance covered, speed in m/s) at time `t` into the movement."""
        if t <= 0:
            return 0.0, self.v_entry
        if t >= self.duration:
            return self.distance, self.v_exit
        run_t = 0.0
        run_d = 0.0
        for ph in self.phases:
            if t <= run_t + ph.duration:
                d, v = ph.sample(t - run_t)
                return run_d + d, v
            run_t += ph.duration
            run_d += ph.distance
        return self.distance, self.v_exit

    def time_at(self, distance: float) -> float:
        """Time to cover `distance` into this movement."""
        if distance <= 0:
            return 0.0
        if distance >= self.distance:
            return self.duration
        run_t = 0.0
        run_d = 0.0
        for ph in self.phases:
            if distance <= run_d + ph.distance:
                left = distance - run_d
                if abs(ph.accel) < 1e-9:
                    return run_t + (left / max(MIN_SPEED_MS, ph.v0))
                disc = ph.v0 * ph.v0 + 2 * ph.accel * left
                v = math.sqrt(max(0.0, disc))
                return run_t + (v - ph.v0) / ph.accel
            run_t += ph.duration
            run_d += ph.distance
        return self.duration


def _phase(v0: float, v1: float, accel: float) -> Phase:
    if abs(accel) < 1e-9 or abs(v1 - v0) < 1e-9:
        return Phase(0.0, 0.0, v0, 0.0)
    duration = (v1 - v0) / accel
    distance = (v1 * v1 - v0 * v0) / (2 * accel)
    return Phase(max(0.0, duration), max(0.0, distance), v0, accel)


def build_profile(distance_m: float, v_entry_ms: float, v_limit_ms: float,
                  v_exit_ms: float, traction: Traction,
                  gradient_permille: float = 0.0) -> Profile:
    """Fastest legal movement over `distance_m` entering at `v_entry_ms` and
    leaving at `v_exit_ms`, never exceeding `v_limit_ms`."""
    distance_m = max(0.0, distance_m)
    v_limit = max(MIN_SPEED_MS, min(v_limit_ms, traction.max_speed_kmh * 1000 / 3600))
    v0 = max(0.0, min(v_entry_ms, v_limit))
    v1 = max(0.0, min(v_exit_ms, v_limit))

    if distance_m < 1e-6:
        return Profile((), 0.0, 0.0, v0, v0, v0)

    grade = G * gradient_permille / 1000.0
    # Resistance and gradient are evaluated at the mean speed of the movement.
    v_mean = max(MIN_SPEED_MS, (v0 + v_limit) / 2)
    drag = resistance_ms2(traction, v_mean)
    accel = max(0.05, traction.max_accel_ms2 - drag - grade)
    brake = max(0.05, traction.max_brake_ms2 + drag + grade)

    # Peak speed of a triangular profile that exactly fits the distance.
    v_peak_sq = (2 * accel * brake * distance_m + brake * v0 * v0 + accel * v1 * v1) / (accel + brake)
    v_peak = math.sqrt(max(0.0, v_peak_sq))

    phases: list[Phase] = []
    if v_peak < v0:
        # The entry speed is already above what fits in the distance: the whole
        # movement is a brake application, and the exit speed is whatever the
        # service brake actually achieves. (Physically this is a train closing on
        # a signal it cannot clear at line speed - it arrives slower, not sooner.)
        v_end = math.sqrt(max(0.0, v0 * v0 - 2 * brake * distance_m))
        v_end = max(v_end, min(v1, v0))
        rate = (v_end * v_end - v0 * v0) / (2 * distance_m) if distance_m > 0 else 0.0
        duration = (2 * distance_m / max(MIN_SPEED_MS, v0 + v_end))
        phases.append(Phase(duration, distance_m, v0, rate))
        return Profile(tuple(phases), distance_m, duration, v0, v_end, v0)
    if v_peak <= v_limit:
        # Triangular: never reaches the permissible speed.
        v_peak = max(v_peak, MIN_SPEED_MS, v0)
        phases.append(_phase(v0, v_peak, accel))
        phases.append(_phase(v_peak, v1, -brake))
    else:
        v_peak = v_limit
        acc = _phase(v0, v_limit, accel)
        dec = _phase(v_limit, v1, -brake)
        cruise_d = distance_m - acc.distance - dec.distance
        phases.append(acc)
        if cruise_d > 1e-6:
            phases.append(Phase(cruise_d / max(MIN_SPEED_MS, v_limit), cruise_d, v_limit, 0.0))
        phases.append(dec)

    phases = [p for p in phases if p.duration > 1e-9]
    covered = sum(p.distance for p in phases)
    # Absorb rounding into the cruise (or the longest) phase so the profile
    # always covers exactly the requested distance.
    drift = distance_m - covered
    if abs(drift) > 1e-6 and phases:
        idx = max(range(len(phases)), key=lambda i: phases[i].distance)
        ph = phases[idx]
        v_ref = max(MIN_SPEED_MS, ph.v0)
        phases[idx] = Phase(max(0.0, ph.duration + drift / v_ref),
                            max(0.0, ph.distance + drift), ph.v0, ph.accel)

    duration = sum(p.duration for p in phases)
    if duration <= 0 and distance_m > 0:
        # Degenerate geometry: fall back on a constant-speed run rather than
        # handing the event loop a zero or negative timeout.
        v_ref = max(MIN_SPEED_MS, (v0 + v1) / 2 or v_limit)
        duration = distance_m / v_ref
        phases = [Phase(duration, distance_m, v_ref, 0.0)]
    return Profile(tuple(phases), distance_m, duration, v0, v1, v_peak)


def run_time(distance_m: float, v_limit_ms: float, traction: Traction,
             v_entry_ms: float = 0.0, v_exit_ms: float = 0.0) -> float:
    """Convenience: how long this movement takes, in seconds."""
    return build_profile(distance_m, v_entry_ms, v_limit_ms, v_exit_ms, traction).duration
