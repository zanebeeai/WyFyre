from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


class ConfigBundle:
    def __init__(self, root_dir: Path) -> None:
        cfg_dir = root_dir / "config"
        self.geometry = load_json(cfg_dir / "geometry.json")
        self.runtime = load_json(cfg_dir / "runtime.json")
        self.calibration = load_json(cfg_dir / "calibration.json")

        sensors = self.geometry.get("sensors", [])
        self.sensor_by_id: dict[str, dict[str, Any]] = {s["sensor_id"]: s for s in sensors}
        self.sensor_bias: dict[str, dict[str, float]] = self.calibration.get("sensor_bias", {})

    def sensor_weight(self, sensor_id: str) -> float:
        s = self.sensor_by_id.get(sensor_id, {})
        return float(s.get("weight", 1.0))

    def sensor_enabled(self, sensor_id: str) -> bool:
        s = self.sensor_by_id.get(sensor_id, {})
        return bool(s.get("enabled", True))

    def sensor_offset(self, sensor_id: str) -> tuple[float, float]:
        s = self.sensor_by_id.get(sensor_id, {})
        return float(s.get("x_offset_mm", 0.0)), float(s.get("y_offset_mm", 0.0))

    def sensor_bias_offset(self, sensor_id: str) -> tuple[float, float]:
        b = self.sensor_bias.get(sensor_id, {})
        return float(b.get("x_bias_mm", 0.0)), float(b.get("y_bias_mm", 0.0))
