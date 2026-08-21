"""Digital-twin state model.

Rich internal representation: per-cause delay buckets, occupancy history and
causal chains that power explainability.

Position is CHAINAGE IN METRES along the train's route, and speed is sampled
from the live motion profile rather than being a stored constant, so both are
real physical quantities. Delay is lateness against the booked time from the
published timetable - not accumulated waiting, which is what the old model
reported and which no controller would recognise.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field, replace

from .dynamics import Profile


class TrainStatus(str, enum.Enum):
    SCHEDULED = "SCHEDULED"        # booked, not yet in the modelled area
    APPROACHING = "APPROACHING"
    RUNNING = "RUNNING"
    REGULATED = "REGULATED"        # running under an imposed speed restriction
    WAITING = "WAITING"            # held at a signal for a resource
    HELD = "HELD"                  # held by a controller decision
    ARRIVING = "ARRIVING"
    DWELLING = "DWELLING"
    DEPARTED = "DEPARTED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


@dataclass
class DelayBuckets:
    """Per-cause delay accumulation (seconds), never overwritten, so the total is
    always attributable. `entry` is lateness the service already carried when it
    reached the modelled area - from a live observation, never a constant."""
    entry: float = 0.0
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
        return (self.entry + self.dwell + self.block_wait + self.junction_wait
                + self.platform_wait + self.headway_wait + self.event
                + self.hold + self.regulation)

    def add(self, cause: str, seconds: float) -> None:
        setattr(self, cause, getattr(self, cause) + seconds)

    def as_dict(self) -> dict[str, float]:
        return {
            "entry": round(self.entry, 1), "dwell": round(self.dwell, 1),
            "block_wait": round(self.block_wait, 1),
            "junction_wait": round(self.junction_wait, 1),
            "platform_wait": round(self.platform_wait, 1),
            "headway_wait": round(self.headway_wait, 1),
            "event": round(self.event, 1), "hold": round(self.hold, 1),
            "regulation": round(self.regulation, 1), "total": round(self.total, 1),
        }

    def copy(self) -> "DelayBuckets":
        return replace(self)


RESOURCE_WAIT_BUCKET = {
    "JUNCTION": "junction_wait", "BLOCK": "block_wait", "PLATFORM": "platform_wait",
}


@dataclass
class TrainRuntime:
    """Live state of one train inside the twin. `s` is metres along its route."""
    train_id: str
    route_id: str
    s: float
    speed_ms: float
    line_speed_kmh: float
    service_class: str
    priority: int
    category: str
    booked_dep_sec: int
    entry_at_sec: float
    source: str = ""
    provenance: str = ""
    status: TrainStatus = TrainStatus.SCHEDULED
    next_use_index: int = 0
    next_stop_index: int = 0
    finished: bool = False
    admitted: bool = False
    delays: DelayBuckets = field(default_factory=DelayBuckets)

    # Live motion: sample position and speed from the profile in flight.
    profile: Profile | None = None
    profile_t0: float = 0.0
    profile_s0: float = 0.0

    # Time-boxed phases, for remaining-seconds in the DTO.
    dwell_end_t: float = 0.0
    hold_end_t: float = 0.0
    wait_end_t: float | None = None     # None => indefinite (resource withdrawn)

    # Controller-imposed regulation and holds.
    regulated_kmh: float | None = None
    pending_hold_sec: float = 0.0
    pending_event_hold: float = 0.0
    # Absolute controller release time.  Repeated advice extends to the same
    # release instant instead of adding another hold on top of an active one.
    controller_hold_until_sec: float = 0.0
    controller_hold_resource_id: str | None = None
    # Resource lifecycle is explicit so commands can be rejected while a train
    # owns or is queued for infrastructure.  This also makes leaked leases
    # observable in tests and diagnostics.
    queued_resource_id: str | None = None
    current_resource_id: str | None = None
    regulated_until_sec: float | None = None
    regulation_expires_after_resource_id: str | None = None

    # Booked-time accounting.
    actual_dep_sec: float | None = None
    # Service-seconds at the far boundary.  Terminal metrics use this rather
    # than a mid-run lateness snapshot, so a train can never vanish from a
    # fixed benchmark cohort when the rolling live window retires it.
    actual_exit_sec: float | None = None
    departed_platform: bool = False

    def sample(self, now: float) -> tuple[float, float]:
        """(metres along route, speed in m/s) at simulation time `now`."""
        if self.profile is None:
            return self.s, 0.0 if self.status != TrainStatus.RUNNING else self.speed_ms
        covered, speed = self.profile.sample(now - self.profile_t0)
        return self.profile_s0 + covered, speed

    def sample_s(self, now: float) -> float:
        return self.sample(now)[0]

    def speed_kmh_at(self, now: float) -> float:
        return self.sample(now)[1] * 3.6

    def dwell_remaining(self, now: float) -> float:
        return max(0.0, self.dwell_end_t - now) if self.status == TrainStatus.DWELLING else 0.0

    def hold_remaining(self, now: float) -> float:
        return max(0.0, self.hold_end_t - now) if self.status == TrainStatus.HELD else 0.0

    def lateness_sec(self, now: float, epoch_service_sec: float) -> float:
        """Lateness against the booked departure from Vasai Road.

        Once the train has actually left the platform this is fixed at the
        difference that was measured. Before that it is the delay accrued so far,
        plus any overrun already visible against the booked time.
        """
        if self.actual_dep_sec is not None:
            return self.actual_dep_sec - self.booked_dep_sec
        accrued = self.delays.total
        service_now = epoch_service_sec + now
        if service_now > self.booked_dep_sec and not self.finished:
            return max(accrued, service_now - self.booked_dep_sec)
        return accrued


@dataclass
class OccupancyRecord:
    resource_id: str
    train_id: str
    enter: float
    exit: float | None = None


@dataclass
class CausalLink:
    """One edge in the delay-dependency graph - required for explainable delay."""
    cause_type: str
    cause_entity: str
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
    action_id: str | None = None
    plan_id: str | None = None
    effective_resource_id: str | None = None
    reason_conflict_id: str | None = None
    release_at_sec: float | None = None
    expires_after_resource_id: str | None = None
    expires_at_sec: float | None = None
    lifecycle: str = "PROPOSED"
    supersedes_action_id: str | None = None

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
        if self.action_id is not None:
            out["actionId"] = self.action_id
        if self.plan_id is not None:
            out["planId"] = self.plan_id
        if self.effective_resource_id is not None:
            out["effectiveResourceId"] = self.effective_resource_id
        if self.reason_conflict_id is not None:
            out["reasonConflictId"] = self.reason_conflict_id
        if self.release_at_sec is not None:
            out["releaseAtSec"] = round(self.release_at_sec, 3)
        if self.expires_after_resource_id is not None:
            out["expiresAfterResourceId"] = self.expires_after_resource_id
        if self.expires_at_sec is not None:
            out["expiresAtSec"] = round(self.expires_at_sec, 3)
        out["lifecycle"] = self.lifecycle
        if self.supersedes_action_id is not None:
            out["supersedesActionId"] = self.supersedes_action_id
        return out
