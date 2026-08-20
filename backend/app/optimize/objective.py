"""The objective the optimiser minimises.

    J = W_conflict * residual_critical_conflicts     (hard, dominates everything)
      + W_passenger * passenger_minutes_added
      + W_freight   * freight_tonne_minutes_added
      + W_hold      * seconds of imposed hold
      + W_route     * infrastructure changes
      - W_throughput * trains cleared within the horizon

Passenger-minutes is the primary term because it is the quantity a section
controller is really trading: delaying a crowded fast local by one minute costs
2,600 passenger-minutes, while delaying a lightly loaded premium express by the
same minute costs 900. That relationship is measured from the timetable, not
asserted by a hand-tuned priority number.

Weights come from config so they are tunable without touching code.
"""
from __future__ import annotations

from ..config import ObjectiveWeights, settings
from .whatif import OptionEval

RESIDUAL_CONFLICT_PENALTY = 1_000_000.0


def option_cost(ev: OptionEval, w: ObjectiveWeights | None = None) -> float:
    w = w or settings.weights
    hold_sec = ev.action.hold_sec or 0.0
    route_change = 1 if ev.infrastructure_change != "NONE" else 0
    # Only added delay is penalised. A candidate must never earn credit for a
    # negative figure - that used to let a re-route score better than doing
    # nothing at all.
    return (
        w.conflict * RESIDUAL_CONFLICT_PENALTY * ev.residual_conflicts
        + w.passenger * max(0.0, ev.passenger_minutes)
        + w.freight * max(0.0, ev.freight_tonne_minutes)
        + w.hold * hold_sec
        + w.route * route_change
        - w.throughput * ev.throughput_delta
    )


def explain_cost(ev: OptionEval, w: ObjectiveWeights | None = None) -> dict:
    """The same figure broken into its terms, for the console's Why panel."""
    w = w or settings.weights
    hold_sec = ev.action.hold_sec or 0.0
    route_change = 1 if ev.infrastructure_change != "NONE" else 0
    return {
        "residualConflicts": ev.residual_conflicts,
        "passengerMinutes": round(max(0.0, ev.passenger_minutes), 1),
        "freightTonneMinutes": round(max(0.0, ev.freight_tonne_minutes), 1),
        "holdSec": round(hold_sec, 1),
        "routeChanges": route_change,
        "throughputDelta": ev.throughput_delta,
        "total": round(option_cost(ev, w), 1),
    }
