from __future__ import annotations

import math
from typing import Any

import numpy as np

from config_loader import ConfigBundle
from cv_feedback import CvFeedbackAdapter
from models import FusedTarget, FusionResult, GlobalDetection, RawDetection, TrackState


class FusionEngine:
    def __init__(self, config: ConfigBundle, cv_feedback: CvFeedbackAdapter | None = None) -> None:
        self.config = config
        self.cv_feedback = cv_feedback or CvFeedbackAdapter()

        fusion_cfg = self.config.runtime["fusion"]
        coord_cfg = fusion_cfg.get("coordinate_convention", {})
        self.local_y_sign = float(coord_cfg.get("local_y_sign", -1))
        self.cluster_radius_mm = float(fusion_cfg["cluster_radius_mm"])
        self.track_match_radius_mm = float(fusion_cfg["track_match_radius_mm"])
        self.track_timeout_ms = int(fusion_cfg["track_timeout_ms"])
        self.single_target_min_confidence = float(fusion_cfg["single_target_min_confidence"])
        self.weights = dict(fusion_cfg["confidence_weights"])

        hm_cfg = fusion_cfg["heatmap"]
        self.hm_x_min = float(hm_cfg["x_min_mm"])
        self.hm_x_max = float(hm_cfg["x_max_mm"])
        self.hm_y_min = float(hm_cfg["y_min_mm"])
        self.hm_y_max = float(hm_cfg["y_max_mm"])
        self.hm_cell = float(hm_cfg["cell_size_mm"])
        self.hm_sigma = float(hm_cfg["gaussian_sigma_mm"])
        self.hm_alpha = float(hm_cfg["temporal_alpha"])

        self.x_grid = np.arange(self.hm_x_min, self.hm_x_max + self.hm_cell, self.hm_cell)
        self.y_grid = np.arange(self.hm_y_min, self.hm_y_max + self.hm_cell, self.hm_cell)
        self.mesh_x, self.mesh_y = np.meshgrid(self.x_grid, self.y_grid)
        self.prev_heatmap = np.zeros_like(self.mesh_x, dtype=float)

        self.tracks: list[TrackState] = []
        self.next_track_id = 1

    def process(self, raw_detections: list[RawDetection], timestamp_ms: int, mode: str) -> FusionResult:
        global_detections = [
            self._to_global(d)
            for d in raw_detections
            if d.active and self.config.sensor_enabled(d.sensor_id)
        ]
        clusters = self._cluster_detections(global_detections)
        fused_targets = self._fuse_clusters(clusters, timestamp_ms)

        if mode == "single":
            fused_targets = [
                t
                for t in sorted(fused_targets, key=lambda t: t.confidence, reverse=True)
                if t.confidence >= self.single_target_min_confidence
            ][:1]

        heatmap = self._build_heatmap(fused_targets)
        extent = (self.hm_x_min, self.hm_x_max, self.hm_y_min, self.hm_y_max)
        return FusionResult(
            timestamp_ms=timestamp_ms,
            global_detections=global_detections,
            fused_targets=fused_targets,
            heatmap=heatmap,
            heatmap_extent=extent,
        )

    def _to_global(self, det: RawDetection) -> GlobalDetection:
        sx, sy = self.config.sensor_offset(det.sensor_id)
        bx, by = self.config.sensor_bias_offset(det.sensor_id)
        gx = sx + float(det.x_mm) + bx
        gy = sy + self.local_y_sign * float(det.y_mm) + by
        angle_deg = math.degrees(math.atan2(gx, max(1.0, gy)))
        return GlobalDetection(
            raw=det,
            global_x_mm=gx,
            global_y_mm=gy,
            angle_deg=angle_deg,
            speed_abs_cms=abs(float(det.speed_cms)),
            sensor_weight=self.config.sensor_weight(det.sensor_id),
        )

    def _cluster_detections(self, detections: list[GlobalDetection]) -> list[list[GlobalDetection]]:
        clusters: list[list[GlobalDetection]] = []
        centroids: list[tuple[float, float]] = []

        for det in detections:
            assign_idx = -1
            best_dist = float("inf")
            for i, (cx, cy) in enumerate(centroids):
                dist = math.hypot(det.global_x_mm - cx, det.global_y_mm - cy)
                if dist <= self.cluster_radius_mm and dist < best_dist:
                    best_dist = dist
                    assign_idx = i

            if assign_idx < 0:
                clusters.append([det])
                centroids.append((det.global_x_mm, det.global_y_mm))
            else:
                clusters[assign_idx].append(det)
                xs = [d.global_x_mm for d in clusters[assign_idx]]
                ys = [d.global_y_mm for d in clusters[assign_idx]]
                centroids[assign_idx] = (float(np.mean(xs)), float(np.mean(ys)))

        return clusters

    def _fuse_clusters(self, clusters: list[list[GlobalDetection]], timestamp_ms: int) -> list[FusedTarget]:
        provisional: list[dict[str, Any]] = []
        for cluster in clusters:
            xs = [d.global_x_mm for d in cluster]
            ys = [d.global_y_mm for d in cluster]
            speeds = [d.raw.speed_cms for d in cluster]
            sensors = sorted({d.raw.sensor_id for d in cluster})
            provisional.append(
                {
                    "cluster": cluster,
                    "x": float(np.mean(xs)),
                    "y": float(np.mean(ys)),
                    "speed": float(np.mean(speeds)),
                    "sensors": sensors,
                }
            )

        self._update_tracks(provisional, timestamp_ms)

        fused_targets: list[FusedTarget] = []
        for p in provisional:
            track = p.get("track")
            persistence = 0.0
            track_id = -1
            if isinstance(track, TrackState):
                persistence = min(1.0, track.hits / 8.0)
                track_id = track.track_id

            confidence = self._compute_confidence(p["cluster"], persistence)
            confidence = self.cv_feedback.adjust_target_confidence(
                confidence,
                {
                    "x_mm": p["x"],
                    "y_mm": p["y"],
                    "sensors": p["sensors"],
                    "timestamp_ms": timestamp_ms,
                },
            )

            fused_targets.append(
                FusedTarget(
                    track_id=track_id,
                    x_mm=p["x"],
                    y_mm=p["y"],
                    confidence=confidence,
                    speed_cms=p["speed"],
                    sensors=p["sensors"],
                    member_count=len(p["cluster"]),
                    persistence=persistence,
                )
            )

        fused_targets.sort(key=lambda t: t.confidence, reverse=True)
        return fused_targets

    def _update_tracks(self, provisional: list[dict[str, Any]], timestamp_ms: int) -> None:
        for track in self.tracks:
            track.misses += 1

        used_tracks: set[int] = set()
        for p in provisional:
            best: TrackState | None = None
            best_dist = float("inf")
            for track in self.tracks:
                if track.track_id in used_tracks:
                    continue
                dist = math.hypot(p["x"] - track.x_mm, p["y"] - track.y_mm)
                if dist <= self.track_match_radius_mm and dist < best_dist:
                    best_dist = dist
                    best = track

            if best is None:
                best = TrackState(
                    track_id=self.next_track_id,
                    x_mm=p["x"],
                    y_mm=p["y"],
                    speed_cms=p["speed"],
                    confidence=0.0,
                    created_ms=timestamp_ms,
                    updated_ms=timestamp_ms,
                    hits=1,
                    misses=0,
                )
                self.next_track_id += 1
                self.tracks.append(best)
            else:
                best.x_mm = p["x"]
                best.y_mm = p["y"]
                best.speed_cms = p["speed"]
                best.updated_ms = timestamp_ms
                best.hits += 1
                best.misses = 0

            used_tracks.add(best.track_id)
            p["track"] = best

        self.tracks = [
            t
            for t in self.tracks
            if (timestamp_ms - t.updated_ms) <= self.track_timeout_ms and t.misses <= 10
        ]

    def _compute_confidence(self, cluster: list[GlobalDetection], persistence: float) -> float:
        if not cluster:
            return 0.0

        sensor_count = len({d.raw.sensor_id for d in cluster})
        sensor_agreement = min(1.0, sensor_count / 3.0)

        speeds = [abs(d.raw.speed_cms) for d in cluster]
        mean_speed = float(np.mean(speeds)) if speeds else 0.0
        speed_factor = max(0.0, 1.0 - min(mean_speed / 280.0, 1.0) * 0.4)

        angles = [d.angle_deg for d in cluster]
        angle_std = float(np.std(angles)) if len(angles) > 1 else 0.0
        angle_consistency = max(0.0, 1.0 - (angle_std / 35.0))

        sensor_weight = float(np.mean([d.sensor_weight for d in cluster]))
        sensor_weight_factor = min(1.0, sensor_weight / 1.15)

        dist_res = float(np.mean([d.raw.distance_resolution_mm for d in cluster]))
        dist_res_factor = max(0.0, min(1.0, 1.0 - (dist_res - 80.0) / 1200.0))

        weighted = {
            "sensor_agreement": sensor_agreement,
            "persistence": persistence,
            "speed": speed_factor,
            "angle_consistency": angle_consistency,
            "sensor_weight": sensor_weight_factor,
            "distance_resolution": dist_res_factor,
        }

        total_w = sum(self.weights.values())
        if total_w <= 0:
            return 0.0
        score = sum(float(self.weights[k]) * weighted[k] for k in weighted) / total_w
        return max(0.0, min(1.0, float(score)))

    def _build_heatmap(self, fused_targets: list[FusedTarget]) -> np.ndarray:
        hm = np.zeros_like(self.prev_heatmap)
        if fused_targets:
            denom = 2.0 * (self.hm_sigma**2)
            for t in fused_targets:
                dx = self.mesh_x - t.x_mm
                dy = self.mesh_y - t.y_mm
                hm += t.confidence * np.exp(-(dx * dx + dy * dy) / denom)

        hm = self.hm_alpha * hm + (1.0 - self.hm_alpha) * self.prev_heatmap
        self.prev_heatmap = hm
        return hm
