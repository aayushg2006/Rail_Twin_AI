"""Central configuration for the RAIL-TWIN backend.

Everything tunable — simulation seed, objective weights, DB/Redis URLs, feature
flags — lives here so nothing is hard-coded deeper in the stack. Values come from
environment variables (optionally a .env file) via pydantic-settings.
"""
from __future__ import annotations

from functools import lru_cache
import time

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ObjectiveWeights(BaseSettings):
    """Weights for the optimisation objective (see optimize/objective.py).

    J = W_conflict * residual_conflicts + W_passenger * passenger_minutes
      + W_freight * freight_tonne_minutes + W_hold * hold_sec
      + W_route * route_changes - W_throughput * cleared
    """

    model_config = SettingsConfigDict(env_prefix="RAILTWIN_W_")

    # A residual CRITICAL conflict is a hard failure and dominates everything.
    conflict: float = 1.0
    # Per passenger-minute of added delay - the primary term.
    passenger: float = 1.0
    # Per freight tonne-minute. Calibrated so ~110 tonne-minutes of goods delay
    # trades against 1 passenger-minute, which puts a 4,700 t loaded rake at
    # roughly the same worth per minute as a 43-passenger shortfall.
    freight: float = 0.009
    # Small penalties that break ties towards the least intrusive regulation.
    hold: float = 0.02
    route: float = 25.0
    # Reward for clearing a movement inside the horizon.
    throughput: float = 40.0


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RAILTWIN_", env_file=".env", extra="ignore")

    # --- Determinism ---
    seed: int = 42

    # --- Simulation ---
    # Epoch the sim clock starts from (matches the frontend seed in store.tsx).
    epoch_start_ms: int = Field(default_factory=lambda: int(time.time() * 1000))
    # DEMO pins the clock inside the snapshot's active window so the console is
    # populated out of the box. LIVE runs on the real wall clock but remaps the
    # schedule into the window (see SimulationEngine) so it is never empty either.
    clock_mode: str = "DEMO"
    # 2026-08-15T14:10:00+05:30 - the busiest window the timetable covers:
    # ~51 movements in an hour, 16 express, 28 suburban, 7 goods, 19 over the branch.
    demo_epoch_start_ms: int = 1_786_783_200_000
    tick_seconds: float = 0.25               # broadcast cadence (sim advances speed*tick)
    default_horizon_sec: int = 900           # 15 min prediction horizon
    respawn_gap_sec: int = 150
    default_speed: int = 1                   # live mode is wall-clock 1x

    # --- What-if / optimization ---
    whatif_horizon_sec: int = 1200           # 20 min default (allow 10/20/30)
    max_propagation_depth: int = 12

    # --- Persistence (all optional; graceful degradation if unreachable) ---
    database_url: str | None = Field(default=None)
    redis_url: str | None = Field(default=None)

    # --- Server ---
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: str = "*"

    # --- RailRadar live data ---
    # RailRadar publishes running status for PASSENGER services only; goods
    # movements have no public feed and stay synthetic. The free tier allows
    # 1,000 requests a month and returns delay + last reported halt rather than
    # a position stream, so the twin polls a small watchlist and interpolates
    # between observations.
    #   off     no external calls at all (default)
    #   replay  drive from a recorded feed - the demo never touches the network
    #   live    call the API, subject to the monthly budget below
    railradar_mode: str = "off"
    railradar_api_key: str | None = Field(default=None)
    railradar_base_url: str = "https://railradar.in/api"
    railradar_station: str = "BSR"
    # Hard cap. Once spent, the client refuses to call until the month rolls.
    railradar_monthly_budget: int = 1000
    railradar_poll_seconds: int = 1200      # 20 min - ~72 calls/day at 1 train
    railradar_watchlist_size: int = 6       # trains polled each cycle
    railradar_cache_ttl_seconds: int = 900
    railradar_timeout_seconds: float = 8.0
    railradar_replay_file: str = "data/railradar-replay.json"
    # Persist every observation for later retraining on real lateness.
    railradar_collect: bool = True

    # --- ML ---
    artifacts_dir: str = "app/prediction/artifacts"
    ml_confidence_floor: float = 0.35        # below this -> LOW_CONFIDENCE, use deterministic

    weights: ObjectiveWeights = Field(default_factory=ObjectiveWeights)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
