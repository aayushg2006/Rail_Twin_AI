"""REST endpoints: scenarios, live state/metrics, network export, audit log."""
from __future__ import annotations

from fastapi import APIRouter, Request

from ..domain import dto
from ..network.scenarios import scenarios
from ..network.net import (corridor_lines, corridors, lines, network_pack,
                           platforms, resources, timetable_pack, freight_pack)

router = APIRouter(prefix="/api")


def _physical_topology() -> tuple[dict, dict[str, list[int]]]:
    """Connect the operational line IDs to the supplied OSM track graph.

    The same line identifiers drive routing, the schematic and geographic
    snapping.  Keeping this mapping in the API prevents either renderer from
    inventing a second Vasai layout.
    """
    geo = network_pack.get("geo") or {}
    way_ids: dict[str, list[int]] = {}
    by_kind: dict[str, list[int]] = {}
    for way in geo.get("ways", []):
        by_kind.setdefault(str(way.get("kind", "OTHER")), []).append(int(way["id"]))
    main = by_kind.get("WESTERN_MAIN", [])
    branch = by_kind.get("BRANCH_OR_LINK", [])
    freight = by_kind.get("FREIGHT_SIDING", [])
    yard = by_kind.get("YARD", [])
    for line_id in ("SLD", "SLU", "FSD", "FSU", "THD", "THU"):
        way_ids[line_id] = main
    way_ids["BRD"] = branch
    way_ids["BRU"] = branch
    way_ids["GDC"] = sorted(set(branch + freight))
    way_ids["GYL"] = sorted(set(yard + freight))

    nodes = [
        {"id": "NORTH", "kind": "CORRIDOR_END", "corridor": "NORTH"},
        {"id": "SOUTH", "kind": "CORRIDOR_END", "corridor": "SOUTH"},
        {"id": "DIVA", "kind": "CORRIDOR_END", "corridor": "DIVA"},
        {"id": "YARD", "kind": "CORRIDOR_END", "corridor": "YARD"},
        *({"id": item.id, "kind": "PLATFORM_FACE", "corridor": "STATION"}
          for item in platforms.values()),
        *({"id": item.id, "kind": "JUNCTION", "corridor": item.corridor}
          for item in resources.values() if item.kind == "JUNCTION"),
    ]
    edge_specs = {
        "SLD": ("NORTH", "SOUTH", ["J-N", "PF1", "J-S"]),
        "SLU": ("SOUTH", "NORTH", ["J-S", "PF2", "J-N"]),
        "FSD": ("NORTH", "SOUTH", ["J-N", "PF3", "J-S"]),
        "FSU": ("SOUTH", "NORTH", ["J-S", "PF4", "J-N"]),
        "THD": ("NORTH", "SOUTH", ["J-N", "PF5", "J-S"]),
        "THU": ("SOUTH", "NORTH", ["J-S", "PF6", "J-N"]),
        "BRD": ("NORTH", "DIVA", ["J-N", "PF6", "J-B"]),
        "BRU": ("DIVA", "NORTH", ["J-B", "PF7", "J-N"]),
        "GDC": ("NORTH", "DIVA", ["J-G", "J-B"]),
        "GYL": ("YARD", "SOUTH", ["J-G", "J-S"]),
    }
    edges = [{"id": f"TRACK-{line_id}", "lineId": line_id,
              "from": start, "to": end, "via": via,
              "osmWayIds": way_ids[line_id]}
             for line_id, (start, end, via) in edge_specs.items()]
    return {"nodes": nodes, "edges": edges}, way_ids


@router.get("/health")
async def health(request: Request) -> dict:
    orch = request.app.state.orchestrator
    return {
        "status": "degraded" if getattr(orch, "_tick_error", "") else "ok",
        "scenario": orch.engine.scenario_id,
        "simTimeSec": round(orch.engine.now, 1),
        "playing": orch.playing,
        "clients": len(orch._clients),
        "tickFailures": getattr(orch, "_tick_failures", 0),
        "tickError": getattr(orch, "_tick_error", ""),
    }


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


@router.get("/benchmark/latest")
async def latest_benchmark() -> dict:
    """Latest signed 100-run release verdict; absent/failed is never validated."""
    from ..benchmark.runner import load_latest_report
    return load_latest_report()


@router.get("/audit")
async def get_audit(request: Request) -> list[dict]:
    audit = getattr(request.app.state, "audit", None)
    return audit.all() if audit else []


@router.get("/ingest")
async def get_ingest(request: Request) -> dict:
    """Live-data status: mode, budget, watchlist and the latest observations."""
    ingest = getattr(request.app.state, "ingest", None)
    if ingest is None:
        return {"enabled": False, "mode": "off",
                "reason": "ingestion service is not attached"}
    return await ingest.status()


@router.post("/ingest/poll")
async def poll_ingest(request: Request) -> dict:
    """Force one polling cycle (costs budget in live mode)."""
    ingest = getattr(request.app.state, "ingest", None)
    if ingest is None or not ingest.enabled:
        return {"polled": 0, "reason": "ingestion is disabled"}
    fresh = await ingest.poll_board()
    detail = await ingest.poll_detail()
    combined = {observation.number: observation for observation in [*fresh, *detail]}
    return {"polled": len(combined),
            "observations": [observation.as_dict() for observation in combined.values()]}


@router.get("/observations")
async def get_observations(request: Request, limit: int = 200) -> list[dict]:
    """Collected lateness readings, the training set for the real-data model."""
    ingest = getattr(request.app.state, "ingest", None)
    if ingest is None:
        return []
    return await ingest.collector.recent(limit)


@router.get("/network")
async def get_network() -> dict:
    """The physical network in metres. The console maps chainage to pixels."""
    topology, line_to_osm = _physical_topology()
    return {
        "units": "metres",
        "datum": network_pack["datum"],
        # Real OpenStreetMap track geometry for the geographic map view. The
        # physics stays metric chainage; this is only what the console draws.
        "geo": network_pack.get("geo", {"available": False}),
        "topology": topology,
        "lineToOsmWayIds": line_to_osm,
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
