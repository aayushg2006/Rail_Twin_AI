"""Configurable weighted objective (Phase 5).

J = W_delay*(passenger*k + freight)   # passenger impact weighted k× freight
  + W_conflict*residual_conflicts
  + W_headway*headway_violations
  + W_platform*platform_conflicts
  + W_route*route_changes
  + W_hold*hold_seconds

Weights come from config (ObjectiveWeights) so they are tunable without code edits.
The optimizer minimises J; hard constraints are enforced separately and never
traded away for a lower J.
"""
from __future__ import annotations

from ..config import ObjectiveWeights, settings
from .whatif import OptionEval


def option_cost(ev: OptionEval, w: ObjectiveWeights | None = None) -> float:
    w = w or settings.weights
    hold_sec = ev.action.hold_sec or 0.0
    route_change = 1 if ev.infrastructure_change != "NONE" else 0
    # Lexicographic surrogate: safety/residual conflicts are hard constraints in
    # the solver, then throughput, then positive lateness. A faster what-if must
    # never receive a fake benefit merely because a signed value became negative.
    passenger_component = max(0.0, ev.passenger_delay_sec) * w.passenger_vs_freight
    freight_component = max(0.0, ev.freight_delay_sec)
    positive_network = max(0.0, ev.network_delay_sec)
    return (
        w.conflict * 1_000_000 * ev.residual_conflicts
        - 1_000_000 * ev.throughput_delta
        + w.delay * (positive_network + passenger_component + freight_component)
        + w.route * route_change
        + w.hold * hold_sec
    )
