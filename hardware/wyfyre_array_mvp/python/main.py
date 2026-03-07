from __future__ import annotations

import json
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk

import numpy as np
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from config_loader import ConfigBundle
from cv_feedback import CvFeedbackAdapter
from dataset_logger import DatasetLogger
from fusion import FusionEngine
from models import RawDetection
from transport_serial import SerialNodeReceiver
from transport_udp import UdpNodeReceiver
from webcam_stub import WebcamCapture


SENSOR_COLORS = {
    "S0": "#3B82F6",
    "S1": "#10B981",
    "S2": "#F59E0B",
    "S3": "#F97316",
    "S4": "#EF4444",
}


class WyFyreArrayApp:
    def __init__(self, root: tk.Tk, project_root: Path) -> None:
        self.root = root
        self.project_root = project_root
        self.config = ConfigBundle(project_root)
        self.fusion = FusionEngine(self.config, cv_feedback=CvFeedbackAdapter())

        self.root.title("WyFyre Array MVP")
        self.root.geometry("1500x900")

        self.mode_var = tk.StringVar(value=self.config.runtime["app"].get("default_mode", "multi"))
        self.test_mode_var = tk.StringVar(value=self.config.runtime["app"].get("default_test_mode", "normal"))
        self.sensor_focus_var = tk.StringVar(value=self.config.runtime["app"].get("default_sensor_focus", "S2"))
        self.logging_var = tk.BooleanVar(value=bool(self.config.runtime["dataset_logging"].get("enabled", False)))

        self.raw_buffer: list[RawDetection] = []
        self.node_last_seen: dict[str, int] = {}
        self._timestamp_note_shown: set[str] = set()
        self.max_buffer_age_ms = 350

        self.logger = DatasetLogger(
            output_root=(project_root / "datasets").resolve(),
            save_heatmap_every_n=int(self.config.runtime["dataset_logging"].get("save_heatmap_npy_every_n_frames", 3)),
        )
        self.webcam = WebcamCapture(0)

        self.receiver_mode = self.config.runtime["transport"]["mode"]
        if self.receiver_mode == "serial":
            serial_cfg = self.config.runtime["transport"]["serial_fallback"]
            self.receiver = SerialNodeReceiver(ports=serial_cfg["ports"], baud=int(serial_cfg["baud"]))
        else:
            self.receiver = UdpNodeReceiver(
                bind_host=self.config.runtime["transport"]["udp_bind_host"],
                bind_port=int(self.config.runtime["transport"]["udp_data_port"]),
            )

        self._build_ui()
        self.receiver.start()
        self._log(f"Receiver started in {self.receiver_mode} mode")

        if self.logging_var.get():
            self._start_logging()

        self.refresh_ms = int(self.config.runtime["app"].get("refresh_ms", 80))
        self.root.after(self.refresh_ms, self._tick)

    def _build_ui(self) -> None:
        self.root.columnconfigure(0, weight=0)
        self.root.columnconfigure(1, weight=1)
        self.root.rowconfigure(0, weight=1)

        left = ttk.Frame(self.root, padding=10)
        left.grid(row=0, column=0, sticky="nsw")
        right = ttk.Frame(self.root, padding=10)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)

        control = ttk.LabelFrame(left, text="Control", padding=10)
        control.grid(row=0, column=0, sticky="ew", pady=(0, 10))

        ttk.Label(control, text="Fusion mode").grid(row=0, column=0, sticky="w")
        ttk.Combobox(control, textvariable=self.mode_var, values=["single", "multi"], state="readonly", width=10).grid(row=0, column=1, sticky="w", padx=(8, 0))
        ttk.Button(control, text="Set on nodes", command=self._push_mode_to_nodes).grid(row=0, column=2, sticky="w", padx=(8, 0))

        ttk.Label(control, text="Test mode").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Combobox(control, textvariable=self.test_mode_var, values=["normal", "sensor_test", "fused_sanity"], state="readonly", width=12).grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(8, 0))

        ttk.Label(control, text="Sensor focus").grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Combobox(control, textvariable=self.sensor_focus_var, values=["S0", "S1", "S2", "S3", "S4"], state="readonly", width=10).grid(row=2, column=1, sticky="w", padx=(8, 0), pady=(8, 0))

        ttk.Checkbutton(control, text="Dataset logging", variable=self.logging_var, command=self._toggle_logging).grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Button(control, text="Ping nodes", command=self._ping_nodes).grid(row=4, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(control, text="Query mode", command=self._query_nodes).grid(row=4, column=1, sticky="ew", pady=(8, 0), padx=(8, 0))

        status = ttk.LabelFrame(left, text="Node status", padding=10)
        status.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        self.node_status_var = tk.StringVar(value="Waiting for telemetry...")
        ttk.Label(status, textvariable=self.node_status_var, justify="left").pack(anchor="w")

        logs = ttk.LabelFrame(left, text="Logs", padding=8)
        logs.grid(row=2, column=0, sticky="nsew")
        left.rowconfigure(2, weight=1)
        self.log_widget = tk.Text(logs, height=20, width=42, wrap="word")
        self.log_widget.pack(fill="both", expand=True)
        self.log_widget.configure(state="disabled")

        raw = ttk.LabelFrame(left, text="Raw serial", padding=8)
        raw.grid(row=3, column=0, sticky="nsew", pady=(10, 0))
        left.rowconfigure(3, weight=1)
        self.raw_widget = tk.Text(raw, height=10, width=42, wrap="none")
        self.raw_widget.pack(fill="both", expand=True)
        self.raw_widget.configure(state="disabled")

        fig = Figure(figsize=(12, 6), dpi=100)
        self.ax_raw = fig.add_subplot(131)
        self.ax_fused = fig.add_subplot(132)
        self.ax_heat = fig.add_subplot(133)

        for ax, title in ((self.ax_raw, "Raw per-sensor"), (self.ax_fused, "Fused targets"), (self.ax_heat, "Confidence heatmap")):
            ax.set_title(title)
            ax.set_xlim(-3000, 3000)
            ax.set_ylim(0, 6000)
            ax.set_xlabel("X [mm]")
            ax.set_ylabel("Y [mm]")
            ax.grid(alpha=0.25)

        self.heatmap_img = self.ax_heat.imshow(
            np.zeros((10, 10)),
            origin="lower",
            extent=(-3000.0, 3000.0, 0.0, 6000.0),
            cmap="inferno",
            vmin=0,
            vmax=1,
            aspect="auto",
        )

        self.canvas = FigureCanvasTkAgg(fig, master=right)
        self.canvas.get_tk_widget().grid(row=0, column=0, sticky="nsew")

    def _tick(self) -> None:
        self._drain_packets()
        now_ms = int(time.time() * 1000)
        self.raw_buffer = [d for d in self.raw_buffer if (now_ms - d.timestamp_ms) <= self.max_buffer_age_ms]

        test_mode = self.test_mode_var.get()
        active_raw = self.raw_buffer
        if test_mode == "sensor_test":
            focus = self.sensor_focus_var.get()
            active_raw = [d for d in active_raw if d.sensor_id == focus]

        fusion_mode = self.mode_var.get()
        result = self.fusion.process(active_raw, now_ms, fusion_mode)

        if test_mode == "fused_sanity":
            result.fused_targets = sorted(result.fused_targets, key=lambda t: t.member_count, reverse=True)

        self._render(result)
        self._update_status_text(now_ms)

        if self.logging_var.get() and self.logger.enabled:
            webcam_path = None
            if bool(self.config.runtime["dataset_logging"].get("include_webcam", False)):
                webcam_path = self.webcam.capture_frame_to_path(self.logger.session_dir / "webcam", now_ms) if self.logger.session_dir else None
            self.logger.log_frame(
                timestamp_ms=now_ms,
                raw_detections=active_raw,
                global_detections=result.global_detections,
                fused_targets=result.fused_targets,
                heatmap=result.heatmap,
                webcam_frame_path=webcam_path,
            )

        self.root.after(self.refresh_ms, self._tick)

    def _drain_packets(self) -> None:
        while True:
            pkt = self.receiver.pop()
            if pkt is None:
                return

            raw_line = getattr(pkt, "raw_line", "")
            if raw_line:
                self._log_raw(raw_line)

            data = pkt.data
            msg = data.get("msg")
            node_id = data.get("node_id", "?")

            if msg == "detections":
                self.node_last_seen[node_id] = int(time.time() * 1000)
                receipt_ms = int(time.time() * 1000)
                source_ts = int(data.get("timestamp_ms", 0))
                for det in data.get("detections", []):
                    try:
                        self.raw_buffer.append(
                            RawDetection(
                                node_id=node_id,
                                sensor_id=str(det.get("sensor_id")),
                                sensor_index=int(det.get("sensor_index", -1)),
                                timestamp_ms=receipt_ms,
                                target_id=int(det.get("target_id", -1)),
                                x_mm=int(det.get("x_mm", 0)),
                                y_mm=int(det.get("y_mm", 0)),
                                speed_cms=int(det.get("speed_cms", 0)),
                                distance_resolution_mm=int(det.get("distance_resolution_mm", 0)),
                                active=bool(det.get("active", False)),
                            )
                        )
                    except Exception:
                        continue
                if source_ts and source_ts < 1000000 and node_id not in self._timestamp_note_shown:
                    self._log(f"Node {node_id}: using host timestamp for aging (source_ts={source_ts})")
                    self._timestamp_note_shown.add(node_id)
            else:
                self._log(f"Node {node_id}: {json.dumps(data, separators=(',', ':'))}")

    def _render(self, result) -> None:
        self.ax_raw.cla()
        self.ax_fused.cla()

        for ax, title in ((self.ax_raw, "Raw per-sensor"), (self.ax_fused, "Fused targets")):
            ax.set_title(title)
            ax.set_xlim(-3000, 3000)
            ax.set_ylim(0, 6000)
            ax.set_xlabel("X [mm]")
            ax.set_ylabel("Y [mm]")
            ax.grid(alpha=0.25)

        for sensor in self.config.geometry["sensors"]:
            sid = sensor["sensor_id"]
            color = SENSOR_COLORS.get(sid, "#60A5FA")
            sx = sensor["x_offset_mm"]
            self.ax_raw.scatter([sx], [0], marker="^", s=90, c=color)
            self.ax_raw.text(sx + 20, 80, sid, fontsize=8)

        grouped: dict[str, list] = {}
        for d in result.global_detections:
            grouped.setdefault(d.raw.sensor_id, []).append(d)

        for sid, items in grouped.items():
            color = SENSOR_COLORS.get(sid, "#60A5FA")
            self.ax_raw.scatter([i.global_x_mm for i in items], [i.global_y_mm for i in items], c=color, s=40, alpha=0.8)

        for tgt in result.fused_targets:
            size = 80 + 420 * tgt.confidence
            self.ax_fused.scatter([tgt.x_mm], [tgt.y_mm], s=size, c="#1D4ED8", alpha=0.75, edgecolors="#0F172A")
            self.ax_fused.text(
                tgt.x_mm + 40,
                tgt.y_mm + 40,
                f"id={tgt.track_id} c={tgt.confidence:.2f} n={len(tgt.sensors)}",
                fontsize=8,
            )

        self.heatmap_img.set_data(result.heatmap)
        self.heatmap_img.set_extent(result.heatmap_extent)
        self.ax_heat.set_title("Confidence heatmap")
        self.ax_heat.set_xlim(-3000, 3000)
        self.ax_heat.set_ylim(0, 6000)
        self.ax_heat.set_xlabel("X [mm]")
        self.ax_heat.set_ylabel("Y [mm]")

        self.canvas.draw_idle()

    def _update_status_text(self, now_ms: int) -> None:
        lines = []
        expected = self.config.runtime["transport"].get("expected_nodes", ["A", "B"])
        for n in expected:
            last = self.node_last_seen.get(n)
            if last is None:
                lines.append(f"Node {n}: offline")
            else:
                age = now_ms - last
                lines.append(f"Node {n}: online ({age} ms ago)")
        self.node_status_var.set("\n".join(lines))

    def _toggle_logging(self) -> None:
        if self.logging_var.get():
            self._start_logging()
        else:
            self._stop_logging()

    def _start_logging(self) -> None:
        path = self.logger.start()
        self._log(f"Dataset logging started: {path}")
        if bool(self.config.runtime["dataset_logging"].get("include_webcam", False)):
            if self.webcam.start():
                self._log("Webcam stub active")
            else:
                self._log("Webcam stub unavailable (cv2 missing or camera busy)")

    def _stop_logging(self) -> None:
        self.logger.stop()
        self.webcam.stop()
        self._log("Dataset logging stopped")

    def _push_mode_to_nodes(self) -> None:
        mode = self.mode_var.get()
        for node_id in self.config.runtime["transport"].get("expected_nodes", ["A", "B"]):
            ok = self.receiver.send_command(node_id, {"msg": "set_mode", "mode": mode})
            self._log(f"set_mode {mode} -> Node {node_id}: {'sent' if ok else 'not reachable'}")

    def _ping_nodes(self) -> None:
        for node_id in self.config.runtime["transport"].get("expected_nodes", ["A", "B"]):
            ok = self.receiver.send_command(node_id, {"msg": "ping"})
            self._log(f"ping -> Node {node_id}: {'sent' if ok else 'not reachable'}")

    def _query_nodes(self) -> None:
        for node_id in self.config.runtime["transport"].get("expected_nodes", ["A", "B"]):
            ok = self.receiver.send_command(node_id, {"msg": "query_mode"})
            self._log(f"query_mode -> Node {node_id}: {'sent' if ok else 'not reachable'}")

    def _log(self, msg: str) -> None:
        self.log_widget.configure(state="normal")
        self.log_widget.insert("end", msg + "\n")
        keep = int(self.config.runtime["app"].get("log_keep_lines", 500))
        line_count = int(self.log_widget.index("end-1c").split(".")[0])
        if line_count > keep:
            self.log_widget.delete("1.0", f"{line_count - keep}.0")
        self.log_widget.see("end")
        self.log_widget.configure(state="disabled")

    def _log_raw(self, msg: str) -> None:
        self.raw_widget.configure(state="normal")
        self.raw_widget.insert("end", msg + "\n")
        keep = int(self.config.runtime["app"].get("log_keep_lines", 500))
        line_count = int(self.raw_widget.index("end-1c").split(".")[0])
        if line_count > keep:
            self.raw_widget.delete("1.0", f"{line_count - keep}.0")
        self.raw_widget.see("end")
        self.raw_widget.configure(state="disabled")

    def shutdown(self) -> None:
        self.receiver.stop()
        self.logger.stop()
        self.webcam.stop()


def main() -> None:
    root = tk.Tk()
    project_root = Path(__file__).resolve().parent.parent
    app = WyFyreArrayApp(root, project_root)

    def on_close() -> None:
        app.shutdown()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
