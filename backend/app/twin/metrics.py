"""KPI / metric computation (Phase 3). Definitions are documented and match the
frontend KPISet so the console renders them unchanged.

    total_delay      = Σ current delay over active trains (seconds)
    throughput/hour  = trains projected to clear within 3600 s
    platform_util    = occupied platform faces / 7
    on_time_percent  = share of active trains with delay <= 180 s
    utilisation      = occupied_time / available_time (per resource; aggregated elsewhere)
"""
from __future__ import annotations

from dataclasses import dataclass

from ..network.topology import platforms, route_by_id
from .predict import AnalyticState, Prediction, project_finish


@dataclass
class KPISet:
    active_conflicts: int
    trains_tracked: int
    total_delay_sec: float
    average_delay_sec: float
    passenger_delay_sec: float
    freight_delay_sec: float
    throughput_per_hour: int | None
    platform_utilisation: float
    on_time_percent: float
    recovery_time_sec: float | None
    predicted_conflicts: int = 0


def occupied_platforms(state: AnalyticState) -> dict[str, str]:
    out: dict[str, str] = {}
    for tid, st in state.trains.items():
        if st.finished:
            continue
        route = route_by_id[st.route_id]
        for stop in route.stops:
            if abs(st.s - stop.s) < 40:
                out[stop.platform_id] = tid
    return out


def compute_kpis(state: AnalyticState, prediction: Prediction) -> KPISet:
    active = [st for st in state.trains.values() if not st.finished]
    total = sum(st.delay_sec for st in active)
    passenger = sum(st.delay_sec for st in active if st.ttype not in ("FREIGHT", "SHUNT"))
    freight = sum(st.delay_sec for st in active if st.ttype == "FREIGHT")
    on_time = sum(1 for st in active if st.delay_sec <= 180)
    occupied = occupied_platforms(state)
    cleared = 0
    for st in active:
        t = project_finish(state, st.train_id)
        if t is not None and t <= 3600:
            cleared += 1

    critical = sum(1 for c in prediction.conflicts if c.severity == "CRITICAL")
    warnings = sum(1 for c in prediction.conflicts if c.severity == "WARNING")
    recovery = None
    if prediction.conflicts:
        worst = max(c.required_separation_sec - c.separation_sec for c in prediction.conflicts)
        recovery = round(worst + (state.headway_multiplier - 1) * 120)

    return KPISet(
        active_conflicts=critical,
        trains_tracked=len(active),
        total_delay_sec=total,
        average_delay_sec=(total / len(active)) if active else 0.0,
        passenger_delay_sec=passenger,
        freight_delay_sec=freight,
        throughput_per_hour=cleared if cleared > 0 else None,
        platform_utilisation=len(occupied) / len(platforms),
        on_time_percent=(on_time / len(active) * 100) if active else 0.0,
        recovery_time_sec=recovery,
        predicted_conflicts=warnings,
    )
