"""Persist every live observation, so the delay model can eventually be trained
on real Indian Railways lateness rather than on simulated variance.

The models currently learn from the stochastic twin. Each reading stored here is
one row of ground truth; once enough have accumulated, `python -m
app.prediction.train --source=observed` can retrain against them and the model
registry promotes the new version. Until then this is a cheap, append-only log
that degrades to memory when Postgres is absent.
"""
from __future__ import annotations

import logging
from typing import Any

from .railradar import Observation

logger = logging.getLogger("railtwin.collector")

DDL = """
CREATE TABLE IF NOT EXISTS railradar_observation (
    id           BIGSERIAL PRIMARY KEY,
    train_number TEXT NOT NULL,
    lateness_sec DOUBLE PRECISION NOT NULL,
    last_station TEXT,
    observed_at  TIMESTAMPTZ NOT NULL,
    source       TEXT NOT NULL
)
"""
INDEX = ("CREATE INDEX IF NOT EXISTS railradar_observation_number_idx "
         "ON railradar_observation (train_number, observed_at DESC)")


class ObservationCollector:
    def __init__(self, database_url: str | None):
        self.database_url = database_url
        self._pool: Any = None
        self._ready = False
        self.memory: list[dict] = []

    @property
    def count(self) -> int:
        return len(self.memory)

    async def _connect(self) -> None:
        if self._ready or not self.database_url:
            self._ready = True
            return
        try:
            import asyncpg
            self._pool = await asyncpg.create_pool(self.database_url,
                                                   min_size=1, max_size=2)
            async with self._pool.acquire() as con:
                await con.execute(DDL)
                await con.execute(INDEX)
        except Exception as exc:
            logger.info("observation collector staying in memory: %s", exc)
            self._pool = None
        self._ready = True

    async def record(self, obs: Observation) -> None:
        self.memory.append(obs.as_dict())
        if len(self.memory) > 5000:
            del self.memory[:-5000]
        await self._connect()
        if self._pool is None:
            return
        try:
            async with self._pool.acquire() as con:
                await con.execute(
                    "INSERT INTO railradar_observation "
                    "(train_number, lateness_sec, last_station, observed_at, source) "
                    "VALUES ($1, $2, $3, $4::timestamptz, $5)",
                    obs.number, obs.lateness_sec, obs.last_station,
                    obs.observed_at, obs.source)
        except Exception as exc:
            logger.debug("observation not persisted: %s", exc)

    async def recent(self, limit: int = 200) -> list[dict]:
        await self._connect()
        if self._pool is None:
            return self.memory[-limit:]
        try:
            async with self._pool.acquire() as con:
                rows = await con.fetch(
                    "SELECT train_number, lateness_sec, last_station, observed_at, "
                    "source FROM railradar_observation "
                    "ORDER BY observed_at DESC LIMIT $1", limit)
            return [dict(r) for r in rows]
        except Exception:
            return self.memory[-limit:]

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
