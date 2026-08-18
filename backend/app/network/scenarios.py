"""Scenario definitions + per-scenario disruption overrides.

Port of the scenario layer in src/twin/scenario.ts. A scenario is a deterministic
*trigger* (start shifts, speed restrictions, blocked resources, headway multiplier);
downstream delays and conflicts are computed by the simulation, never set here.
"""
from __future__ import annotations

from dataclasses import dataclass, field

SCENARIO_IDS = [
    "BASE", "FREIGHT_DELAY", "EXPRESS_DELAY", "PLATFORM_UNAVAILABLE", "TRACK_BLOCKAGE",
    "SIGNAL_FAILURE", "TRAIN_BREAKDOWN", "YARD_CONGESTION", "PEAK_TRAFFIC", "MULTIPLE_DELAYS",
]


@dataclass(frozen=True)
class ScenarioDefinition:
    id: str
    label: str
    description: str


scenarios: list[ScenarioDefinition] = [
    ScenarioDefinition("BASE", "Base demo scenario",
                       "Express 12928 works PF 6 then the Diva branch while goods 4271 runs the north chord to the same turnout."),
    ScenarioDefinition("FREIGHT_DELAY", "Freight running late",
                       "Goods 4271 loses time on the chord and arrives into the turnout window later."),
    ScenarioDefinition("EXPRESS_DELAY", "Express running late",
                       "Express 12928 enters 6 minutes down and holds PF 6 longer."),
    ScenarioDefinition("PLATFORM_UNAVAILABLE", "PF 6 unavailable",
                       "PF 6 withdrawn from service; Diva-bound expresses must be re-planned."),
    ScenarioDefinition("TRACK_BLOCKAGE", "Branch block BR-1 blocked",
                       "Diva-bound branch block taken out of service by a permanent-way team."),
    ScenarioDefinition("SIGNAL_FAILURE", "Signal failure at JB",
                       "Turnout worked under caution; separation over JB doubles."),
    ScenarioDefinition("TRAIN_BREAKDOWN", "Goods brake defect",
                       "Goods 4271 restricted to 20 km/h on the chord."),
    ScenarioDefinition("YARD_CONGESTION", "Goods yard congestion",
                       "Goods loop occupied; the loop alternative is not available."),
    ScenarioDefinition("PEAK_TRAFFIC", "Peak suburban traffic",
                       "Suburban services closed up on the slow and fast pairs."),
    ScenarioDefinition("MULTIPLE_DELAYS", "Multiple simultaneous delays",
                       "Express late, goods restricted and PF 6 under pressure at once."),
]

scenario_by_id: dict[str, ScenarioDefinition] = {s.id: s for s in scenarios}


@dataclass
class TrainOverride:
    start_shift: float = 0.0        # seconds of shift applied to start position
    speed_kmh: float | None = None
    nominal_speed_kmh: float | None = None


@dataclass
class ScenarioSetup:
    overrides: dict[str, TrainOverride] = field(default_factory=dict)
    blocked_resources: list[str] = field(default_factory=list)
    unavailable_routes: list[str] = field(default_factory=list)
    headway_multiplier: float = 1.0


def scenario_setup(scenario_id: str) -> ScenarioSetup:
    if scenario_id == "FREIGHT_DELAY":
        return ScenarioSetup(overrides={"F-4271": TrainOverride(start_shift=150, speed_kmh=32)})
    if scenario_id == "EXPRESS_DELAY":
        return ScenarioSetup(overrides={"E-12928": TrainOverride(start_shift=-120, speed_kmh=62)})
    if scenario_id == "PLATFORM_UNAVAILABLE":
        return ScenarioSetup(blocked_resources=["PF6"])
    if scenario_id == "TRACK_BLOCKAGE":
        return ScenarioSetup(blocked_resources=["BLK-DV1"])
    if scenario_id == "SIGNAL_FAILURE":
        return ScenarioSetup(headway_multiplier=2.0)
    if scenario_id == "TRAIN_BREAKDOWN":
        return ScenarioSetup(overrides={"F-4271": TrainOverride(speed_kmh=20, nominal_speed_kmh=40)})
    if scenario_id == "YARD_CONGESTION":
        return ScenarioSetup(unavailable_routes=["RT-FRT-LOOP"])
    if scenario_id == "PEAK_TRAFFIC":
        return ScenarioSetup(overrides={
            "L-90312": TrainOverride(start_shift=60),
            "L-90455": TrainOverride(start_shift=90),
            "K-91007": TrainOverride(start_shift=-40),
        })
    if scenario_id == "MULTIPLE_DELAYS":
        return ScenarioSetup(
            overrides={
                "E-12928": TrainOverride(start_shift=-100, speed_kmh=60),
                "F-4271": TrainOverride(speed_kmh=26, nominal_speed_kmh=40),
            },
            headway_multiplier=1.5,
        )
    return ScenarioSetup()
