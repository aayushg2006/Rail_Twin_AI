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
    """Configurable weights for the optimization objective (Phase 5).

    J = W_delay*total_delay + W_conflict*conflicts + W_headway*headway_violations
      + W_platform*platform_conflicts + W_route*route_changes + W_hold*hold_seconds
    """

    model_config = SettingsConfigDict(env_prefix="RAILTWIN_W_")

    delay: float = 1.0          # per second of network delay
    conflict: float = 6000.0    # per residual CRITICAL conflict (dominates)
    headway: float = 2500.0     # per headway violation
    platform: float = 3000.0    # per platform conflict
    route: float = 400.0        # per route change (infrastructure churn)
    hold: float = 0.5           # per second of imposed hold
    # Recommendation scoring: passenger delay weighted vs freight (matches TS engine).
    passenger_vs_freight: float = 3.0


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RAILTWIN_", env_file=".env", extra="ignore")

    # --- Determinism ---
    seed: int = 42

    # --- Simulation ---
    # Epoch the sim clock starts from (matches the frontend seed in store.tsx).
    epoch_start_ms: int = Field(default_factory=lambda: int(time.time() * 1000))
    clock_mode: str = "LIVE"
    demo_epoch_start_ms: int = 1_786_792_440_000  # 2026-08-15T16:44:00+05:30
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

    # --- ML ---
    artifacts_dir: str = "app/prediction/artifacts"
    ml_confidence_floor: float = 0.35        # below this -> LOW_CONFIDENCE, use deterministic

    weights: ObjectiveWeights = Field(default_factory=ObjectiveWeights)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
