"""Scenario presets - deterministic disruption TRIGGERS, never outcomes.

A scenario only sets initial conditions: extra entry lateness on a service, a
resource withdrawn from use, a speed restriction, or degraded headway. Every
delay, conflict and knock-on effect that follows is computed by the simulation.
Nothing here says what the result should be.

Services are chosen by SELECTOR, not by train number. Naming numbers made the
presets silently do nothing whenever the clock sat in a window those particular
trains did not run in; a selector resolves against whatever is actually on the
ground, so every preset bites at any hour.
"""
from __future__ import annotations

from dataclasses import dataclass, field

SCENARIO_IDS = [
    "BASE", "PLATFORM_BLOCKED", "BRANCH_BLOCKED",
    "SIGNAL_DEGRADED", "FREIGHT_LATE", "PEAK_SURGE",
]


@dataclass(frozen=True)
class ScenarioDefinition:
    id: str
    label: str
    description: str
    mechanism: str


scenarios: list[ScenarioDefinition] = [
    ScenarioDefinition(
        "BASE", "Normal working",
        "The booked timetable running as published, with observed entry lateness.",
        "No disruption applied."),
    ScenarioDefinition(
        "PLATFORM_BLOCKED", "Platform out of use",
        "The busiest platform face is withdrawn from service, so the movements "
        "booked to it must be re-platformed.",
        "The platform road working the most trains in the current window is blocked."),
    ScenarioDefinition(
        "BRANCH_BLOCKED", "Diva branch obstructed",
        "The first block section on the Diva-bound branch road is unavailable.",
        "Block BLK-D-BRD-1 withdrawn from service."),
    ScenarioDefinition(
        "SIGNAL_DEGRADED", "Signalling under caution",
        "Signalling is degraded across the junction and every headway doubles.",
        "Headway multiplier 2.0 on all resources."),
    ScenarioDefinition(
        "FREIGHT_LATE", "Goods running late",
        "The next two goods paths over the branch reach the section badly down "
        "and at reduced speed.",
        "Entry lateness +18 min and a 30 km/h restriction on the next 2 branch freights."),
    ScenarioDefinition(
        "PEAK_SURGE", "Suburban bunching",
        "Several suburban services reach the section late together, closing up "
        "the fast road.",
        "Entry lateness of 4-9 min on the next 5 suburban services."),
]

scenario_by_id: dict[str, ScenarioDefinition] = {s.id: s for s in scenarios}


@dataclass
class TrainOverride:
    """Initial conditions for one service. `entry_delay_sec` is how late it
    reaches the modelled area; everything downstream is simulated."""
    entry_delay_sec: float = 0.0
    speed_kmh: float | None = None
    platform_id: str | None = None


@dataclass
class DynamicOverride:
    """Apply an override to the next `count` services matching `match`."""
    match: str
    count: int
    override: TrainOverride
    stagger_sec: float = 0.0    # added cumulatively, so the group is not identical


@dataclass
class ScenarioSetup:
    overrides: dict[str, TrainOverride] = field(default_factory=dict)
    dynamic: list[DynamicOverride] = field(default_factory=list)
    blocked_resources: list[str] = field(default_factory=list)
    unavailable_routes: list[str] = field(default_factory=list)
    headway_multiplier: float = 1.0
    # Resolved by the engine against the current window. "BUSIEST_PLATFORM"
    # withdraws whichever face is actually working the most trains right now,
    # so the preset bites at any hour instead of naming a face that may be idle.
    block_selector: str | None = None


def matches(entry, kind: str) -> bool:
    """Does this fleet entry satisfy a selector?"""
    touches_branch = "DIVA" in (entry.arrival_corridor, entry.departure_corridor)
    if kind == "FREIGHT_BRANCH":
        return entry.category == "FREIGHT" and touches_branch
    if kind == "FREIGHT_ANY":
        return entry.category == "FREIGHT"
    if kind == "SUBURBAN":
        return entry.category in ("LOCAL", "MEMU")
    if kind == "SUBURBAN_FAST":
        return entry.service_class in ("LOCAL_FAST", "LOCAL_AC", "SUBURBAN")
    if kind == "EXPRESS_BRANCH":
        return entry.category == "EXPRESS" and touches_branch
    if kind == "BRANCH_ANY":
        return touches_branch
    return False


def scenario_setup(scenario_id: str) -> ScenarioSetup:
    if scenario_id == "PLATFORM_BLOCKED":
        return ScenarioSetup(block_selector="BUSIEST_PLATFORM")
    if scenario_id == "BRANCH_BLOCKED":
        return ScenarioSetup(blocked_resources=["BLK-D-BRD-1"])
    if scenario_id == "SIGNAL_DEGRADED":
        return ScenarioSetup(headway_multiplier=2.0)
    if scenario_id == "FREIGHT_LATE":
        return ScenarioSetup(dynamic=[
            DynamicOverride("FREIGHT_BRANCH", 2,
                            TrainOverride(entry_delay_sec=1080, speed_kmh=30),
                            stagger_sec=240),
        ])
    if scenario_id == "PEAK_SURGE":
        return ScenarioSetup(dynamic=[
            DynamicOverride("SUBURBAN_FAST", 5,
                            TrainOverride(entry_delay_sec=240), stagger_sec=90),
        ])
    return ScenarioSetup()
