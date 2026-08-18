"""ModelRegistry — tracks trained model metadata (Phase 4)."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field


@dataclass
class ModelRecord:
    model_name: str
    version: str
    trained_at: str
    dataset_version: str
    features: list[str]
    metrics: dict
    artifact_path: str


class ModelRegistry:
    def __init__(self, artifacts_dir: str):
        self.dir = artifacts_dir
        self.path = os.path.join(artifacts_dir, "registry.json")

    def load(self) -> dict[str, ModelRecord]:
        if not os.path.exists(self.path):
            return {}
        raw = json.loads(open(self.path).read())
        return {k: ModelRecord(**v) for k, v in raw.items()}

    def save(self, records: dict[str, ModelRecord]) -> None:
        os.makedirs(self.dir, exist_ok=True)
        with open(self.path, "w") as fh:
            json.dump({k: asdict(v) for k, v in records.items()}, fh, indent=2)
