"""Contended infrastructure as SimPy resources (Phase 3).

Each junction / block / platform is a capacity-1 SimPy resource plus a headway
gate: after a train clears, the next movement cannot enter until
`exit + headway`. Cross-route junction contention falls out naturally because
conflicting routes share the same resource object (e.g. both the Diva express and
the goods chord use resource JB). Blocked resources make trains wait.
"""
from __future__ import annotations

import simpy

from ..network.topology import resource_by_id
from .state import OccupancyRecord

BLOCKED_FREE_AT = float("inf")


class ManagedResource:
    def __init__(self, env: simpy.Environment, resource_id: str, headway_multiplier: float = 1.0):
        spec = resource_by_id[resource_id]
        self.id = resource_id
        self.kind = spec.kind
        self.base_headway = spec.headway_sec
        self.headway_multiplier = headway_multiplier
        self.res = simpy.Resource(env, capacity=spec.capacity)
        self.free_at = 0.0            # earliest time the next mover may enter
        self.blocked = False
        self.occupancy: list[OccupancyRecord] = []

    @property
    def headway(self) -> float:
        return self.base_headway * self.headway_multiplier

    def gate_wait(self, now: float) -> float:
        """Seconds a train must wait for interlocking headway/clearance."""
        if self.blocked:
            return BLOCKED_FREE_AT
        return max(0.0, self.free_at - now)

    def on_enter(self, train_id: str, now: float) -> OccupancyRecord:
        rec = OccupancyRecord(self.id, train_id, enter=now)
        self.occupancy.append(rec)
        return rec

    def on_exit(self, rec: OccupancyRecord, now: float) -> None:
        rec.exit = now
        self.free_at = now + self.headway

    def snapshot_occupant(self, now: float) -> str | None:
        for rec in reversed(self.occupancy):
            if rec.exit is None or rec.exit >= now:
                if rec.enter <= now:
                    return rec.train_id
        return None


def build_resources(env: simpy.Environment, headway_multiplier: float,
                    blocked: set[str]) -> dict[str, ManagedResource]:
    out: dict[str, ManagedResource] = {}
    for rid in resource_by_id:
        mr = ManagedResource(env, rid, headway_multiplier)
        mr.blocked = rid in blocked
        out[rid] = mr
    return out
