"""Build data/timetable-bsr.json from the two India Rail Info PDF exports.

Run once (offline); the committed JSON is what the twin loads at runtime.

    python tools/ingest/parse_pdfs.py            # write data/timetable-bsr.json
    python tools/ingest/parse_pdfs.py --verify   # parse + assert, write nothing

Provenance is preserved per service so the console can never present a published
timing and a synthetic one as the same thing.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pdf_extract import RawService, extract_long_distance, extract_suburban  # noqa: E402
from regions import infer_corridors  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LONG = Path.home() / "Downloads" / "IRI-Departures-BSR-2.pdf"
DEFAULT_LOCAL = Path.home() / "Downloads" / "Vasai Local Train data.pdf"
OUT = ROOT / "data" / "timetable-bsr.json"

# Rake-type token -> (category, service class, priority, economic weight).
# Priority 0 is the highest. Economic weight is the relative cost of delaying
# this movement; `typicalLoad` below carries the passenger count separately so
# the optimiser can minimise passenger-minutes rather than an abstract score.
RAKE_CLASS: dict[str, tuple[str, str, int, float]] = {
    "Drnt": ("EXPRESS", "PREMIUM", 0, 10.0),
    "Raj": ("EXPRESS", "PREMIUM", 0, 10.0),
    "Shtb": ("EXPRESS", "PREMIUM", 0, 10.0),
    "VB": ("EXPRESS", "PREMIUM", 0, 10.0),
    "ACSF": ("EXPRESS", "PREMIUM", 0, 8.0),
    "ACExp": ("EXPRESS", "PREMIUM", 1, 7.0),
    "SF": ("EXPRESS", "SUPERFAST", 1, 6.0),
    "SKr": ("EXPRESS", "SUPERFAST", 1, 6.0),
    "Hms": ("EXPRESS", "SUPERFAST", 1, 6.0),
    "Exp": ("EXPRESS", "MAIL_EXPRESS", 2, 5.0),
    "MEMU": ("MEMU", "SUBURBAN", 3, 3.0),
    "Pass": ("PASSENGER", "PASSENGER", 4, 2.5),
}

# Approximate seated+standing load, used for passenger-minutes in the objective.
TYPICAL_LOAD = {
    "PREMIUM": 900, "SUPERFAST": 1300, "MAIL_EXPRESS": 1500, "PASSENGER": 1200,
    "SUBURBAN": 1200, "LOCAL_FAST": 2600, "LOCAL_SEMIFAST": 2600,
    "LOCAL_SLOW": 2400, "LOCAL_AC": 1800,
}

# Line speed by service class over the Vasai Road section (km/h).
LINE_SPEED = {
    "PREMIUM": 110, "SUPERFAST": 105, "MAIL_EXPRESS": 100, "PASSENGER": 80,
    "SUBURBAN": 80, "LOCAL_FAST": 100, "LOCAL_SEMIFAST": 90, "LOCAL_SLOW": 80,
    "LOCAL_AC": 100,
}

# Halt dwell at Vasai Road (seconds) by class.
DWELL = {
    "PREMIUM": 120, "SUPERFAST": 120, "MAIL_EXPRESS": 120, "PASSENGER": 60,
    "SUBURBAN": 45, "LOCAL_FAST": 30, "LOCAL_SEMIFAST": 30, "LOCAL_SLOW": 30,
    "LOCAL_AC": 30,
}

ID_PREFIX = {"EXPRESS": "E", "MEMU": "M", "LOCAL": "L", "PASSENGER": "P"}


def hhmm_to_sec(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 3600 + int(m) * 60


def clean_name(raw: str) -> str:
    """Undo the export's mid-word truncation and column bleed."""
    name = re.sub(r"\s+", " ", raw or "").strip()
    name = name.replace("…", "").replace("...", "")
    name = re.sub(r"(Mumb|MumbWR)$", "", name).strip()
    return re.sub(r"[\s\-]+$", "", name)


def local_class(name: str) -> str:
    low = name.lower()
    if "ac " in low or low.endswith(" ac") or "ac fast" in low or "ac local" in low:
        return "LOCAL_AC"
    if "semi" in low:
        return "LOCAL_SEMIFAST"
    if "fast" in low:
        return "LOCAL_FAST"
    return "LOCAL_SLOW"


def classify(raw: RawService) -> tuple[str, str, int, float]:
    """(category, service_class, priority, economic_weight)."""
    if raw.rake_type in RAKE_CLASS and raw.rake_type != "Mumb":
        return RAKE_CLASS[raw.rake_type]
    if raw.rake_type == "MEMU":
        return RAKE_CLASS["MEMU"]
    cls = local_class(raw.name)
    priority = 2 if cls in ("LOCAL_FAST", "LOCAL_AC") else 3
    weight = 4.0 if cls in ("LOCAL_FAST", "LOCAL_AC") else 3.5
    return ("LOCAL", cls, priority, weight)


def to_service(raw: RawService, seq: int) -> dict:
    name = clean_name(raw.name)
    category, service_class, priority, weight = classify(raw)
    arrival, departure, corridor_conf = infer_corridors(name, raw.dest, raw.platform)
    dep_sec = hhmm_to_sec(raw.dep_hhmm)
    arr_sec = hhmm_to_sec(raw.arr_hhmm) if raw.arr_hhmm else None
    return {
        "id": f"{ID_PREFIX.get(category, 'X')}-{raw.number}-{seq}",
        "number": raw.number,
        "name": name,
        "category": category,
        "serviceClass": service_class,
        "rakeType": raw.rake_type,
        "priority": priority,
        "economicWeight": weight,
        "typicalLoad": TYPICAL_LOAD.get(service_class, 1000),
        "lineSpeedKmh": LINE_SPEED.get(service_class, 90),
        "dwellSec": DWELL.get(service_class, 60),
        "bookedDepSec": dep_sec,
        "bookedDepHhmm": raw.dep_hhmm,
        "bookedPlatform": raw.platform,
        "operatingDays": raw.days,
        "dayConfidence": raw.day_confidence,
        "origin": raw.origin,
        "destination": raw.dest,
        "destArrSec": arr_sec,
        "halts": raw.halts,
        "distanceKm": raw.distance_km,
        "arrivalCorridor": arrival,
        "departureCorridor": departure,
        "corridorConfidence": corridor_conf,
        "source": raw.source,
        "provenance": "published-timetable",
    }


def build(long_pdf: Path, local_pdf: Path) -> dict:
    longs = extract_long_distance(str(long_pdf))
    locals_ = extract_suburban(str(local_pdf))

    # 26 suburban services appear in BOTH exports at the same minute. Keep the
    # long-distance copy, which is the one that carries the BSR platform number.
    services: list[dict] = []
    seen: set[tuple[str, int]] = set()
    duplicates = 0
    for raw in sorted(longs + locals_, key=lambda r: (r.dep_hhmm, r.number)):
        key = (raw.number, hhmm_to_sec(raw.dep_hhmm))
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        services.append(to_service(raw, len(services)))

    covered = [hhmm_to_sec(r.dep_hhmm) for r in longs]
    long_span = (min(covered), max(covered)) if covered else (0, 0)
    return {
        "id": "BSR-TT-2026-08-15",
        "label": "Vasai Road Jn booked departures",
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "referenceDate": "2026-08-15",
        "timezone": "Asia/Kolkata",
        "sources": [
            {
                "file": "IRI-Departures-BSR-2.pdf",
                "label": "India Rail Info - BSR departures (long distance)",
                "url": "https://indiarailinfo.com/departures/70",
                "rows": len(longs),
                "coverage": "PARTIAL",
                "coverageNote": (
                    "The export stops mid-day; it carries "
                    f"{len(longs)} of the 206 rows the page header reports, spanning "
                    f"{long_span[0] // 3600:02d}:{long_span[0] % 3600 // 60:02d}-"
                    f"{long_span[1] // 3600:02d}:{long_span[1] % 3600 // 60:02d}. "
                    "Backfill the remainder from the RailRadar station board."
                ),
            },
            {
                "file": "Vasai Local Train data.pdf",
                "label": "India Rail Info - BSR EMU departures (suburban)",
                "rows": len(locals_),
                "coverage": "FULL",
                "coverageNote": "Complete 24-hour suburban departure list.",
            },
        ],
        "duplicatesMerged": duplicates,
        "services": services,
    }


def verify(pack: dict) -> list[str]:
    problems: list[str] = []
    services = pack["services"]
    by_number: dict[str, list[dict]] = {}
    for s in services:
        by_number.setdefault(s["number"], []).append(s)

    def check(cond: bool, msg: str) -> None:
        if not cond:
            problems.append(msg)

    check(len(services) >= 520, f"expected >=520 services, got {len(services)}")
    check(all(0 <= s["bookedDepSec"] < 86400 for s in services), "departure out of range")
    check(all(len(s["operatingDays"]) == 7 for s in services), "bad operatingDays width")
    check(all(any(c != "." for c in s["operatingDays"]) for s in services),
          "a service runs on no day at all")

    # Spot-checks read straight off the PDFs.
    spot = {
        "12283": ("00:10", 6, "NZM", "..T...."),
        "93009": ("08:18", 4, "DRD", None),
        "61003": ("09:50", 6, "DIVA", None),
        "12928": (None, None, None, None),
    }
    for number, (dep, pf, dest, days) in spot.items():
        rows = by_number.get(number)
        if not rows:
            if number == "12928":
                continue  # outside the partial export's window
            problems.append(f"{number}: missing from parse")
            continue
        row = rows[0]
        if dep and row["bookedDepHhmm"] != dep:
            problems.append(f"{number}: dep {row['bookedDepHhmm']} != {dep}")
        if pf and row["bookedPlatform"] != pf:
            problems.append(f"{number}: platform {row['bookedPlatform']} != {pf}")
        if dest and row["destination"] != dest:
            problems.append(f"{number}: dest {row['destination']} != {dest}")
        if days and row["operatingDays"] != days:
            problems.append(f"{number}: days {row['operatingDays']} != {days}")

    # 61003 works the Diva branch; it must be routed onto it.
    memu = by_number.get("61003", [{}])[0]
    check(memu.get("departureCorridor") == "DIVA",
          f"61003 should depart towards DIVA, got {memu.get('departureCorridor')}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--long-pdf", type=Path, default=DEFAULT_LONG)
    ap.add_argument("--local-pdf", type=Path, default=DEFAULT_LOCAL)
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--verify", action="store_true", help="parse and check, write nothing")
    args = ap.parse_args()

    for path in (args.long_pdf, args.local_pdf):
        if not path.exists():
            print(f"[error] missing PDF: {path}", file=sys.stderr)
            return 2

    pack = build(args.long_pdf, args.local_pdf)
    problems = verify(pack)

    counts: dict[str, int] = {}
    corridors: dict[str, int] = {}
    for s in pack["services"]:
        counts[s["category"]] = counts.get(s["category"], 0) + 1
        key = f"{s['arrivalCorridor']}->{s['departureCorridor']}"
        corridors[key] = corridors.get(key, 0) + 1

    print(f"services      : {len(pack['services'])} "
          f"({pack['duplicatesMerged']} cross-export duplicates merged)")
    print(f"by category   : {counts}")
    print(f"by path       : {dict(sorted(corridors.items(), key=lambda kv: -kv[1]))}")
    low = sum(1 for s in pack["services"] if s["corridorConfidence"] == "LOW")
    print(f"low-confidence routing: {low}")

    if problems:
        print("\n[FAIL]")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("\n[ok] all checks passed")

    if not args.verify:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(pack, indent=1), encoding="utf-8")
        print(f"[ok] wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
