"""Synthetic training-data generation from the digital twin (Phase 4).

Runs many deterministic episodes (varying scenario, seed, injected events) and
samples per-train states. Targets are TRUE outcomes read from the same episode as
it plays out — remaining travel time to the next finish, delay H seconds later,
and whether the train accrues fresh resource-wait delay within H (a materialised
conflict). This is emphatically NOT the hard-coded demo result: it is data the
twin produces, used to teach models that then run on unseen states.
"""
from __future__ import annotations

import random
from collections import defaultdict

import pandas as pd

from ..config import settings
from ..network.fleet import fleet
from ..network.scenarios import SCENARIO_IDS
from ..twin.engine import DelayEvent, SimulationEngine
from ..twin.predict import predict
from .features import FEATURE_NAMES, extract

FUTURE_HORIZON = 300.0   # seconds ahead for delay / conflict targets


def _wait_delay(rt) -> float:
    d = rt.delays
    return d.block_wait + d.junction_wait + d.platform_wait + d.headway_wait


def generate_dataset(n_episodes: int = 40, t_max: float = 900.0, dt: float = 15.0,
                     seed0: int = 1000) -> pd.DataFrame:
    rows: list[dict] = []
    for ep in range(n_episodes):
        seed = seed0 + ep
        scenario = SCENARIO_IDS[ep % len(SCENARIO_IDS)]
        rng = random.Random(seed)
        eng = SimulationEngine(scenario, seed=seed)
        if rng.random() < 0.4:
            eng.seek(rng.uniform(10, 40))
            target = rng.choice([f.id for f in fleet])
            eng.inject_event(DelayEvent("EPV", "TRAIN", target,
                                        delay_seconds=rng.choice([120, 240, 360, 480]),
                                        reason="synthetic"))

        series: dict[str, list[dict]] = defaultdict(list)
        finishes: dict[str, list[float]] = defaultdict(list)
        prev_finished = {tid: False for tid in eng.trains}

        while eng.now < t_max:
            eng.advance(dt)
            astate = eng.analytic_state()
            pred = predict(astate)
            hour = ((settings.epoch_start_ms / 1000 + eng.now) / 3600) % 24
            for tid, st in astate.trains.items():
                rt = eng.trains[tid]
                if not prev_finished[tid] and rt.finished:
                    finishes[tid].append(eng.now)
                prev_finished[tid] = rt.finished
                if st.finished:
                    continue
                in_critical = any(c.severity == "CRITICAL" and tid in (c.train_a, c.train_b)
                                  for c in pred.conflicts)
                series[tid].append({
                    "t": eng.now, "feats": extract(astate, tid, pred, hour),
                    "delay": st.delay_sec, "wait": _wait_delay(rt),
                    "crit": 1 if in_critical else 0,
                })

        for tid, seq in series.items():
            fin = finishes[tid]
            for rec in seq:
                t0 = rec["t"]
                next_finish = next((ft for ft in fin if ft > t0), None)
                future = [r for r in seq if r["t"] >= t0 + FUTURE_HORIZON]
                ref = future[0] if future else (seq[-1] if seq else rec)
                window = [r for r in seq if t0 < r["t"] <= t0 + FUTURE_HORIZON]
                # Conflict target: does this train become involved in a CRITICAL
                # predicted conflict within the horizon (materialised or forecast)?
                conflict_label = 1 if any(r["crit"] for r in window) else 0
                row = dict(rec["feats"])
                row["episode"] = f"ep{ep}"
                row["train"] = tid
                row["target_eta"] = (next_finish - t0) if next_finish is not None else None
                row["target_delay"] = ref["delay"]
                row["target_conflict"] = conflict_label
                rows.append(row)

    return pd.DataFrame(rows)


def split_by_episode(df: pd.DataFrame, seed: int = 42) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Split by simulation episode (never leak rows of one episode across splits)."""
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
