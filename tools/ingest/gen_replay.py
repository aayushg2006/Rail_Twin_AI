"""Build data/railradar-replay.json - a recorded live feed for demos.

RailRadar's free tier is 1,000 requests a month and a demo cannot afford to fail
because a network call timed out on stage. `RAILTWIN_RAILRADAR_MODE=replay`
serves this file instead, exercising the exact same ingestion, assimilation and
collection path as `live` - only the transport differs.

Two ways to build it:

    python tools/ingest/gen_replay.py                  synthesise a plausible feed
    python tools/ingest/gen_replay.py --from-live      capture from the real API
                                                       (needs RAILTWIN_RAILRADAR_API_KEY)

The synthesised lateness distribution is drawn from published Indian Railways
punctuality: most suburban services within a couple of minutes, a long thin tail
of badly delayed long-distance trains.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "railradar-replay.json"

# Rough station codes on either side of Vasai Road, used as "last reported halt".
APPROACH = {
    "NORTH": ["VR", "NSP", "VTN", "BOR"],
    "SOUTH": ["NIG", "BYR", "MIRA", "BVI"],
    "DIVA": ["JCNR", "KARD", "KHBV", "BIRD"],
}


def _lateness(service_class: str, rng: random.Random) -> float:
    """Seconds late. Suburban runs tight; long distance has a heavy tail."""
    if service_class.startswith("LOCAL") or service_class == "SUBURBAN":
        if rng.random() < 0.70:
            return round(rng.uniform(0, 120), 0)
        return round(min(1500, rng.lognormvariate(5.0, 0.7)), 0)
    if rng.random() < 0.45:
        return round(rng.uniform(0, 240), 0)
    return round(min(7200, rng.lognormvariate(6.4, 0.85)), 0)


def synthesise(seed: int, limit: int) -> dict:
    sys.path.insert(0, str(ROOT / "backend"))
    from app.network.net import timetable_pack       # noqa: E402

    rng = random.Random(seed)
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    services = timetable_pack["services"]
    # One reading per distinct train number, spread across the day.
    seen: set[str] = set()
    observations: list[dict] = []
    for s in services:
        if s["number"] in seen:
            continue
        seen.add(s["number"])
        observations.append({
            "number": s["number"],
            "latenessSec": _lateness(s["serviceClass"], rng),
            "lastStation": rng.choice(APPROACH.get(s["arrivalCorridor"], ["BSR"])),
            "observedAt": (now - dt.timedelta(seconds=rng.randrange(0, 1800))
                           ).isoformat(timespec="seconds"),
        })
        if len(observations) >= limit:
            break
    return {
        "id": f"RR-REPLAY-SYNTH-{seed}",
        "generatedAt": now.isoformat(timespec="seconds"),
        "provenance": "synthetic",
        "provenanceNote": (
            "A stand-in for the RailRadar live feed so the console can be "
            "demonstrated without spending API budget or depending on the "
            "network. Lateness is drawn from a distribution matching published "
            "Indian Railways punctuality; these are NOT real observations. "
            "Run with --from-live to capture genuine readings."),
        "observations": observations,
    }


def capture(limit: int) -> dict:
    import asyncio
    sys.path.insert(0, str(ROOT / "backend"))
    from app.config import settings                   # noqa: E402
    from app.ingest.railradar import RailRadarClient  # noqa: E402
    from app.network.net import timetable_pack        # noqa: E402

    if not settings.railradar_api_key:
        raise SystemExit("RAILTWIN_RAILRADAR_API_KEY is not set")

    async def run() -> list[dict]:
        client = RailRadarClient(mode="live")
        out: list[dict] = []
        seen: set[str] = set()
        import time
        for s in timetable_pack["services"]:
            if len(out) >= limit:
                break
            if s["number"] in seen:
                continue
            seen.add(s["number"])
            obs = await client.live_status(s["number"], time.time())
            if obs:
                out.append(obs.as_dict())
        await client.aclose()
        return out

    observations = asyncio.run(run())
    return {
        "id": "RR-REPLAY-CAPTURED",
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "provenance": "observed",
        "provenanceNote": "Captured from the RailRadar live API.",
        "observations": observations,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--limit", type=int, default=10_000,
                    help="how many train numbers to include")
    ap.add_argument("--from-live", action="store_true",
                    help="capture from the real API instead of synthesising")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()

    pack = capture(args.limit) if args.from_live else synthesise(args.seed, args.limit)
    late = [o["latenessSec"] for o in pack["observations"]]
    on_time = sum(1 for v in late if v <= 300)
    print(f"observations : {len(late)}  ({pack['provenance']})")
    if late:
        print(f"lateness     : median {sorted(late)[len(late) // 2]:.0f}s  "
              f"max {max(late):.0f}s  within 5 min {on_time / len(late) * 100:.0f}%")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(pack, indent=1), encoding="utf-8")
    print(f"[ok] wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
