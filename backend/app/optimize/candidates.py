"""Candidate action generation for a predicted conflict (Phase 5).

Every candidate is a bounded, meaningful operational move — HOLD, SPEED_REGULATION,
ALTERNATE_ROUTE, PLATFORM_REASSIGNMENT — with an affected train, a magnitude, a
reason and its preconditions. No arbitrary actions are produced. Mirrors the
frontend option set so the Options table is populated by the backend.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from ..network.fleet import fleet_by_id
from ..network.topology import resource_by_id, route_by_id, route_resource_use
from ..twin.predict import AnalyticState, Conflict, occupancy_seconds, project_arrival
from ..twin.state import AppliedAction


@dataclass
class Candidate:
    id: str
    letter: str
    title: str
    action: AppliedAction
    infrastructure_change: str          # NONE | LOW | MEDIUM
    infeasible_reason: str | None = None


def needed_gap(conflict: Conflict) -> float:
    return math.ceil(conflict.required_separation_sec - conflict.separation_sec) + 20


def generate(state: AnalyticState, conflict: Conflict) -> list[Candidate]:
    out: list[Candidate] = []
    a = fleet_by_id.get(conflict.train_a) if conflict.train_a else None
    b = fleet_by_id.get(conflict.train_b) if conflict.train_b else None
    # The train that gives way is the lower-priority movement (higher number).
    if a and b:
        give = a if a.priority >= b.priority else b
        keep = b if give is a else a
    else:
        give = a or b
        keep = None
    if give is None:
        return out

    gap = needed_gap(conflict)
    st_give = state.trains.get(give.id)
    if st_give is None:
        return out
    give_route = st_give.route_id
    use = next((u for u in route_resource_use.get(give_route, [])
                if u.resource_id == conflict.resource_id), None)
    distance = (use.s - st_give.s) if use else 0.0
    current_eta = project_arrival(state, give.id, use.s if use else st_give.s) or 0.0

    letters = iter("ABCDEFGH")

    # A. Speed regulation on the give-way movement.
    if distance > 40 and current_eta > 0:
        target_eta = current_eta + gap
        speed = round(max(10.0, min(st_give.nominal_speed_kmh, (distance * 10 * 3.6) / target_eta)))
        if speed >= 15:
            out.append(Candidate("OPT-SPEED", next(letters), f"Regulate {give.id} to {speed} km/h",
                                  AppliedAction("SPEED_REGULATION", give.id, speed_kmh=speed), "NONE"))
        else:
            out.append(Candidate("OPT-SPEED", next(letters), f"Regulate {give.id}",
                                  AppliedAction("SPEED_REGULATION", give.id, speed_kmh=speed), "NONE",
                                  "Required speed is below the permissible minimum for this section"))

    # B. Hold the give-way movement short of the resource.
    out.append(Candidate("OPT-HOLD-GIVE", next(letters), f"Hold {give.id} for {round(gap)} s",
                         AppliedAction("HOLD", give.id, hold_sec=gap), "NONE"))

    # C. Hold the priority movement instead — feasible, usually worse.
    if keep:
        occ_a = occupancy_seconds(state, conflict.train_a, conflict.resource_id) if conflict.train_a else 0.0
        occ_b = occupancy_seconds(state, conflict.train_b, conflict.resource_id) if conflict.train_b else 0.0
        hold_earlier = round(occ_a + conflict.separation_sec + occ_b + conflict.required_separation_sec)
        hold_keep = hold_earlier if keep.id == conflict.train_a else round(gap)
        out.append(Candidate("OPT-HOLD-KEEP", next(letters), f"Hold {keep.id} for {hold_keep} s",
                             AppliedAction("HOLD", keep.id, hold_sec=hold_keep), "NONE"))

    # D. Alternate route, where one physically exists for that train.
    if give.alternate_route_ids:
        alt = next((r for r in give.alternate_route_ids if r not in state.unavailable_routes), None)
        if alt:
            out.append(Candidate("OPT-ROUTE", next(letters),
                                 f"Route {give.id} via {route_by_id[alt].label}",
                                 AppliedAction("ALTERNATE_ROUTE", give.id, route_id=alt), "LOW"))
        else:
            out.append(Candidate("OPT-ROUTE", next(letters), f"Route {give.id} via goods loop",
                                 AppliedAction("ALTERNATE_ROUTE", give.id), "LOW",
                                 "Goods loop is occupied in the current state"))

    # E. Platform re-assignment — only when the contended resource is a platform.
    if resource_by_id.get(conflict.resource_id) and resource_by_id[conflict.resource_id].kind == "PLATFORM":
        target = give.alternate_platform_ids[0] if give.alternate_platform_ids else None
        out.append(Candidate("OPT-PLATFORM", next(letters),
                             f"Re-platform {give.id} to {target}" if target else f"Re-platform {give.id}",
                             AppliedAction("PLATFORM_REASSIGNMENT", give.id, platform_id=target), "MEDIUM",
                             None if target else "No alternate face serves this train's direction at Vasai Road"))
    return out
