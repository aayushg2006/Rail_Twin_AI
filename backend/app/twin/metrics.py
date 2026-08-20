"""KPI computation.

Every metric here is measured against the published timetable or against real
occupancy history. The previous set contained several quantities that could not
move: `earliness` was hardcoded to 0.0, `platform_utilisation` was an
instantaneous count that was almost always 0, `throughput` counted trains that
had not finished yet, and `recovery_time` was a separation deficit with the word
"seconds" attached to it.

Definitions
    lateness            actual departure from BSR minus booked departure
                        (negative = early, and earliness is now reportable)
    passenger_minutes   lateness x typical load, summed - the quantity the
                        optimiser actually minimises, and the one that makes a
                        crowded fast local outrank a lightly loaded express
    throughput/hour     platform departures observed in the last 3600 s
    platform_util       occupied time / available time over the last 3600 s
    on_time_percent     share of departures within 3 minutes of booked time
"""
from __future__ import annotations

from dataclasses import dataclass

from ..network.fleet import fleet_by_id, passenger_load
from ..network.net import platforms
from .predict import AnalyticState, Prediction

ON_TIME_THRESHOLD_SEC = 180.0
UTILISATION_WINDOW_SEC = 3600.0


@dataclass
class KPISet:
    active_conflicts: int
    predicted_conflicts: int
    trains_tracked: int
    total_lateness_sec: float
    earliness_sec: float
    average_lateness_sec: float
    passenger_delay_sec: float
    freight_delay_sec: float
    passenger_minutes: float
    throughput_per_hour: int | None
    platform_utilisation: float
    on_time_percent: float | None
    departures_measured: int
    scheduled_ahead: int


def occupied_platforms(state: AnalyticState) -> dict[str, str]:
    """Which platform face each standing train is occupying right now."""
    out: dict[str, str] = {}
    for tid, st in state.trains.items():
        if st.finished or not st.admitted:
            continue
        route = state.routes.get(tid)
        if route is None or not route.stops:
            continue
        stop = route.stops[0]
        if st.dwell_remaining > 0 or abs(st.s - stop.s) < 300:
            out[stop.platform_id] = tid
    return out


def platform_utilisation(engine, now: float) -> float:
    """Occupied time over available time across all faces in the last hour."""
    window_start = max(0.0, now - UTILISATION_WINDOW_SEC)
    span = now - window_start
    if span <= 0:
        return 0.0
    occupied = 0.0
    for pid in platforms:
        mr = engine.resources.get(pid)
        if mr is None:
            continue
        for rec in mr.occupancy:
            enter = max(rec.enter, window_start)
            exit_t = min(rec.exit if rec.exit is not None else now, now)
            if exit_t > enter:
                occupied += exit_t - enter
    return min(1.0, occupied / (span * len(platforms)))


def compute_kpis(engine, state: AnalyticState, prediction: Prediction,
                 common_trains: set[str] | None = None) -> KPISet:
    """`common_trains` restricts every figure to a shared set of services.

    The three tracks on the RECORD page (AI, do-nothing, priority rule) can hold
    DIFFERENT trains at the same instant: a held train stays in section longer,
    so it inflates the AI denominator and makes "average lateness" incomparable.
    Passing the intersection of admitted trains makes the columns mean the same
    thing.
    """
    active = [st for st in state.trains.values()
              if st.admitted and not st.finished
              and (common_trains is None or st.train_id in common_trains)]

    lateness = {tid: rt.lateness_sec(engine.now, engine.service_epoch_sec)
                for tid, rt in engine.trains.items()
                if rt.admitted and not rt.finished
                and (common_trains is None or tid in common_trains)}
    late_values = list(lateness.values())
    total_late = sum(v for v in late_values if v > 0)
    early = -sum(v for v in late_values if v < 0)

    passenger = sum(v for tid, v in lateness.items()
                    if v > 0 and not (fleet_by_id[tid].is_freight if tid in fleet_by_id else False))
    freight = sum(v for tid, v in lateness.items()
                  if v > 0 and (fleet_by_id[tid].is_freight if tid in fleet_by_id else False))
    passenger_minutes = sum(max(0.0, v) * passenger_load(tid) / 60.0
                            for tid, v in lateness.items())

    # Throughput and punctuality are measured on departures that have actually
    # happened, so both stay empty until the twin has observed some.
    horizon_start = engine.service_seconds - UTILISATION_WINDOW_SEC
    departed = [rt for rt in engine.trains.values()
                if rt.actual_dep_sec is not None and rt.actual_dep_sec >= horizon_start]
    on_time = sum(1 for rt in departed
                  if abs(rt.actual_dep_sec - rt.booked_dep_sec) <= ON_TIME_THRESHOLD_SEC)

    critical = sum(1 for c in prediction.conflicts if c.severity == "CRITICAL")
    warnings = sum(1 for c in prediction.conflicts if c.severity == "WARNING")
    scheduled = sum(1 for st in state.trains.values() if not st.admitted and not st.finished)

    return KPISet(
        active_conflicts=critical,
        predicted_conflicts=warnings,
        trains_tracked=len(active),
        total_lateness_sec=round(total_late, 1),
        earliness_sec=round(early, 1),
        average_lateness_sec=round(total_late / len(active), 1) if active else 0.0,
        passenger_delay_sec=round(passenger, 1),
        freight_delay_sec=round(freight, 1),
        passenger_minutes=round(passenger_minutes, 1),
        throughput_per_hour=len(departed) if departed else None,
        platform_utilisation=round(platform_utilisation(engine, engine.now), 4),
        on_time_percent=round(on_time / len(departed) * 100, 1) if departed else None,
        departures_measured=len(departed),
        scheduled_ahead=scheduled,
    )
