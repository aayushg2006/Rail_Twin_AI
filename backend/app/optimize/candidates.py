"""Candidate action generation for a predicted conflict.

Every candidate is a bounded, real operational move - HOLD, SPEED_REGULATION or
PLATFORM_REASSIGNMENT - with an affected train, a magnitude and its
preconditions. No arbitrary actions are produced.

Which movement gives way is decided by what it costs the network: the train
carrying fewer passenger-minutes yields, with operating priority as the
tie-break. That is why a crowded fast local can hold its path against a lightly
loaded express, and why an empty goods rake always yields.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from ..network.fleet import fleet_by_id
from ..network.net import resources as net_resources
from ..network.routes import alternate_platforms
from ..twin.predict import (AnalyticState, Conflict, occupancy_seconds,
                            project_arrival)
from ..twin.state import AppliedAction

MIN_REGULATED_KMH = 15.0
GAP_MARGIN_SEC = 20.0


@dataclass
class Candidate:
    id: str
    letter: str
    title: str
    action: AppliedAction
    infrastructure_change: str          # NONE | LOW | MEDIUM
    infeasible_reason: str | None = None


def needed_gap(conflict: Conflict) -> float:
    """Extra separation the FOLLOWING movement needs to clear this conflict."""
    return math.ceil(conflict.required_separation_sec - conflict.separation_sec) + GAP_MARGIN_SEC


def hold_for(state: AnalyticState, conflict: Conflict, train_id: str) -> float:
    """How long this particular train must be held to clear the conflict.

    It depends entirely on which of the two it is. Holding the FOLLOWING
    movement by the separation shortfall works. Holding the LEADING movement by
    the same amount does not - it only closes the gap further. To take the
    leader out of the way it has to be held long enough to fall in BEHIND the
    other movement, which is a re-order, not a regulation.
    """
    if not conflict.train_b or train_id == conflict.train_b:
        return needed_gap(conflict)
    occ_lead = occupancy_seconds(state, conflict.train_a, conflict.resource_id)
    occ_follow = occupancy_seconds(state, conflict.train_b, conflict.resource_id)
    return math.ceil(occ_lead + max(0.0, conflict.separation_sec) + occ_follow
                     + conflict.required_separation_sec) + GAP_MARGIN_SEC


def _cost_rank(entry) -> tuple[float, int]:
    """Lower is cheaper to delay: passenger-minutes first, then priority."""
    return (entry.typical_load * entry.economic_weight, -entry.priority)


def _resource_s(state: AnalyticState, tid: str, resource_id: str) -> float | None:
    route = state.routes.get(tid)
    if route is None:
        return None
    for use in route.uses:
        if use.resource_id == resource_id:
            return use.enter_s
    return None


def generate(state: AnalyticState, conflict: Conflict) -> list[Candidate]:
    out: list[Candidate] = []
    a = fleet_by_id.get(conflict.train_a) if conflict.train_a else None
    b = fleet_by_id.get(conflict.train_b) if conflict.train_b else None

    if a and b:
        give, keep = (a, b) if _cost_rank(a) <= _cost_rank(b) else (b, a)
    else:
        give, keep = (a or b), None
    if give is None:
        return out

    st_give = state.trains.get(give.id)
    if st_give is None:
        return out

    give_hold = hold_for(state, conflict, give.id)
    use_s = _resource_s(state, give.id, conflict.resource_id)
    distance_m = (use_s - st_give.s) if use_s is not None else 0.0
    current_eta = project_arrival(state, give.id, use_s) if use_s is not None else None
    letters = iter("ABCDEFGH")

    # A. Regulate the give-way movement so it arrives after the conflict clears.
    if distance_m > 200 and current_eta and current_eta > 0:
        target_eta = current_eta + give_hold
        speed = round(max(5.0, min(st_give.line_speed_kmh,
                                   distance_m * 3.6 / target_eta)))
        if speed >= MIN_REGULATED_KMH:
            out.append(Candidate(
                "OPT-SPEED", next(letters), f"Regulate {give.number} to {speed} km/h",
                AppliedAction("SPEED_REGULATION", give.id, speed_kmh=speed), "NONE"))
        else:
            out.append(Candidate(
                "OPT-SPEED", next(letters), f"Regulate {give.number}",
                AppliedAction("SPEED_REGULATION", give.id, speed_kmh=speed), "NONE",
                f"Would need {speed} km/h, below the {MIN_REGULATED_KMH:.0f} km/h "
                "minimum for this section"))

    # B. Hold the give-way movement short of the contended resource.
    out.append(Candidate(
        "OPT-HOLD-GIVE", next(letters), f"Hold {give.number} for {round(give_hold)} s",
        AppliedAction("HOLD", give.id, hold_sec=give_hold), "NONE"))

    # C. Hold the priority movement instead - always feasible, usually worse,
    #    and kept so the controller can see what it would cost.
    if keep:
        hold_keep = round(hold_for(state, conflict, keep.id))
        out.append(Candidate(
            "OPT-HOLD-KEEP", next(letters), f"Hold {keep.number} for {hold_keep} s",
            AppliedAction("HOLD", keep.id, hold_sec=hold_keep), "NONE"))

    # D. Re-platform, when another face can take the movement.
    route = state.routes.get(give.id)
    spec = net_resources.get(conflict.resource_id)
    if route and route.platform_id and (spec is None or spec.kind == "PLATFORM"):
        alternates = [p for p in alternate_platforms(route)
                      if p not in state.blocked_resources]
        if alternates:
            target = alternates[0]
            out.append(Candidate(
                "OPT-PLATFORM", next(letters),
                f"Re-platform {give.number} to {target.replace('PF', 'PF ')}",
                AppliedAction("PLATFORM_REASSIGNMENT", give.id, platform_id=target),
                "LOW"))
        else:
            out.append(Candidate(
                "OPT-PLATFORM", next(letters), f"Re-platform {give.number}",
                AppliedAction("PLATFORM_REASSIGNMENT", give.id), "LOW",
                "No other face at Vasai Road can take this movement"))

    # E. When the resource itself is withdrawn, re-platforming the affected
    #    movement is the only thing that can clear it.
    if conflict.resource_id in state.blocked_resources and route and route.platform_id:
        alternates = [p for p in alternate_platforms(route)
                      if p not in state.blocked_resources]
        if alternates and not any(c.id == "OPT-PLATFORM" for c in out):
            out.append(Candidate(
                "OPT-PLATFORM-ALT", next(letters),
                f"Re-platform {give.number} to {alternates[0].replace('PF', 'PF ')}",
                AppliedAction("PLATFORM_REASSIGNMENT", give.id, platform_id=alternates[0]),
                "LOW"))
    return out
