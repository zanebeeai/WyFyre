from __future__ import annotations

import argparse
import statistics
import time
from datetime import datetime
from pathlib import Path

from config_loader import ConfigBundle, save_json
from models import RawDetection
from transport_serial import SerialNodeReceiver
from transport_udp import UdpNodeReceiver


def run_calibration(project_root: Path, duration_s: float, expected_x_mm: float, expected_y_mm: float) -> None:
    cfg = ConfigBundle(project_root)
    local_y_sign = float(cfg.runtime.get("fusion", {}).get("coordinate_convention", {}).get("local_y_sign", -1))

    if cfg.runtime["transport"]["mode"] == "serial":
        serial_cfg = cfg.runtime["transport"]["serial_fallback"]
        rx = SerialNodeReceiver(serial_cfg["ports"], int(serial_cfg["baud"]))
        pop = rx.pop
    else:
        rx = UdpNodeReceiver(
            cfg.runtime["transport"]["udp_bind_host"],
            int(cfg.runtime["transport"]["udp_data_port"]),
        )
        pop = rx.pop

    rx.start()
    print(f"Collecting {duration_s:.1f}s of samples. Stand at x={expected_x_mm:.0f}mm y={expected_y_mm:.0f}mm")

    start = time.time()
    per_sensor_global_x: dict[str, list[float]] = {}
    per_sensor_global_y: dict[str, list[float]] = {}

    try:
        while (time.time() - start) < duration_s:
            pkt = pop()
            if pkt is None:
                time.sleep(0.01)
                continue
            data = pkt.data
            if data.get("msg") != "detections":
                continue

            ts = int(data.get("timestamp_ms", int(time.time() * 1000)))
            for d in data.get("detections", []):
                det = RawDetection(
                    node_id=str(data.get("node_id", "?")),
                    sensor_id=str(d.get("sensor_id", "")),
                    sensor_index=int(d.get("sensor_index", -1)),
                    timestamp_ms=ts,
                    target_id=int(d.get("target_id", -1)),
                    x_mm=int(d.get("x_mm", 0)),
                    y_mm=int(d.get("y_mm", 0)),
                    speed_cms=int(d.get("speed_cms", 0)),
                    distance_resolution_mm=int(d.get("distance_resolution_mm", 0)),
                    active=bool(d.get("active", False)),
                )
                if not det.active:
                    continue
                sx, sy = cfg.sensor_offset(det.sensor_id)
                gx = sx + det.x_mm
                gy = sy + local_y_sign * det.y_mm
                if abs(gy - expected_y_mm) > 1500:
                    continue
                per_sensor_global_x.setdefault(det.sensor_id, []).append(gx)
                per_sensor_global_y.setdefault(det.sensor_id, []).append(gy)
    finally:
        rx.stop()

    calibration = cfg.calibration
    calibration["generated_at"] = datetime.utcnow().isoformat() + "Z"
    for sid in cfg.sensor_by_id:
        xs = per_sensor_global_x.get(sid, [])
        ys = per_sensor_global_y.get(sid, [])
        if not xs or not ys:
            print(f"{sid}: no valid samples")
            continue
        median_x = statistics.median(xs)
        median_y = statistics.median(ys)
        bias_x = expected_x_mm - median_x
        bias_y = expected_y_mm - median_y
        calibration.setdefault("sensor_bias", {}).setdefault(sid, {})["x_bias_mm"] = round(bias_x, 1)
        calibration.setdefault("sensor_bias", {}).setdefault(sid, {})["y_bias_mm"] = round(bias_y, 1)
        print(f"{sid}: median=({median_x:.1f},{median_y:.1f}) -> bias=({bias_x:.1f},{bias_y:.1f})")

    save_json(project_root / "config" / "calibration.json", calibration)
    print("Updated config/calibration.json")


def main() -> None:
    parser = argparse.ArgumentParser(description="WyFyre array offset calibration")
    parser.add_argument("--duration", type=float, default=10.0, help="Capture duration in seconds")
    parser.add_argument("--x", type=float, default=0.0, help="Expected global X in mm")
    parser.add_argument("--y", type=float, default=2000.0, help="Expected global Y in mm")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    run_calibration(project_root, args.duration, args.x, args.y)


if __name__ == "__main__":
    main()
