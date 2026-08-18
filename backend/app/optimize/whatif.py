"""What-if evaluation (Phase 5).

For each candidate: clone the analytic state, apply the action, project the twin
forward over the horizon, detect residual conflicts, compute the delta metrics and
run safety validation. Never mutates the live state. Produces the exact
OptionOutcome shape the console's Options table renders.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..config import settings
from ..network.fleet import fleet_by_id
from ..twin.predict import (AnalyticState, Conflict, apply_action, predict,
                            project_finish)
from ..twin.state import AppliedAction
from .candidates import Candidate
from .safety import validate

HORIZON = settings.default_horizon_sec


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
    throughput_delta: int
    infrastructure_change: str
    safety: dict
    residual_conflicts: int
    feasible: bool
    infeasible_reason: str | None = None

    def as_dict(self) -> dict:
        return {
            "id": self.id, "letter": self.letter, "title": self.title,
            "action": self.action.as_dict(),
            "conflictResolved": self.conflict_resolved,
            "addedDelaySec": {k: round(v, 1) for k, v in self.added_delay_sec.items()},
            "networkDelaySec": round(self.network_delay_sec, 1),
            "passengerDelaySec": round(self.passenger_delay_sec, 1),
            "freightDelaySec": round(self.freight_delay_sec, 1),
            "throughputDelta": self.throughput_delta,
            "infrastructureChange": self.infrastructure_change,
            "safety": self.safety,
            "residualConflicts": self.residual_conflicts,
            "feasible": self.feasible,
            **({"infeasibleReason": self.infeasible_reason} if self.infeasible_reason else {}),
        }


def delay_profile(state: AnalyticState) -> dict[str, float]:
    out: dict[str, float] = {}
    for tid, st in state.trains.items():
        finish = project_finish(state, tid)
        out[tid] = st.delay_sec + (finish or 0.0)
    return out


def throughput_within(state: AnalyticState, horizon: float) -> int:
    count = 0
    for tid in state.trains:
        t = project_finish(state, tid)
        if t is not None and t <= horizon:
            count += 1
    return count


def evaluate(base: AnalyticState, base_delays: dict[str, float], cand: Candidate,
             conflict: Conflict, horizon: float = HORIZON) -> OptionEval:
    if cand.infeasible_reason:
        return OptionEval(cand.id, cand.letter, cand.title, cand.action, False, {}, 0, 0, 0, 0,
                          cand.infrastructure_change, {"passed": False, "checks": []}, 0, False,
                          cand.infeasible_reason)

    after = apply_action(base, cand.action)
    pred = predict(after, horizon)
    # Resolve the selected causal conflict locally. Independent conflicts remain
    # visible as residual network work and do not make this action unsafe.
    def same_causal_conflict(other: Conflict) -> bool:
        if other.resource_id != conflict.resource_id:
            return False
        if not conflict.train_b:
            return other.train_a in {conflict.train_a, cand.action.train_id}
        return {other.train_a, other.train_b} == {conflict.train_a, conflict.train_b}

    still = any(c.severity == "CRITICAL" and same_causal_conflict(c) for c in pred.conflicts)
    resolved = not still
    after_delays = delay_profile(after)
    added: dict[str, float] = {}
    network = passenger = freight = 0.0
    for tid in base.trains:
        d = after_delays.get(tid, 0.0) - base_delays.get(tid, 0.0)
        if abs(d) < 1:
            continue
        added[tid] = d
        network += d
        if fleet_by_id[tid].type == "FREIGHT":
            freight += d
        else:
            passenger += d
    residual = sum(1 for c in pred.conflicts if c.severity == "CRITICAL")
    base_thru = throughput_within(base, horizon)
    after_thru = throughput_within(after, horizon)
    return OptionEval(
        cand.id, cand.letter, cand.title, cand.action, resolved, added,
        network, passenger, freight, after_thru - base_thru,
        cand.infrastructure_change, validate(cand.action, after, conflict, resolved),
        residual, True)
