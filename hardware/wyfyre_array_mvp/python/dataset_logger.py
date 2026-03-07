from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np

from models import FusedTarget, GlobalDetection, RawDetection


class DatasetLogger:
    def __init__(self, output_root: Path, save_heatmap_every_n: int = 3) -> None:
        self.output_root = output_root
        self.save_heatmap_every_n = max(1, save_heatmap_every_n)
        self.session_dir: Optional[Path] = None
        self._jsonl_path: Optional[Path] = None
        self._frame_idx = 0

    @property
    def enabled(self) -> bool:
        return self.session_dir is not None

    def start(self) -> Path:
        stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        self.session_dir = self.output_root / f"session_{stamp}"
        self.session_dir.mkdir(parents=True, exist_ok=True)
        (self.session_dir / "heatmaps").mkdir(exist_ok=True)
        (self.session_dir / "webcam").mkdir(exist_ok=True)
        self._jsonl_path = self.session_dir / "radar_fusion.jsonl"
        self._frame_idx = 0
        return self.session_dir

    def stop(self) -> None:
        self.session_dir = None
        self._jsonl_path = None

    def log_frame(
        self,
        timestamp_ms: int,
        raw_detections: list[RawDetection],
        global_detections: list[GlobalDetection],
        fused_targets: list[FusedTarget],
        heatmap: np.ndarray,
        webcam_frame_path: Optional[str],
    ) -> None:
        if self._jsonl_path is None or self.session_dir is None:
            return

        heatmap_path = None
        if self._frame_idx % self.save_heatmap_every_n == 0:
            heatmap_file = self.session_dir / "heatmaps" / f"heatmap_{timestamp_ms}.npy"
            np.save(heatmap_file, heatmap)
            heatmap_path = str(heatmap_file)

        record = {
            "timestamp_ms": timestamp_ms,
            "raw_detections": [asdict(d) for d in raw_detections],
            "global_detections": [
                {
                    **asdict(g.raw),
                    "global_x_mm": g.global_x_mm,
                    "global_y_mm": g.global_y_mm,
                    "angle_deg": g.angle_deg,
                    "speed_abs_cms": g.speed_abs_cms,
                    "sensor_weight": g.sensor_weight,
                }
                for g in global_detections
            ],
            "fused_targets": [asdict(t) for t in fused_targets],
            "heatmap_path": heatmap_path,
            "webcam_frame_path": webcam_frame_path,
        }

        with self._jsonl_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, separators=(",", ":")) + "\n")

        self._frame_idx += 1
