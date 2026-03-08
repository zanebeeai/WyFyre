from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RawDetection:
    node_id: str
    sensor_id: str
    sensor_index: int
    timestamp_ms: int
    target_id: int
    x_mm: int
    y_mm: int
    speed_cms: int
    distance_resolution_mm: int
    active: bool


@dataclass(frozen=True)
class GlobalDetection:
    raw: RawDetection
    global_x_mm: float
    global_y_mm: float
    angle_deg: float
    speed_abs_cms: float
    sensor_weight: float


@dataclass
class FusedTarget:
    track_id: int
    x_mm: float
    y_mm: float
    confidence: float
    speed_cms: float
    sensors: list[str] = field(default_factory=list)
    member_count: int = 0
    persistence: float = 0.0


@dataclass
class TrackState:
    track_id: int
    x_mm: float
    y_mm: float
    speed_cms: float
    confidence: float
    created_ms: int
    updated_ms: int
    hits: int = 1
    misses: int = 0


@dataclass
class FusionResult:
    timestamp_ms: int
    global_detections: list[GlobalDetection]
    fused_targets: list[FusedTarget]
    heatmap: Any
    heatmap_extent: tuple[float, float, float, float]
