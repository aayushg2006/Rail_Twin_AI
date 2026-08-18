"""RAIL-TWIN FastAPI application.

Boots one live SimulationOrchestrator and exposes it over a WebSocket (live
snapshots + controller commands) and a small REST surface. Postgres/Redis are
optional — the app runs fully in-memory when they are absent (graceful).
"""
from __future__ import annotations

import contextlib

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .api import routes_sim, ws
from .orchestrator.orchestrator import SimulationOrchestrator


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    orch = SimulationOrchestrator("BASE")
    app.state.orchestrator = orch
    # Optional persistence — attach if reachable, otherwise degrade gracefully.
    with contextlib.suppress(Exception):
        from .persistence.audit import AuditStore
        app.state.audit = AuditStore(settings.database_url)
        await app.state.audit.connect()
        orch.decision_hook = app.state.audit.record_sync
    # Optimisation (CP-SAT) provider.
    with contextlib.suppress(Exception):
        from .optimize.provider import attach_optimizer
        attach_optimizer(orch)
    # ML (XGBoost) provider — only attaches when trained artifacts are present.
    with contextlib.suppress(Exception):
        from .prediction.provider import attach_ml
        from .prediction.service import get_service
        if get_service().ready:
            attach_ml(orch)
    await orch.start()
    try:
        yield
    finally:
        await orch.stop()
        with contextlib.suppress(Exception):
            await app.state.audit.close()


app = FastAPI(title="RAIL-TWIN", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ws.router)
app.include_router(routes_sim.router)


@app.get("/")
async def root() -> dict:
    return {"name": "RAIL-TWIN", "version": "0.1.0",
            "ws": "/ws", "health": "/api/health"}
