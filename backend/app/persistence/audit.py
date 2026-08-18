"""Audit store for controller decisions (human-in-the-loop, Phase 5).

Always keeps an in-memory append-only log so the console works with no database.
If DATABASE_URL is set and asyncpg is importable & reachable, decisions are also
mirrored to Postgres. Never raises into the request path.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any


class AuditStore:
    def __init__(self, database_url: str | None):
        self.database_url = database_url
        self.records: list[dict] = []
        self._pool: Any = None

    async def connect(self) -> None:
        if not self.database_url:
            return
        try:
            import asyncpg  # noqa
            self._pool = await asyncpg.create_pool(self.database_url, min_size=1, max_size=4)
            async with self._pool.acquire() as con:
                await con.execute(
                    """CREATE TABLE IF NOT EXISTS audit_log (
                        recommendation_id TEXT PRIMARY KEY,
                        simulation_id TEXT, generated_at DOUBLE PRECISION,
                        conflict_id TEXT, recommended_action JSONB, alternatives JSONB,
                        safety_result JSONB, controller_action TEXT,
                        modified_parameters JSONB, final_action JSONB, resulting_metrics JSONB
                    )""")
        except Exception:
            self._pool = None  # graceful: stay in-memory

    def record_sync(self, decision: dict) -> None:
        rec = {
            "recommendation_id": decision.get("conflictId", "") + "-" + uuid.uuid4().hex[:8],
            "simulation_id": decision.get("simulationId", "live"),
            "generated_at": decision.get("simTimeSec", time.time()),
            "conflict_id": decision.get("conflictId"),
            "recommended_action": decision.get("action"),
            "alternatives": decision.get("alternatives", []),
            "safety_result": decision.get("safety"),
            "controller_action": decision.get("outcome"),
            "modified_parameters": decision.get("override"),
            "final_action": decision.get("action"),
            "resulting_metrics": decision.get("kpis"),
            "note": decision.get("note", ""),
        }
        self.records.append(rec)
        if self._pool is not None:
            with __import__("contextlib").suppress(RuntimeError):
                asyncio.get_running_loop().create_task(self._write(rec))

    async def _write(self, rec: dict) -> None:
        import json
        try:
            async with self._pool.acquire() as con:
                await con.execute(
                    """INSERT INTO audit_log (recommendation_id, simulation_id, generated_at,
                        conflict_id, recommended_action, alternatives, safety_result,
                        controller_action, modified_parameters, final_action, resulting_metrics)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
                       ON CONFLICT (recommendation_id) DO NOTHING""",
                    rec["recommendation_id"], rec["simulation_id"], rec["generated_at"],
                    rec["conflict_id"], json.dumps(rec["recommended_action"]),
                    json.dumps(rec["alternatives"]), json.dumps(rec["safety_result"]),
                    rec["controller_action"], json.dumps(rec["modified_parameters"]),
                    json.dumps(rec["final_action"]), json.dumps(rec["resulting_metrics"]))
        except Exception:
            pass

    def all(self) -> list[dict]:
        return list(reversed(self.records))

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
