"""Generate data/freight-paths.json - 150 synthetic goods paths through Vasai Road.

Freight is the one traffic class with no public live feed: RailRadar covers
passenger services only, and FOIS is not open. So goods movements are SYNTHETIC,
and every record says so. What is NOT invented is their shape - rake types,
loaded/empty speed bands, load masses and the routes through the junction follow
Indian Railways freight practice for this section, so the contention they create
against the real passenger timetable is operationally realistic.

Deterministic: same seed, same 150 paths.

    python tools/ingest/gen_freight.py [--seed 42] [--count 150]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "freight-paths.json"

# rake code -> (label, commodity, laden speed, empty speed, tonnes, wagons, priority, weight)
RAKE_TYPES: dict[str, tuple[str, str, int, int, int, int, int, float]] = {
    "BLC": ("Container flat (CONCOR)", "CONTAINER", 75, 75, 2400, 45, 2, 4.5),
    "BTPN": ("Tank rake (POL)", "PETROLEUM", 60, 65, 3600, 50, 3, 4.0),
    "BOXN": ("Open hopper", "COAL_ORE", 50, 65, 4700, 58, 4, 2.5),
    "BCN": ("Covered van", "GENERAL_GOODS", 55, 65, 3800, 42, 4, 2.5),
    "BRN": ("Flat rake (steel)", "STEEL", 50, 60, 4200, 40, 4, 2.2),
    "NMG": ("Auto carrier", "AUTOMOBILE", 65, 70, 1600, 26, 3, 3.5),
}

# Route through the junction -> (arrival corridor, departure corridor, share).
# The goods chord lets freight bypass the passenger platforms; the Diva branch
# carries the JNPT / Konkan flow that actually conflicts with the branch MEMUs
# and the Diva-bound expresses.
PATHS: list[tuple[str, str, str, float]] = [
    ("FRT-N-DIVA", "NORTH", "DIVA", 0.34),   # Gujarat/north -> Panvel/JNPT
    ("FRT-DIVA-N", "DIVA", "NORTH", 0.30),   # returning empties/loads to the north
    ("FRT-N-S", "NORTH", "SOUTH", 0.12),     # through freight to Mumbai port
    ("FRT-S-N", "SOUTH", "NORTH", 0.12),
    ("FRT-YARD", "YARD", "DIVA", 0.07),      # yard reception -> branch
    ("FRT-SHUNT", "YARD", "YARD", 0.05),     # goods-yard shunt moves
]

# Freight is worked round the clock but paths bunch at night, when the suburban
# service thins out and the section has spare capacity.
HOUR_WEIGHTS = [
    3.0, 3.2, 3.4, 3.0, 2.4, 1.2, 0.6, 0.4, 0.5, 0.8, 1.2, 1.4,
    1.5, 1.4, 1.2, 0.9, 0.6, 0.4, 0.5, 1.0, 1.8, 2.4, 2.8, 3.0,
]


def build(count: int, seed: int) -> dict:
    rng = random.Random(seed)
    codes = list(RAKE_TYPES)
    code_weights = [3, 2, 4, 4, 2, 1]
    path_ids = [p[0] for p in PATHS]
    path_weights = [p[3] for p in PATHS]
    by_path = {p[0]: p for p in PATHS}

    paths: list[dict] = []
    for i in range(count):
        code = rng.choices(codes, weights=code_weights)[0]
        label, commodity, laden_kmh, empty_kmh, tonnes, wagons, priority, weight = RAKE_TYPES[code]
        laden = rng.random() < 0.62
        path_id = rng.choices(path_ids, weights=path_weights)[0]
        _, arrival, departure, _ = by_path[path_id]

        hour = rng.choices(range(24), weights=HOUR_WEIGHTS)[0]
        dep_sec = hour * 3600 + rng.randrange(0, 3600)
        speed = laden_kmh if laden else empty_kmh
        shunt = path_id == "FRT-SHUNT"
        if shunt:
            # A yard shunt is a short light move, not a booked line path.
            label, commodity = "Yard shunt", "SHUNT"
            speed, laden, priority, weight = 15, False, 6, 0.5
            wagons = rng.randrange(4, 14)
            tonnes = wagons * 22

        paths.append({
            "id": f"F-{4000 + i}",
            "number": str(4000 + i),
            "name": f"{label} - {'loaded' if laden else 'empty'}",
            "category": "SHUNT" if shunt else "FREIGHT",
            "serviceClass": "SHUNT" if shunt else (
                "PREMIUM_FREIGHT" if code in ("BLC", "NMG")
                else "FREIGHT" if laden else "FREIGHT_EMPTY"),
            "rakeType": "SHUNT" if shunt else code,
            "commodity": commodity,
            "laden": laden,
            "grossTonnes": tonnes if (laden or shunt) else round(tonnes * 0.32),
            "wagons": wagons,
            "priority": priority if (laden or shunt) else priority + 1,
            "economicWeight": weight if (laden or shunt) else round(weight * 0.4, 1),
            "typicalLoad": 0,
            "lineSpeedKmh": speed,
            "dwellSec": 0,
            "bookedDepSec": dep_sec,
            "bookedDepHhmm": f"{hour:02d}:{dep_sec % 3600 // 60:02d}",
            "bookedPlatform": None,
            "operatingDays": "SMTWTFS",
            "dayConfidence": "SYNTHETIC",
            "origin": "Vasai Road Goods" if arrival == "YARD" else arrival,
            "destination": "Vasai Road Goods" if departure == "YARD" else departure,
            "arrivalCorridor": arrival,
            "departureCorridor": departure,
            "corridorConfidence": "HIGH",
            "pathId": path_id,
            "source": "synthetic-freight",
            "provenance": "synthetic",
        })

    paths.sort(key=lambda p: p["bookedDepSec"])
    return {
        "id": f"BSR-FRT-SYNTH-{seed}",
        "label": "Vasai Road synthetic goods paths",
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "seed": seed,
        "provenance": "synthetic",
        "provenanceNote": (
            "Goods movements are generated, not observed: RailRadar exposes passenger "
            "services only and FOIS is not publicly available. Rake types, speed bands, "
            "tonnages and junction paths follow Indian Railways freight practice for "
            "this section so the contention against the real passenger timetable is "
            "realistic, but no individual path corresponds to a real train."
        ),
        "paths": paths,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--count", type=int, default=150)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()

    pack = build(args.count, args.seed)
    counts: dict[str, int] = {}
    for p in pack["paths"]:
        counts[p["rakeType"]] = counts.get(p["rakeType"], 0) + 1
    assert all(p["grossTonnes"] < 400 for p in pack["paths"] if p["rakeType"] == "SHUNT")
    path_counts: dict[str, int] = {}
    for p in pack["paths"]:
        path_counts[p["pathId"]] = path_counts.get(p["pathId"], 0) + 1

    assert len(pack["paths"]) == args.count
    assert all(0 <= p["bookedDepSec"] < 86400 for p in pack["paths"])
    assert all(p["provenance"] == "synthetic" for p in pack["paths"])

    print(f"paths     : {len(pack['paths'])}")
    print(f"rake types: {counts}")
    print(f"junction  : {path_counts}")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(pack, indent=1), encoding="utf-8")
    print(f"[ok] wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
