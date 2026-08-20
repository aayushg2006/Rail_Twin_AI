"""Generate data/network-bsr.json - the PHYSICAL network at Vasai Road Jn, in metres.

This file replaces the old pixel topology, where the station was an SVG drawing
and `METRES_PER_UNIT = 10` turned drawing units into distance. That made every
Western route exactly 15.80 km, the Diva branch 17.4 km (its own label said
36.8), platforms 5.5 km long and a shunt move 6.5 km. Every ETA, headway and
separation in the console descended from those numbers.

Here, position is CHAINAGE IN METRES measured from the centre of the Vasai Road
platform line: positive towards Virar on the Western main, positive towards Diva
on the branch, negative towards Mumbai. Screen coordinates are derived from
chainage at render time and appear nowhere in the physics.

Distances are the published ones. The station distances below come from the
"Nearby Stations" block of the India Rail Info BSR page supplied as
`Vasai Local Train data.pdf`; the outer Diva-branch stations come from the
branch timetable. Nothing here is chosen to make a drawing look balanced.

    python tools/ingest/gen_network.py
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "network-bsr.json"

# --------------------------------------------------------------------- extents
# Station limits: home signal to home signal. A junction of this size works to
# roughly 1.2 km either side of the platform line.
STATION_LIMIT_M = 1200.0
# How far out of the junction the twin models: the approach-control area. 8 km
# on each corridor is ~5 minutes at line speed, so every movement inside the
# model can still reach the junction within the 15-minute prediction horizon,
# and a through run is 16 km rather than an unrealistically long 24 km.
MODELLED_REACH_M = 8000.0
# Vasai Road takes 24-coach long-distance formations, so every face is full length.
PLATFORM_LENGTH_M = 600.0

# Published distances from Vasai Road (metres).
STATIONS = {
    "NORTH": [("NSP", "Nalla Sopara", 4000.0), ("VR", "Virar", 8000.0),
              ("VTN", "Vaitarna", 17000.0), ("DRD", "Dahanu Road", 72000.0)],
    "SOUTH": [("NIG", "Naigaon", 4000.0), ("BYR", "Bhayandar", 9000.0),
              ("MIRA", "Mira Road", 12000.0), ("BVI", "Borivali", 18000.0)],
    "DIVA": [("JCNR", "Juchandra", 5000.0), ("KARD", "Kaman Road", 11000.0),
             ("KHBV", "Kharbao", 17200.0), ("BIRD", "Bhiwandi Road", 23500.0),
             ("KOPR", "Kopar", 31000.0), ("DIVA", "Diva Jn", 36800.0)],
}

CORRIDORS = {
    "NORTH": {
        "label": "Western main - Nalla Sopara / Virar / Dahanu Road",
        "shortLabel": "TOWARD VIRAR / DAHANU ROAD",
        "screenDir": "RIGHT", "reachM": MODELLED_REACH_M,
        "lineSpeedKmh": 110,
    },
    "SOUTH": {
        "label": "Western main - Naigaon / Bhayandar / Mumbai",
        "shortLabel": "TOWARD BHAYANDAR / CHURCHGATE",
        "screenDir": "LEFT", "reachM": MODELLED_REACH_M,
        "lineSpeedKmh": 110,
    },
    "DIVA": {
        "label": "Vasai Road - Diva branch (Bhiwandi / Panvel / Kalyan)",
        "shortLabel": "TOWARD DIVA JN - BHIWANDI / PANVEL / KALYAN",
        "screenDir": "DOWN_LEFT", "reachM": MODELLED_REACH_M,
        # Double line, absolute block, 100 km/h sectional speed.
        "lineSpeedKmh": 100,
    },
    "YARD": {
        "label": "Vasai Road goods yard and reception lines",
        "shortLabel": "GOODS YARD",
        "screenDir": "UP", "reachM": 2400.0,
        "lineSpeedKmh": 25,
    },
}

# ------------------------------------------------------------------ running lines
# id, label, kind, direction, platform, sectional speed
LINES = [
    ("SLD", "Slow line (Down - Mumbai bound)", "SUBURBAN_SLOW", "DOWN", "PF1", 80),
    ("SLU", "Slow line (Up - Virar bound)", "SUBURBAN_SLOW", "UP", "PF2", 80),
    ("FSD", "Fast line (Down - Mumbai bound)", "SUBURBAN_FAST", "DOWN", "PF3", 100),
    ("FSU", "Fast line (Up - Virar bound)", "SUBURBAN_FAST", "UP", "PF4", 100),
    ("THD", "Through line (Down - Mumbai bound)", "MAIN_THROUGH", "DOWN", "PF5", 110),
    ("THU", "Through line (Up - north bound)", "MAIN_THROUGH", "UP", "PF6", 110),
    ("BRD", "Diva branch (Diva bound)", "BRANCH", "BRANCH_OUT", "PF6", 100),
    ("BRU", "Diva branch (Vasai bound)", "BRANCH", "BRANCH_IN", "PF7", 100),
    ("GDC", "Goods avoiding chord", "FREIGHT", "BOTH", None, 60),
    ("GYL", "Goods yard reception / loop", "YARD", "BOTH", None, 25),
]

PLATFORMS = [
    ("PF1", "PF 1", "WEST", "Slow line Down - Churchgate bound", ["SLD"]),
    ("PF2", "PF 2", "ISLAND", "Slow line Up - Virar bound", ["SLU"]),
    ("PF3", "PF 3", "ISLAND", "Fast line Down - Churchgate bound", ["FSD"]),
    ("PF4", "PF 4", "ISLAND", "Fast line Up - Virar / Dahanu bound", ["FSU"]),
    ("PF5", "PF 5", "ISLAND", "Through line Down - long distance", ["THD"]),
    ("PF6", "PF 6", "EAST", "Through Up / Diva branch - long distance", ["THU", "BRD"]),
    ("PF7", "PF 7", "EAST", "Diva branch arrivals / long distance", ["BRU"]),
]

# --------------------------------------------------------------------- resources
# Headway is the minimum interval between successive movements over a shared
# resource, from Indian Railways practice for this section:
#   * junction / turnout - route setting, clearing and release: 120 s
#     (180 s at the branch turnout, where the route crosses the running lines)
#   * platform road - arrival to next arrival on the same face: 120 s
#   * automatic-signalled suburban block on the Western main: 120 s
#     (Mumbai suburban is worked at a ~3 min design headway)
#   * absolute-block section on the Diva branch: 300 s
JUNCTIONS = [
    # id, label, corridor, chainage, length, headway
    ("J-N", "North end crossover", "NORTH", 1150.0, 320.0, 120.0),
    ("J-S", "South end crossover", "SOUTH", 1150.0, 320.0, 120.0),
    ("J-B", "Diva branch turnout", "DIVA", 900.0, 400.0, 180.0),
    ("J-G", "Goods chord turnout", "NORTH", 2100.0, 300.0, 150.0),
    ("J-Y", "Goods yard neck", "YARD", 400.0, 250.0, 120.0),
]

# Block sections: corridor -> (block boundaries in metres, headway).
# Blocks are held PER RUNNING LINE, not per corridor: the Down and the Up road
# are different physical track, so two trains in opposite directions never
# contend for the same block. Only following moves on the same line do.
BLOCK_PLAN = {
    # Western main: automatic block signalling at roughly 1.3 km spacing, which
    # is what lets Mumbai suburban work a ~3 min headway.
    "NORTH": ([1500.0, 2800.0, 4100.0, 5400.0, 6700.0, 8000.0], 120.0),
    "SOUTH": ([1500.0, 2800.0, 4100.0, 5400.0, 6700.0, 8000.0], 120.0),
    # Diva branch: absolute block, long sections, far coarser headway.
    "DIVA": ([1400.0, 4200.0, 8000.0], 300.0),
}

# Which running lines exist on each corridor. The goods avoiding chord links the
# north end straight to the branch without entering the platform roads.
CORRIDOR_LINES = {
    "NORTH": ["SLD", "SLU", "FSD", "FSU", "THD", "THU", "GDC"],
    "SOUTH": ["SLD", "SLU", "FSD", "FSU", "THD", "THU"],
    "DIVA": ["BRD", "BRU", "GDC"],
    "YARD": ["GYL"],
}

# Which running lines can occupy each junction. A junction is shared, and that
# sharing is exactly what makes conflicting routes conflict.
JUNCTION_LINES = {
    "J-N": ["SLD", "SLU", "FSD", "FSU", "THD", "THU"],
    "J-S": ["SLD", "SLU", "FSD", "FSU", "THD", "THU"],
    "J-B": ["BRD", "BRU", "GDC"],
    "J-G": ["GDC", "THU", "THD"],
    "J-Y": ["GYL", "GDC"],
}


def build() -> dict:
    corridors = {}
    for cid, spec in CORRIDORS.items():
        corridors[cid] = {
            **spec,
            "id": cid,
            "stations": [
                {"code": c, "name": n, "chainageM": m}
                for c, n, m in STATIONS.get(cid, [])
            ],
        }

    platforms = [
        {
            "id": pid, "label": label, "side": side, "usage": usage,
            "serves": serves,
            "lengthM": PLATFORM_LENGTH_M,
            "centreChainageM": 0.0,
            "startChainageM": -PLATFORM_LENGTH_M / 2,
            "endChainageM": PLATFORM_LENGTH_M / 2,
        }
        for pid, label, side, usage, serves in PLATFORMS
    ]

    lines = [
        {"id": lid, "label": label, "kind": kind, "direction": direction,
         "platformId": pid, "speedLimitKmh": speed}
        for lid, label, kind, direction, pid, speed in LINES
    ]

    resources: list[dict] = []
    for rid, label, corridor, chainage, length, headway in JUNCTIONS:
        resources.append({
            "id": rid, "label": label, "kind": "JUNCTION", "corridor": corridor,
            "lines": JUNCTION_LINES.get(rid, []),
            "fromM": chainage - length / 2, "toM": chainage + length / 2,
            "centreM": chainage, "lengthM": length,
            "headwaySec": headway, "capacity": 1,
        })

    for pid, label, side, usage, serves in PLATFORMS:
        resources.append({
            "id": pid, "label": f"{label} platform road", "kind": "PLATFORM",
            "corridor": "STATION", "lines": serves,
            "fromM": -PLATFORM_LENGTH_M / 2, "toM": PLATFORM_LENGTH_M / 2,
            "centreM": 0.0, "lengthM": PLATFORM_LENGTH_M,
            "headwaySec": 120.0, "capacity": 1,
        })

    for corridor, (edges, headway) in BLOCK_PLAN.items():
        for line_id in CORRIDOR_LINES[corridor]:
            for i in range(len(edges) - 1):
                a, b = edges[i], edges[i + 1]
                resources.append({
                    "id": f"BLK-{corridor[0]}-{line_id}-{i + 1}",
                    "label": f"{line_id} block {i + 1} ({corridor.title()})",
                    "kind": "BLOCK", "corridor": corridor, "lines": [line_id],
                    "fromM": a, "toM": b, "centreM": (a + b) / 2, "lengthM": b - a,
                    "headwaySec": headway, "capacity": 1,
                })

    return {
        "id": "BSR-NET-2026-08",
        "label": "Vasai Road Jn physical network",
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "units": "metres",
        "datum": (
            "Chainage 0 is the centre of the Vasai Road platform line. Every corridor "
            "measures OUTWARD from the station as a positive number, so distance along "
            "a route is the sum of absolute chainage changes. `screenDir` is a "
            "rendering hint only and never enters the physics."
        ),
        "stationLimitM": STATION_LIMIT_M,
        "modelledReachM": MODELLED_REACH_M,
        "sources": [
            {"label": "India Rail Info - BSR nearby-station distances",
             "file": "Vasai Local Train data.pdf",
             "covers": "Nalla Sopara, Virar, Naigaon, Bhayandar, Juchandra, Kaman Road, Vaitarna, Borivali"},
            {"label": "Vasai Road - Diva branch timetable distances",
             "covers": "Kharbao, Bhiwandi Road, Kopar, Diva Jn"},
        ],
        "corridorLines": CORRIDOR_LINES,
        "corridors": corridors,
        "lines": lines,
        "platforms": platforms,
        "resources": resources,
    }


def check(net: dict) -> list[str]:
    problems: list[str] = []
    corridors = net["corridors"]

    def want(cond: bool, msg: str) -> None:
        if not cond:
            problems.append(msg)

    diva = {s["code"]: s["chainageM"] for s in corridors["DIVA"]["stations"]}
    want(abs(diva["DIVA"] - 36800) < 1, f"Diva Jn should be 36.8 km, got {diva.get('DIVA')}")
    north = {s["code"]: s["chainageM"] for s in corridors["NORTH"]["stations"]}
    want(abs(north["VR"] - 8000) < 1, f"Virar should be 8 km, got {north.get('VR')}")
    want(all(abs(p["lengthM"] - 600) < 1 for p in net["platforms"]),
         "platforms must be 600 m")
    want(len({p["id"] for p in net["platforms"]}) == 7, "expected 7 platform faces")

    ids = [r["id"] for r in net["resources"]]
    want(len(ids) == len(set(ids)), "duplicate resource ids")
    for r in net["resources"]:
        want(r["toM"] > r["fromM"], f"{r['id']}: zero or negative length")
        want(r["headwaySec"] > 0, f"{r['id']}: non-positive headway")

    # Blocks on one running line must tile without gaps or overlaps.
    for corridor in BLOCK_PLAN:
        for line_id in CORRIDOR_LINES[corridor]:
            blocks = sorted((r for r in net["resources"]
                             if r["kind"] == "BLOCK" and r["corridor"] == corridor
                             and r["lines"] == [line_id]),
                            key=lambda r: r["fromM"])
            for a, b in zip(blocks, blocks[1:]):
                want(abs(a["toM"] - b["fromM"]) < 1e-6,
                     f"{corridor}/{line_id}: gap between {a['id']} and {b['id']}")

    line_ids = {ln["id"] for ln in net["lines"]}
    for r in net["resources"]:
        want(bool(r["lines"]), f"{r['id']}: no running line uses this resource")
        unknown = set(r["lines"]) - line_ids
        want(not unknown, f"{r['id']}: unknown lines {sorted(unknown)}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--verify", action="store_true")
    args = ap.parse_args()

    net = build()
    problems = check(net)

    kinds: dict[str, int] = {}
    for r in net["resources"]:
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
    print(f"corridors : {list(net['corridors'])}")
    print(f"lines     : {len(net['lines'])}   platforms: {len(net['platforms'])}")
    print(f"resources : {kinds}")
    print(f"lines/corr: { {c: len(v) for c, v in CORRIDOR_LINES.items()} }")
    print(f"platform  : {net['platforms'][0]['lengthM']:.0f} m")
    print(f"Diva Jn   : {net['corridors']['DIVA']['stations'][-1]['chainageM'] / 1000:.1f} km")

    if problems:
        print("\n[FAIL]")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("\n[ok] all checks passed")
    if not args.verify:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(net, indent=1), encoding="utf-8")
        print(f"[ok] wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
