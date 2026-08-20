"""Training-data generation from the digital twin.

The previous version labelled every row with the output of the deterministic
`predict()` function and then trained a model to reproduce it. That is circular:
the model was learning a closed-form formula the process already had, which is
why it scored an ETA MAE of 1.95 seconds and a conflict AUC of 0.997 while
adding no information whatsoever.

Two things fix it.

1. The twin is now STOCHASTIC - dwell overruns, entry lateness drawn from the
   observed distribution, and genuine queueing at contended resources - so the
   deterministic projection CANNOT be exact and there is real residual variance
   to learn.
2. Targets are what ACTUALLY HAPPENED in the episode, read out of the DES after
   the fact:
     target_eta        real remaining time to clear the section
     target_lateness   real lateness H seconds later
     target_conflict   whether the train really did lose time waiting for a
                       resource within H - a materialised conflict, not a
                       prediction copied from predict()

The deterministic projection becomes the BASELINE each model is scored against,
which is the only way "why AI?" has an answer.
"""
from __future__ import annotations

import random
from collections import defaultdict

import pandas as pd

from ..network.scenarios import SCENARIO_IDS
from ..twin.engine import SimulationEngine
from ..twin.predict import predict, project_finish
from .features import FEATURE_NAMES, extract

FUTURE_HORIZON = 300.0     # seconds ahead for the lateness / conflict targets
SAMPLE_EVERY = 20.0        # seconds of simulation between samples

# Start hours spread across the working day so the model sees the night freight
# peak, the morning suburban peak and the middle-of-day long-distance mix.
START_HOURS = (1, 6, 8, 10, 12, 14, 16, 18, 21, 23)


def _wait_delay(rt) -> float:
    d = rt.delays
    return d.block_wait + d.junction_wait + d.platform_wait + d.headway_wait


def _epoch_for(hour: int) -> int:
    """Milliseconds for 2026-08-15 at `hour` IST - the twin's reference day."""
    base = 1_786_783_200_000            # 14:10 IST
    return int(base + (hour - 14) * 3600_000 - 600_000)


def generate_dataset(n_episodes: int = 40, t_max: float = 1800.0,
                     dt: float = SAMPLE_EVERY, seed0: int = 1000) -> pd.DataFrame:
    rows: list[dict] = []
    for ep in range(n_episodes):
        seed = seed0 + ep
        scenario = SCENARIO_IDS[ep % len(SCENARIO_IDS)]
        hour = START_HOURS[ep % len(START_HOURS)]
        eng = SimulationEngine(scenario, seed=seed, epoch_start_ms=_epoch_for(hour),
                               stochastic=True)

        series: dict[str, list[dict]] = defaultdict(list)
        cleared_at: dict[str, float] = {}

        while eng.now < t_max:
            eng.advance(dt)
            astate = eng.analytic_state()
            pred = predict(astate)
            # analytic_state() drops finished trains, so watch the runtimes
            # directly or the moment a train clears is never observed.
            for tid, rt in eng.trains.items():
                if rt.finished and rt.admitted:
                    cleared_at.setdefault(tid, eng.now)
            for tid, st in astate.trains.items():
                rt = eng.trains.get(tid)
                if rt is None or not rt.admitted or rt.finished:
                    continue
                series[tid].append({
                    "t": eng.now,
                    "feats": extract(astate, tid, pred),
                    "lateness": rt.lateness_sec(eng.now, eng.service_epoch_sec),
                    "wait": _wait_delay(rt),
                    # The deterministic answer, kept as the baseline to beat.
                    "projected_remaining": project_finish(astate, tid) or 0.0,
                })

        for tid, seq in series.items():
            finish = cleared_at.get(tid)
            for i, rec in enumerate(seq):
                t0 = rec["t"]
                window = [r for r in seq if t0 < r["t"] <= t0 + FUTURE_HORIZON]
                later = [r for r in seq if r["t"] >= t0 + FUTURE_HORIZON]
                reference = later[0] if later else (seq[-1] if seq else rec)

                # A conflict MATERIALISED if the train actually accrued fresh
                # resource-wait time inside the horizon.
                fresh_wait = max((r["wait"] for r in window), default=rec["wait"]) - rec["wait"]

                row = dict(rec["feats"])
                row["episode"] = f"ep{ep}"
                row["scenario"] = scenario
                row["train"] = tid
                row["baseline_remaining"] = rec["projected_remaining"]
                row["target_eta"] = (finish - t0) if (finish is not None and finish > t0) else None
                row["target_lateness"] = reference["lateness"]
                row["target_conflict"] = 1 if fresh_wait > 5.0 else 0
                rows.append(row)

    return pd.DataFrame(rows)


def split_by_episode(df: pd.DataFrame, seed: int = 42
                     ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Split by episode so no episode leaks across train/val/test."""
    episodes = sorted(df["episode"].unique())
    rng = random.Random(seed)
    rng.shuffle(episodes)
    n = len(episodes)
    n_train = int(n * 0.70)
    n_val = int(n * 0.15)
    train_eps = set(episodes[:n_train])
    val_eps = set(episodes[n_train:n_train + n_val])
    test_eps = set(episodes[n_train + n_val:])
    return (df[df["episode"].isin(train_eps)].copy(),
            df[df["episode"].isin(val_eps)].copy(),
            df[df["episode"].isin(test_eps)].copy())
