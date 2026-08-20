"""Fetch the real Vasai Road track layout from OpenStreetMap.

Run once; the committed `data/vasai-osm.json` is what the network generator
reads, so the twin has no runtime dependency on Overpass.

    python tools/ingest/fetch_osm.py

Why this exists: the station was previously a hand-drawn schematic with invented
platform sides and the Diva branch leaving to the south-WEST. OSM has the actual
infrastructure - 39 track ways, 6 switches, 5 signals, and platform polygons
tagged `2;3`, `4;5`, `6;7`, which is how Vasai Road really is laid out: PF1 as a
side platform plus three islands. The branch leaves the SOUTH end heading EAST.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "vasai-osm.json"
OVERPASS = "https://overpass-api.de/api/interpreter"

# Vasai Road station node, from OSM.
STATION_LAT = 19.3826676
STATION_LNG = 72.8320245

# Enough to take in the whole junction: the quadruple main from Naigaon to past
# Virar Road, the Diva branch turnout and the goods yard.
BBOX = (19.3650, 72.8150, 19.4020, 72.8500)

QUERY = """
[out:json][timeout:90];
(
  way["railway"~"^(rail|platform)$"]({s},{w},{n},{e});
  node["railway"~"^(station|halt|switch|signal|crossing|milestone)$"]({s},{w},{n},{e});
);
out geom;
"""


def metres_per_degree(lat: float) -> tuple[float, float]:
    """Local scale factors. At 19.4 degrees an equirectangular approximation is
    accurate to a few centimetres over a 20 km box, so no projection library is
    needed (and no image rebuild to add one)."""
    return 111_132.0, 111_320.0 * math.cos(math.radians(lat))


def fetch() -> dict:
    body = QUERY.format(s=BBOX[0], w=BBOX[1], n=BBOX[2], e=BBOX[3]).encode()
    request = urllib.request.Request(
        OVERPASS, data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "User-Agent": "rail-twin/1.0 (SIH prototype)"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def build(raw: dict) -> dict:
    per_lat, per_lng = metres_per_degree(STATION_LAT)

    def to_local(lat: float, lng: float) -> tuple[float, float]:
        """Metres east and north of the station node."""
        return ((lng - STATION_LNG) * per_lng, (lat - STATION_LAT) * per_lat)

    elements = raw.get("elements", [])
    ways, platforms, nodes = [], [], []

    for element in elements:
        tags = element.get("tags", {})
        kind = tags.get("railway")
        if element["type"] == "way" and element.get("geometry"):
            points = [{"lat": p["lat"], "lng": p["lon"]} for p in element["geometry"]]
            local = [to_local(p["lat"], p["lng"]) for p in points]
            length = sum(math.dist(local[i], local[i + 1]) for i in range(len(local) - 1))
            record = {
                "id": element["id"],
                "name": tags.get("name"),
                "usage": tags.get("usage"),
                "service": tags.get("service"),
                "electrified": tags.get("electrified"),
                "maxspeed": tags.get("maxspeed"),
                "points": points,
                "local": [{"x": round(x, 1), "y": round(y, 1)} for x, y in local],
                "lengthM": round(length, 1),
            }
            if kind == "platform":
                record["ref"] = tags.get("ref")
                platforms.append(record)
            else:
                ways.append(record)
        elif element["type"] == "node" and kind:
            x, y = to_local(element["lat"], element["lon"])
            nodes.append({
                "id": element["id"], "kind": kind,
                "name": tags.get("name"), "ref": tags.get("ref"),
                "lat": element["lat"], "lng": element["lon"],
                "local": {"x": round(x, 1), "y": round(y, 1)},
            })

    return {
        "id": "BSR-OSM",
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "source": "OpenStreetMap via Overpass API",
        "licence": "ODbL 1.0 - (c) OpenStreetMap contributors",
        "station": {"code": "BSR", "name": "Vasai Road",
                    "lat": STATION_LAT, "lng": STATION_LNG},
        "bbox": {"south": BBOX[0], "west": BBOX[1], "north": BBOX[2], "east": BBOX[3]},
        "localFrame": {
            "originLat": STATION_LAT, "originLng": STATION_LNG,
            "metresPerDegreeLat": round(per_lat, 2),
            "metresPerDegreeLng": round(per_lng, 2),
            "note": ("x is metres east of the station node, y is metres north. "
                     "Equirectangular at this latitude; accurate to centimetres "
                     "over the modelled box."),
        },
        "ways": ways,
        "platforms": platforms,
        "nodes": nodes,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()

    pack = build(fetch())
    switches = [n for n in pack["nodes"] if n["kind"] == "switch"]
    signals = [n for n in pack["nodes"] if n["kind"] == "signal"]
    sidings = [w for w in pack["ways"] if w["service"] == "siding"]
    print(f"track ways : {len(pack['ways'])} ({len(sidings)} sidings)")
    print(f"platforms  : {[(p['ref'], round(p['lengthM'])) for p in pack['platforms']]}")
    print(f"switches   : {len(switches)}   signals: {len(signals)}")
    span_x = [p["x"] for w in pack["ways"] for p in w["local"]]
    span_y = [p["y"] for w in pack["ways"] for p in w["local"]]
    print(f"extent     : x {min(span_x):.0f}..{max(span_x):.0f} m, "
          f"y {min(span_y):.0f}..{max(span_y):.0f} m")

    assert pack["platforms"], "no platform polygons returned"
    assert switches, "no switches returned"
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(pack, indent=1), encoding="utf-8")
    print(f"[ok] wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
