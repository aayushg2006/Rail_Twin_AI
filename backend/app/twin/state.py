"""Digital-twin state model (Phase 3).

Rich internal representation: per-cause delay buckets, occupancy history and
causal chains that power explainability. The api/dto layer maps this down to the
exact frontend shapes in src/domain/types.ts.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field, replace


class TrainStatus(str, enum.Enum):
    SCHEDULED = "SCHEDULED"
    DEPARTED = "DEPARTED"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    HELD = "HELD"
    ARRIVING = "ARRIVING"
    DWELLING = "DWELLING"
    DEPARTED_STATION = "DEPARTED_STATION"
    DELAYED = "DELAYED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


@dataclass
class DelayBuckets:
    """Per-cause delay accumulation (seconds). Individual causes are never
    overwritten — total_delay is always their sum. Required for explainability.

    The seven causes named in the Phase-3 brief (base_schedule, dwell, block_wait,
    junction_wait, platform_wait, headway_wait, event) plus two controller-action
    causes (hold, regulation) so a controller decision's cost is attributable too.
    """
    base_schedule: float = 0.0
    dwell: float = 0.0
    block_wait: float = 0.0
    junction_wait: float = 0.0
    platform_wait: float = 0.0
    headway_wait: float = 0.0
    event: float = 0.0
    hold: float = 0.0
    regulation: float = 0.0

    @property
    def total(self) -> float:
        return (self.base_schedule + self.dwell + self.block_wait + self.junction_wait
                + self.platform_wait + self.headway_wait + self.event
                + self.hold + self.regulation)

    def add(self, cause: str, seconds: float) -> None:
        setattr(self, cause, getattr(self, cause) + seconds)

    def as_dict(self) -> dict[str, float]:
        return {
            "base_schedule": self.base_schedule, "dwell": self.dwell,
            "block_wait": self.block_wait, "junction_wait": self.junction_wait,
            "platform_wait": self.platform_wait, "headway_wait": self.headway_wait,
            "event": self.event, "hold": self.hold, "regulation": self.regulation,
            "total": self.total,
        }

    def copy(self) -> "DelayBuckets":
        return replace(self)


# Which delay bucket a wait over a resource kind lands in.
RESOURCE_WAIT_BUCKET = {"JUNCTION": "junction_wait", "BLOCK": "block_wait", "PLATFORM": "platform_wait"}


@dataclass
class TrainRuntime:
    """Live state of one train inside the twin."""
    train_id: str
    route_id: str                      # effective route (may differ from fleet default after reroute)
    s: float                           # distance along route path, map units
    speed_kmh: float
    nominal_speed_kmh: float
    priority: int
    ttype: str
    status: TrainStatus = TrainStatus.RUNNING
    next_stop_index: int = 0
    finished: bool = False
    delays: DelayBuckets = field(default_factory=DelayBuckets)

    # motion sampling — interpolate position at arbitrary env.now
    move_s0: float = 0.0
    move_s1: float = 0.0
    move_t0: float = 0.0
    move_t1: float = 0.0
    moving: bool = False

    # time-boxed phases (for remaining-seconds in the DTO)
    dwell_end_t: float = 0.0
    hold_end_t: float = 0.0
    wait_end_t: float | None = None    # None => indefinite (blocked)

    # controller-imposed hold queued but not yet consumed
    pending_hold_sec: float = 0.0
    # portion of the pending hold that is an external event delay (attributed to `event`)
    pending_event_hold: float = 0.0

    def sample_s(self, now: float) -> float:
        if self.moving and self.move_t1 > self.move_t0:
            if now <= self.move_t0:
                return self.move_s0
            if now >= self.move_t1:
                return self.move_s1
            t = (now - self.move_t0) / (self.move_t1 - self.move_t0)
            return self.move_s0 + (self.move_s1 - self.move_s0) * t
        return self.s

    def dwell_remaining(self, now: float) -> float:
        return max(0.0, self.dwell_end_t - now) if self.status == TrainStatus.DWELLING else 0.0

    def hold_remaining(self, now: float) -> float:
        return max(0.0, self.hold_end_t - now) if self.status == TrainStatus.HELD else 0.0


@dataclass
class OccupancyRecord:
    resource_id: str
    train_id: str
    enter: float
    exit: float | None = None


@dataclass
class CausalLink:
    """One edge in the delay-dependency graph — required for explainable delay."""
    cause_type: str        # e.g. JUNCTION_OCCUPANCY, HEADWAY, BLOCK_OCCUPANCY, EVENT
    cause_entity: str      # train id or resource id that caused it
    affected_train: str
    resource: str
    added_delay_seconds: float
    timestamp: float

    def as_dict(self) -> dict:
        return {
            "cause_type": self.cause_type, "cause_entity": self.cause_entity,
            "affected_train": self.affected_train, "resource": self.resource,
            "added_delay_seconds": round(self.added_delay_seconds, 2),
            "timestamp": round(self.timestamp, 2),
        }


@dataclass
class AppliedAction:
    kind: str
    train_id: str
    speed_kmh: float | None = None
    hold_sec: float | None = None
    route_id: str | None = None
    platform_id: str | None = None

    def as_dict(self) -> dict:
        out: dict = {"kind": self.kind, "trainId": self.train_id}
        if self.speed_kmh is not None:
            out["speedKmh"] = self.speed_kmh
        if self.hold_sec is not None:
            out["holdSec"] = self.hold_sec
        if self.route_id is not None:
            out["routeId"] = self.route_id
        if self.platform_id is not None:
            out["platformId"] = self.platform_id
        return out
