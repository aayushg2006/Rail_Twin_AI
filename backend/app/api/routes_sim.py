"""REST endpoints: scenarios, live state/metrics, network export, audit log."""
from __future__ import annotations

from fastapi import APIRouter, Request

from ..domain import dto
from ..network.scenarios import scenarios
from ..network.topology import (junctions, platforms, resources, routes,
                                signals, station_ticks, tracks)

router = APIRouter(prefix="/api")


@router.get("/health")
async def health(request: Request) -> dict:
    orch = request.app.state.orchestrator
    return {"status": "ok", "scenario": orch.engine.scenario_id,
            "simTimeSec": round(orch.engine.now, 1), "playing": orch.playing,
            "clients": len(orch._clients)}


@router.get("/scenarios")
async def list_scenarios() -> list[dict]:
    return [{"id": s.id, "label": s.label, "description": s.description} for s in scenarios]


@router.get("/state")
async def get_state(request: Request) -> dict:
    orch = request.app.state.orchestrator
    return orch._build_bundle()


@router.get("/metrics")
async def get_metrics(request: Request) -> dict:
    orch = request.app.state.orchestrator
    return dto.kpis_dict(orch._cached_kpis)


@router.get("/audit")
async def get_audit(request: Request) -> list[dict]:
    audit = getattr(request.app.state, "audit", None)
    return audit.all() if audit else []


@router.get("/network")
async def get_network() -> dict:
    """Full topology export (map-unit coordinates) for optional client use."""
    def pts(path):
        return [p.as_dict() for p in path]
    return {
        "tracks": [{"id": t.id, "name": t.name, "kind": t.kind, "direction": t.direction,
                    "through": t.through, "path": pts(t.path)} for t in tracks],
        "platforms": [{"id": p.id, "label": p.label, "x": p.x, "y": p.y, "w": p.w,
                       "h": p.h, "serves": p.serves, "usage": p.usage, "side": p.side}
                      for p in platforms],
        "junctions": [{"id": j.id, "label": j.label, "at": j.at.as_dict()} for j in junctions],
        "resources": [{"id": r.id, "label": r.label, "kind": r.kind, "at": r.at.as_dict(),
                       "radius": r.radius, "headwaySec": r.headway_sec, "capacity": r.capacity}
                      for r in resources],
        "signals": [{"id": s.id, "at": s.at.as_dict(), "facing": s.facing, "aspect": s.aspect}
                    for s in signals],
        "stationTicks": [{"name": s.name, "at": s.at.as_dict(), "km": s.km,
                          "corridor": s.corridor} for s in station_ticks],
        "routes": [{"id": r.id, "label": r.label, "tracks": r.tracks,
                    "corridorFrom": r.corridor_from, "corridorTo": r.corridor_to,
                    "path": pts(r.path),
                    "stops": [{"s": st.s, "platformId": st.platform_id,
                               "dwellSec": st.dwell_sec} for st in r.stops]}
                   for r in routes],
    }
