"""REST endpoints: scenarios, live state/metrics, network export, audit log."""
from __future__ import annotations

from fastapi import APIRouter, Request

from ..domain import dto
from ..network.scenarios import scenarios
from ..network.net import (corridor_lines, corridors, lines, network_pack,
                           platforms, resources, timetable_pack, freight_pack)

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


@router.get("/data-pack")
async def get_data_pack() -> dict:
    """Provenance for everything the console displays."""
    return {
        "network": {k: v for k, v in network_pack.items() if k != "resources"},
        "timetable": {k: v for k, v in timetable_pack.items() if k != "services"},
        "freight": {k: v for k, v in freight_pack.items() if k != "paths"},
        "serviceCount": len(timetable_pack["services"]),
        "freightPathCount": len(freight_pack["paths"]),
    }


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
    """The physical network in metres. The console maps chainage to pixels."""
    return {
        "units": "metres",
        "datum": network_pack["datum"],
        "stationLimitM": network_pack["stationLimitM"],
        "modelledReachM": network_pack["modelledReachM"],
        "corridors": {cid: {
            "id": c.id, "label": c.label, "shortLabel": c.short_label,
            "screenDir": c.screen_dir, "reachM": c.reach_m,
            "lineSpeedKmh": c.line_speed_kmh,
            "stations": [{"code": code, "name": name, "chainageM": m}
                         for code, name, m in c.stations],
        } for cid, c in corridors.items()},
        "corridorLines": corridor_lines,
        "lines": [{"id": l.id, "label": l.label, "kind": l.kind,
                   "direction": l.direction, "platformId": l.platform_id,
                   "speedLimitKmh": l.speed_limit_kmh} for l in lines.values()],
        "platforms": [{"id": p.id, "label": p.label, "side": p.side,
                       "usage": p.usage, "serves": list(p.serves),
                       "lengthM": p.length_m} for p in platforms.values()],
        "resources": [{"id": r.id, "label": r.label, "kind": r.kind,
                       "corridor": r.corridor, "lines": sorted(r.lines),
                       "fromM": r.from_m, "toM": r.to_m, "centreM": r.centre_m,
                       "lengthM": r.length_m, "headwaySec": r.headway_sec,
                       "capacity": r.capacity} for r in resources.values()],
    }
