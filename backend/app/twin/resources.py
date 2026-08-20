"""Contended infrastructure as SimPy resources.

Each junction / block / platform road is a capacity-1 SimPy resource plus a
headway gate: after a train clears, the next movement cannot enter until
`exit + headway`. Cross-route contention falls out naturally because conflicting
routes share the same resource object - the Diva express, the branch MEMU and the
goods chord all take the J-B turnout. Blocked resources make trains wait.

Block sections are held per running line, so opposing moves never contend for the
same block; only following moves on the same road do.
"""
from __future__ import annotations

import simpy

from ..network.net import resources as net_resources
from .state import OccupancyRecord

BLOCKED_FREE_AT = float("inf")


class ManagedResource:
    def __init__(self, env: simpy.Environment, resource_id: str,
                 headway_multiplier: float = 1.0, policy: str = "FIFO"):
        spec = net_resources[resource_id]
        self.id = resource_id
        self.kind = spec.kind
        self.base_headway = spec.headway_sec
        self.headway_multiplier = headway_multiplier
        self.policy = policy
        # FIFO is what an uncontrolled section does: whoever reaches the signal
        # first goes first. PRIORITY is the traditional dispatching rule -
        # always let the higher class through - which is the other baseline the
        # optimiser is measured against.
        self.res = (simpy.PriorityResource(env, capacity=spec.capacity)
                    if policy == "PRIORITY" else simpy.Resource(env, capacity=spec.capacity))
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


    def request(self, priority: int = 0):
        """Queue for this resource under the configured dispatching policy."""
        if self.policy == "PRIORITY":
            return self.res.request(priority=priority)
        return self.res.request()


def build_resources(env: simpy.Environment, headway_multiplier: float,
                    blocked: set[str], policy: str = "FIFO") -> dict[str, ManagedResource]:
    out: dict[str, ManagedResource] = {}
    for rid in net_resources:
        mr = ManagedResource(env, rid, headway_multiplier, policy)
        mr.blocked = rid in blocked
        out[rid] = mr
    return out
