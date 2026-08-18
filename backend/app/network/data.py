"""Canonical shared Vasai Road data-pack loader.

The JSON lives outside the frontend/backend packages so both runtimes consume
the same schedule and provenance metadata. Docker copies it to /srv/data.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_data_pack() -> dict[str, Any]:
    candidates = [
        Path(__file__).resolve().parents[3] / "data" / "vasai-data.json",
        Path("/srv/data/vasai-data.json"),
        Path("data/vasai-data.json"),
    ]
    for path in candidates:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    raise FileNotFoundError("Canonical data pack data/vasai-data.json was not found")


data_pack = load_data_pack()
