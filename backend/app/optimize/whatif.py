"""What-if evaluation.

For each candidate: clone the analytic state, apply the action, re-project the
whole network over the horizon, count residual conflicts, compute the delta
metrics and run safety validation. Never mutates the live state.

The headline metric is PASSENGER-MINUTES - added delay multiplied by the people
on board - because that is what a controller is actually trading. Freight is
valued separately in tonne-minutes so a loaded container rake is not treated as
worthless just because nobody is sitting in it.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..config import settings
from ..network.fleet import fleet_by_id
from ..twin.predict import (AnalyticState, Conflict, apply_action, predict,
                            project_finish_free, queue_rollout)
from ..twin.state import AppliedAction
from .candidates import Candidate
from .safety import validate

HORIZON = settings.default_horizon_sec
SIGNIFICANT_SEC = 1.0


@dataclass
class OptionEval:
    id: str
    letter: str
    title: str
    action: AppliedAction
    conflict_resolved: bool
    added_delay_sec: dict[str, float]
    network_delay_sec: float
    passenger_delay_sec: float
    freight_delay_sec: float
    passenger_minutes: float
    freight_tonne_minutes: float
    weighted_delay_sec: float
    throughput_delta: int
    infrastructure_change: str
    safety: dict
    residual_conflicts: int
    feasible: bool
    infeasible_reason: str | None = None
    response_class: str = "RESOLUTION"
    # Absolute count of CRITICAL conflicts left on the network after this
    # action, kept so `residual_conflicts` can be expressed as a delta.
    critical_conflicts: int = 0
    # Queue-aware no-command comparison for this exact frame.  Exposing the
    # saving lets the console report the useful result rather than an
    # ambiguous absolute queue-delay figure.
    reference_network_delay_sec: float = 0.0
    network_delay_saving_sec: float = 0.0

    def as_dict(self) -> dict:
        return {
            "id": self.id, "letter": self.letter, "title": self.title,
            "action": self.action.as_dict(),
            "conflictResolved": self.conflict_resolved,
            "addedDelaySec": {k: round(v, 1) for k, v in self.added_delay_sec.items()},
            "networkDelaySec": round(self.network_delay_sec, 1),
            "referenceNetworkDelaySec": round(self.reference_network_delay_sec, 1),
            "networkDelaySavingSec": round(self.network_delay_saving_sec, 1),
            "passengerDelaySec": round(self.passenger_delay_sec, 1),
            "freightDelaySec": round(self.freight_delay_sec, 1),
            "passengerMinutes": round(self.passenger_minutes, 1),
            "freightTonneMinutes": round(self.freight_tonne_minutes, 1),
            "weightedDelaySec": round(self.weighted_delay_sec, 1),
            "throughputDelta": self.throughput_delta,
            "infrastructureChange": self.infrastructure_change,
            "safety": self.safety,
            "residualConflicts": self.residual_conflicts,
            "criticalConflicts": self.critical_conflicts,
            "feasible": self.feasible,
            "responseClass": self.response_class,
            **({"infeasibleReason": self.infeasible_reason} if self.infeasible_reason else {}),
        }


def delay_profile(state: AnalyticState) -> dict[str, float]:
    """Conflict-free finish profile used as the common comparison datum."""
    out: dict[str, float] = {}
    for tid, st in state.trains.items():
        if st.finished:
            continue
        finish = project_finish_free(state, tid)
        if finish is not None:
            out[tid] = finish
    return out


def throughput_within(state: AnalyticState, horizon: float) -> int:
    rollout = queue_rollout(state, horizon_sec=horizon)
    return sum(1 for t in rollout.finish_sec.values() if t <= horizon)


def evaluate_do_nothing(base: AnalyticState, base_delays: dict[str, float],
                        conflict: Conflict, horizon: float = HORIZON) -> OptionEval:
    """Literal no-command reference through the shared queue scheduler.

    The protecting signal wait now emerges from resource availability and
    propagates through every downstream queue.  No synthetic follower hold is
    inserted, so candidates and their reference finally share one model.
    """
    forced = Candidate(
        "OPT-NONE", "-", "Take no action (interlocking/FIFO)",
        AppliedAction("NO_ACTION", conflict.train_b or conflict.train_a), "NONE")
    result = evaluate(base, base_delays, forced, conflict, horizon, reference=None)
    # Existing critical episodes are the reference, not a penalty charged only
    # to FIFO. `critical_conflicts` retains their absolute count so candidates
    # can be scored by the delta they create or remove.
    result.residual_conflicts = 0
    return result


def evaluate(base: AnalyticState, base_delays: dict[str, float], cand: Candidate,
             conflict: Conflict, horizon: float = HORIZON,
             response_class: str = "RESOLUTION",
             reference: "OptionEval | None" = None) -> OptionEval:
    if cand.infeasible_reason:
        return OptionEval(
            cand.id, cand.letter, cand.title, cand.action, False, {},
            0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0,
            cand.infrastructure_change,
            {"passed": False, "checks": [], "mode": response_class},
            0, False, cand.infeasible_reason, response_class, 0)

    after = apply_action(base, cand.action)
    pred = predict(after, horizon)

    def same_conflict(other: Conflict) -> bool:
        if other.resource_id != conflict.resource_id:
            return False
        if not conflict.train_b:
            return other.train_a in {conflict.train_a, cand.action.train_id}
        return {other.train_a, other.train_b} == {conflict.train_a, conflict.train_b}

    still = any(c.severity == "CRITICAL" and same_conflict(c) for c in pred.conflicts)
    resolved = not still
    # Containment protects the movement but must not claim full resolution.
    reported_resolved = resolved if response_class == "RESOLUTION" else False

    rollout = queue_rollout(after, horizon_sec=horizon)
    after_delays = rollout.finish_sec
    added: dict[str, float] = {}
    network = passenger = freight = weighted = 0.0
    pax_minutes = tonne_minutes = 0.0
    for tid in base.trains:
        if tid not in base_delays or tid not in after_delays:
            continue
        d = after_delays[tid] - base_delays[tid]
        if abs(d) < SIGNIFICANT_SEC:
            continue
        f = fleet_by_id.get(tid)
        if f is None:
            continue
        added[tid] = d
        network += d
        weighted += d * f.economic_weight
        if f.is_freight:
            freight += d
            tonne_minutes += d * f.gross_tonnes / 60.0
        else:
            passenger += d
            pax_minutes += d * f.typical_load / 60.0

    critical_after = sum(1 for c in pred.conflicts if c.severity == "CRITICAL")
    # Residual conflicts count RELATIVE to taking no action. As an absolute
    # network-wide total it was near-identical across every option, so the
    # `w.conflict * 1e6` term never discriminated between them and an action
    # that CREATED a conflict was not penalised at all.
    residual = (max(0, critical_after - reference.critical_conflicts)
                if reference is not None else critical_after)
    thru_delta = throughput_within(after, horizon) - throughput_within(base, horizon)

    reference_network = reference.network_delay_sec if reference is not None else network
    return OptionEval(
        cand.id, cand.letter, cand.title, cand.action, reported_resolved, added,
        network, passenger, freight, pax_minutes, tonne_minutes, weighted,
        thru_delta, cand.infrastructure_change,
        validate(cand.action, after, conflict, resolved, response_class, before=base),
        residual, True, None, response_class, critical_after,
        reference_network, reference_network - network)
