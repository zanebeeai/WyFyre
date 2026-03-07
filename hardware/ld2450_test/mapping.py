from __future__ import annotations

import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from matplotlib.patches import Arc, Wedge
import numpy as np
from serial.tools import list_ports

from serial_protocol import LD2450Controller, ZoneFilteringConfig


HOST_BAUD = 115200
RADAR_BAUD_OPTIONS = [9600, 19200, 38400, 57600, 115200, 230400, 256000, 460800]
TARGET_COLORS = ["#0A84FF", "#30A46C", "#FF8A00"]
MAX_RANGE_MM = 6000
AZIMUTH_LIMIT_DEG = 60
TRAIL_HISTORY = 20


class LD2450App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("LD2450 Control Console")
        self.root.geometry("1280x760")

        self.controller: LD2450Controller | None = None

        self.port_var = tk.StringVar(value="")
        self.host_baud_var = tk.IntVar(value=HOST_BAUD)
        self.radar_baud_var = tk.StringVar(value="256000")
        self.bluetooth_var = tk.BooleanVar(value=True)
        self.zone_mode_var = tk.StringVar(value="0")
        self.track_mode_var = tk.StringVar(value="Unknown")
        self.fw_var = tk.StringVar(value="-")
        self.mac_var = tk.StringVar(value="-")

        self.target_vars = []
        for i in range(3):
            row = {
                "x": tk.StringVar(value="-"),
                "y": tk.StringVar(value="-"),
                "speed": tk.StringVar(value="-"),
                "dist": tk.StringVar(value="-"),
                "active": tk.StringVar(value="No"),
                "name": f"T{i+1}",
            }
            self.target_vars.append(row)

        self.zone_entries: list[tk.Entry] = []
        self.target_trails: list[list[tuple[int, int]]] = [[], [], []]
        self._build_ui()
        self.refresh_ports()
        self._schedule_update()

    def _build_ui(self) -> None:
        self.root.columnconfigure(0, weight=0)
        self.root.columnconfigure(1, weight=1)
        self.root.rowconfigure(0, weight=1)

        left = ttk.Frame(self.root, padding=10)
        left.grid(row=0, column=0, sticky="nsw")

        right = ttk.Frame(self.root, padding=10)
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(0, weight=1)
        right.rowconfigure(1, weight=0)
        right.columnconfigure(0, weight=1)

        self._build_connection_card(left)
        self._build_command_card(left)
        self._build_reference_card(left)
        self._build_zone_card(left)
        self._build_targets_card(left)

        self._build_plot(right)
        self._build_log(right)

    def _build_connection_card(self, parent: ttk.Frame) -> None:
        card = ttk.LabelFrame(parent, text="Connection", padding=10)
        card.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        card.columnconfigure(1, weight=1)

        ttk.Label(card, text="Port").grid(row=0, column=0, sticky="w")
        self.port_combo = ttk.Combobox(card, textvariable=self.port_var, width=14, state="readonly")
        self.port_combo.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        ttk.Button(card, text="Refresh", command=self.refresh_ports).grid(row=0, column=2, padx=(8, 0))

        ttk.Label(card, text="Host Baud").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(card, textvariable=self.host_baud_var, width=10).grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(8, 0))

        btns = ttk.Frame(card)
        btns.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        ttk.Button(btns, text="Connect", command=self.connect).pack(side="left", fill="x", expand=True)
        ttk.Button(btns, text="Disconnect", command=self.disconnect).pack(side="left", fill="x", expand=True, padx=(8, 0))

    def _build_command_card(self, parent: ttk.Frame) -> None:
        card = ttk.LabelFrame(parent, text="Device Commands", padding=10)
        card.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        card.columnconfigure(0, weight=1)
        card.columnconfigure(1, weight=1)

        ttk.Button(card, text="Enable Config", command=lambda: self._run_cmd("Enable config", self._cmd_enable_cfg)).grid(row=0, column=0, sticky="ew")
        ttk.Button(card, text="End Config", command=lambda: self._run_cmd("End config", self._cmd_end_cfg)).grid(row=0, column=1, sticky="ew", padx=(8, 0))

        ttk.Button(card, text="Single Target", command=lambda: self._run_cmd("Single target", self._cmd_single_target)).grid(row=1, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(card, text="Multi Target", command=lambda: self._run_cmd("Multi target", self._cmd_multi_target)).grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(8, 0))

        ttk.Button(card, text="Query Tracking", command=lambda: self._run_cmd("Query tracking", self._cmd_query_tracking)).grid(row=2, column=0, sticky="ew", pady=(8, 0))
        ttk.Label(card, textvariable=self.track_mode_var).grid(row=2, column=1, sticky="w", padx=(12, 0), pady=(8, 0))

        ttk.Button(card, text="Read Firmware", command=lambda: self._run_cmd("Read firmware", self._cmd_read_fw)).grid(row=3, column=0, sticky="ew", pady=(8, 0))
        ttk.Label(card, textvariable=self.fw_var).grid(row=3, column=1, sticky="w", padx=(12, 0), pady=(8, 0))

        ttk.Button(card, text="Get MAC", command=lambda: self._run_cmd("Get MAC", self._cmd_get_mac)).grid(row=4, column=0, sticky="ew", pady=(8, 0))
        ttk.Label(card, textvariable=self.mac_var).grid(row=4, column=1, sticky="w", padx=(12, 0), pady=(8, 0))

        baud_row = ttk.Frame(card)
        baud_row.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Label(baud_row, text="Radar Baud").pack(side="left")
        ttk.Combobox(
            baud_row,
            textvariable=self.radar_baud_var,
            values=[str(v) for v in RADAR_BAUD_OPTIONS],
            width=8,
            state="readonly",
        ).pack(side="left", padx=(8, 0))
        ttk.Button(baud_row, text="Set", command=lambda: self._run_cmd("Set radar baud", self._cmd_set_radar_baud)).pack(side="left", padx=(8, 0))

        misc_row = ttk.Frame(card)
        misc_row.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Checkbutton(misc_row, variable=self.bluetooth_var, text="Bluetooth On").pack(side="left")
        ttk.Button(misc_row, text="Apply", command=lambda: self._run_cmd("Bluetooth setup", self._cmd_bluetooth)).pack(side="left", padx=(8, 0))
        ttk.Button(misc_row, text="Restart", command=lambda: self._run_cmd("Restart module", self._cmd_restart)).pack(side="left", padx=(8, 0))
        ttk.Button(misc_row, text="Factory Reset", command=self._factory_reset_prompt).pack(side="left", padx=(8, 0))

    def _build_zone_card(self, parent: ttk.Frame) -> None:
        card = ttk.LabelFrame(parent, text="Zone Filtering", padding=10)
        card.grid(row=3, column=0, sticky="ew", pady=(0, 10))

        top = ttk.Frame(card)
        top.grid(row=0, column=0, sticky="ew")
        ttk.Label(top, text="Mode").pack(side="left")
        ttk.Combobox(top, textvariable=self.zone_mode_var, values=["0", "1", "2"], width=6, state="readonly").pack(side="left", padx=(8, 0))
        ttk.Button(top, text="Query", command=lambda: self._run_cmd("Query zone", self._cmd_query_zone)).pack(side="left", padx=(12, 0))
        ttk.Button(top, text="Apply", command=lambda: self._run_cmd("Apply zone", self._cmd_apply_zone)).pack(side="left", padx=(8, 0))

        grid = ttk.Frame(card)
        grid.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        labels = ["x1", "y1", "x2", "y2"]
        for c, label in enumerate(labels):
            ttk.Label(grid, text=label).grid(row=0, column=c + 1, padx=3)

        for r in range(3):
            ttk.Label(grid, text=f"R{r + 1}").grid(row=r + 1, column=0, sticky="w", padx=(0, 4), pady=2)
            for c in range(4):
                ent = ttk.Entry(grid, width=7)
                ent.grid(row=r + 1, column=c + 1, padx=3, pady=2)
                ent.insert(0, "0")
                self.zone_entries.append(ent)

    def _build_targets_card(self, parent: ttk.Frame) -> None:
        card = ttk.LabelFrame(parent, text="Targets", padding=10)
        card.grid(row=4, column=0, sticky="ew")

        headers = ["ID", "x [mm]", "y [mm]", "speed [cm/s]", "dist res [mm]", "active"]
        for c, title in enumerate(headers):
            ttk.Label(card, text=title).grid(row=0, column=c, sticky="w", padx=4)

        for r, row_vars in enumerate(self.target_vars, start=1):
            ttk.Label(card, text=row_vars["name"]).grid(row=r, column=0, sticky="w", padx=4)
            ttk.Label(card, textvariable=row_vars["x"]).grid(row=r, column=1, sticky="w", padx=4)
            ttk.Label(card, textvariable=row_vars["y"]).grid(row=r, column=2, sticky="w", padx=4)
            ttk.Label(card, textvariable=row_vars["speed"]).grid(row=r, column=3, sticky="w", padx=4)
            ttk.Label(card, textvariable=row_vars["dist"]).grid(row=r, column=4, sticky="w", padx=4)
            ttk.Label(card, textvariable=row_vars["active"]).grid(row=r, column=5, sticky="w", padx=4)

    def _build_plot(self, parent: ttk.Frame) -> None:
        fig = Figure(figsize=(8, 6), dpi=100)
        fig.patch.set_facecolor("#08131D")
        self.ax = fig.add_subplot(111)
        self.ax.set_title("LD2450 Radar Scope", color="#AEEFD8")
        self.ax.set_xlabel("X [mm]")
        self.ax.set_ylabel("Y [mm]")
        self.ax.set_facecolor("#041018")
        self.ax.grid(False)

        x_limit = int(MAX_RANGE_MM * np.sin(np.deg2rad(AZIMUTH_LIMIT_DEG))) + 400
        self.ax.set_xlim(-x_limit, x_limit)
        self.ax.set_ylim(-MAX_RANGE_MM - 400, 800)
        self.ax.set_aspect("equal", adjustable="box")
        self.ax.tick_params(colors="#8BC0A8")
        self.ax.xaxis.label.set_color("#8BC0A8")
        self.ax.yaxis.label.set_color("#8BC0A8")
        for spine in self.ax.spines.values():
            spine.set_color("#2B4A5C")

        self._draw_radar_backdrop()

        self.ax.scatter([0], [0], marker="^", s=180, c="#D8FBE8", zorder=6)
        self.ax.text(80, -100, "sensor", fontsize=10, color="#D8FBE8", zorder=6)

        self.scatter = self.ax.scatter([], [], s=180, c="#0A84FF", edgecolors="#E8F7FF", linewidths=0.8, zorder=8)
        self.trail_artists = [
            self.ax.plot([], [], color=TARGET_COLORS[i], alpha=0.35, linewidth=2, zorder=5)[0]
            for i in range(3)
        ]
        self.sweep_line = self.ax.plot([], [], color="#7DFFC8", alpha=0.75, linewidth=1.4, zorder=4)[0]
        self.ann = []

        self.canvas = FigureCanvasTkAgg(fig, master=parent)
        self.canvas.get_tk_widget().grid(row=0, column=0, sticky="nsew")

    def _draw_radar_backdrop(self) -> None:
        wedge = Wedge(
            center=(0, 0),
            r=MAX_RANGE_MM,
            theta1=-(90 + AZIMUTH_LIMIT_DEG),
            theta2=-(90 - AZIMUTH_LIMIT_DEG),
            facecolor="#0D2730",
            edgecolor="#2C8F6D",
            alpha=0.45,
            linewidth=1.6,
            zorder=1,
        )
        self.ax.add_patch(wedge)

        for meters in range(1, 7):
            r = meters * 1000
            ring = Arc(
                (0, 0),
                width=2 * r,
                height=2 * r,
                angle=0,
                theta1=-(90 + AZIMUTH_LIMIT_DEG),
                theta2=-(90 - AZIMUTH_LIMIT_DEG),
                color="#3C705E",
                linewidth=0.8,
                alpha=0.55,
                zorder=2,
            )
            self.ax.add_patch(ring)
            self.ax.text(70, -r + 80, f"{meters} m", color="#82C7AB", fontsize=8, zorder=3)

        for angle in (-60, -30, 0, 30, 60):
            theta = np.deg2rad(angle)
            x = MAX_RANGE_MM * np.sin(theta)
            y = -MAX_RANGE_MM * np.cos(theta)
            self.ax.plot([0, x], [0, y], color="#3C705E", linewidth=0.75, alpha=0.5, zorder=2)
            self.ax.text(x * 0.98, y * 0.98, f"{angle:+d}°", color="#82C7AB", fontsize=8, ha="center", va="center", zorder=3)

    def _build_reference_card(self, parent: ttk.Frame) -> None:
        card = ttk.LabelFrame(parent, text="Command Help + Example Procedures", padding=10)
        card.grid(row=2, column=0, sticky="ew", pady=(0, 10))

        ref_text = tk.Text(card, height=14, wrap="word")
        ref_text.pack(fill="both", expand=True)
        ref_text.insert(
            "end",
            "Radar profile\n"
            "- 24 GHz ISM FMCW sensor with integrated tracking firmware.\n"
            "- Visualization window maps 6 m max range with +/-60 deg azimuth FOV.\n"
            "- Ring spacing is 1 m; sweep line is a visual aid only.\n"
            "- Coordinate convention: forward is negative Y, right is positive X.\n\n"
            "Commands\n"
            "- Enable Config: Enter configuration mode before changing settings.\n"
            "- End Config: Exit configuration mode and return to normal operation.\n"
            "- Single Target: Track one strongest target.\n"
            "- Multi Target: Track up to three targets.\n"
            "- Query Tracking: Read active tracking mode from sensor.\n"
            "- Read Firmware: Read LD2450 firmware version string.\n"
            "- Get MAC: Read module MAC address string.\n"
            "- Radar Baud + Set: Change LD2450 UART baud rate.\n"
            "- Bluetooth On + Apply: Enable/disable sensor Bluetooth interface.\n"
            "- Restart: Reboot LD2450 module.\n"
            "- Factory Reset: Restore default parameters.\n"
            "- Zone Query: Read zone filter mode + region coordinates.\n"
            "- Zone Apply: Write current zone mode + coordinates.\n\n"
            "Example Procedures\n"
            "1) Basic live tracking\n"
            "   a. Connect COM port.\n"
            "   b. Enable Config.\n"
            "   c. Multi Target.\n"
            "   d. End Config.\n"
            "   e. Observe Targets table and Live Plot.\n\n"
            "2) Create an inclusion zone\n"
            "   a. Enable Config.\n"
            "   b. Set Mode=1.\n"
            "   c. Fill R1 x1/y1/x2/y2.\n"
            "   d. Apply zone config.\n"
            "   e. End Config.\n\n"
            "3) Recover from wrong config\n"
            "   a. Enable Config.\n"
            "   b. Factory Reset.\n"
            "   c. Restart.\n"
            "   d. Reconnect and set desired mode again.\n"
        )
        ref_text.configure(state="disabled")

    def _build_log(self, parent: ttk.Frame) -> None:
        log_card = ttk.LabelFrame(parent, text="Status", padding=8)
        log_card.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        self.log_widget = tk.Text(log_card, height=8, wrap="word")
        self.log_widget.pack(fill="both", expand=True)
        self.log_widget.configure(state="disabled")

    def refresh_ports(self) -> None:
        ports = [p.device for p in list_ports.comports()]
        self.port_combo["values"] = ports
        if ports and self.port_var.get() not in ports:
            self.port_var.set(ports[0])

    def connect(self) -> None:
        if self.controller is not None and self.controller.connected:
            self._log("Already connected")
            return
        port = self.port_var.get().strip()
        if not port:
            messagebox.showerror("No Port", "Select a serial port first.")
            return

        try:
            self.controller = LD2450Controller(port=port, baudrate=self.host_baud_var.get())
            self.controller.connect()
            self._log(f"Connected to {port} @ {self.host_baud_var.get()}")
        except Exception as exc:
            self.controller = None
            messagebox.showerror("Connect Failed", str(exc))

    def disconnect(self) -> None:
        if self.controller is None:
            return
        self.controller.disconnect()
        self._log("Disconnected")

    def _run_cmd(self, label: str, fn) -> None:
        if self.controller is None or not self.controller.connected:
            self._log(f"{label}: not connected")
            return

        def worker() -> None:
            try:
                result = fn()
                self.root.after(0, lambda: self._log(f"{label}: {result}"))
            except Exception as exc:
                self.root.after(0, lambda: self._log(f"{label} failed: {exc}"))

        threading.Thread(target=worker, daemon=True).start()

    def _cmd_enable_cfg(self) -> str:
        assert self.controller is not None
        ok = self.controller.enable_configuration_mode()
        return "OK" if ok else "NACK"

    def _cmd_end_cfg(self) -> str:
        assert self.controller is not None
        ok = self.controller.end_configuration_mode()
        return "OK" if ok else "NACK"

    def _cmd_single_target(self) -> str:
        assert self.controller is not None
        ok = self.controller.single_target_tracking()
        return "OK" if ok else "NACK"

    def _cmd_multi_target(self) -> str:
        assert self.controller is not None
        ok = self.controller.multi_target_tracking()
        return "OK" if ok else "NACK"

    def _cmd_query_tracking(self) -> str:
        assert self.controller is not None
        mode = self.controller.query_target_tracking()
        label = "Single" if mode == 1 else "Multi" if mode == 2 else f"Unknown ({mode})"
        self.root.after(0, lambda: self.track_mode_var.set(label))
        return label

    def _cmd_read_fw(self) -> str:
        assert self.controller is not None
        version = self.controller.read_firmware_version()
        self.root.after(0, lambda: self.fw_var.set(version))
        return version

    def _cmd_set_radar_baud(self) -> str:
        assert self.controller is not None
        baud = int(self.radar_baud_var.get())
        ok = self.controller.set_serial_port_baud_rate(baud)
        msg = "OK" if ok else "NACK"
        if ok:
            msg += " (warning: ESP UART2 is fixed at 256000 in sketch)"
        return msg

    def _cmd_restart(self) -> str:
        assert self.controller is not None
        ok = self.controller.restart_module()
        return "OK" if ok else "NACK"

    def _cmd_bluetooth(self) -> str:
        assert self.controller is not None
        ok = self.controller.bluetooth_setup(self.bluetooth_var.get())
        return "OK" if ok else "NACK"

    def _cmd_get_mac(self) -> str:
        assert self.controller is not None
        mac = self.controller.get_mac_address()
        self.root.after(0, lambda: self.mac_var.set(mac if mac else "-"))
        return mac if mac else "(empty)"

    def _cmd_query_zone(self) -> str:
        assert self.controller is not None
        cfg = self.controller.query_zone_filtering()
        self.root.after(0, lambda: self._apply_zone_cfg_to_ui(cfg))
        return f"mode={cfg.mode}"

    def _cmd_apply_zone(self) -> str:
        assert self.controller is not None
        cfg = self._zone_cfg_from_ui()
        ok = self.controller.set_zone_filtering(cfg)
        return "OK" if ok else "NACK"

    def _factory_reset_prompt(self) -> None:
        if messagebox.askyesno("Factory Reset", "Restore factory settings on LD2450?"):
            self._run_cmd("Factory reset", self._cmd_factory_reset)

    def _cmd_factory_reset(self) -> str:
        assert self.controller is not None
        ok = self.controller.restore_factory_settings()
        return "OK" if ok else "NACK"

    def _zone_cfg_from_ui(self) -> ZoneFilteringConfig:
        values = []
        for ent in self.zone_entries:
            txt = ent.get().strip() or "0"
            values.append(int(txt))

        return ZoneFilteringConfig(
            mode=int(self.zone_mode_var.get()),
            region1=(values[0], values[1], values[2], values[3]),
            region2=(values[4], values[5], values[6], values[7]),
            region3=(values[8], values[9], values[10], values[11]),
        )

    def _apply_zone_cfg_to_ui(self, cfg: ZoneFilteringConfig) -> None:
        self.zone_mode_var.set(str(cfg.mode))
        values = [*cfg.region1, *cfg.region2, *cfg.region3]
        for entry, value in zip(self.zone_entries, values):
            entry.delete(0, tk.END)
            entry.insert(0, str(value))

    def _schedule_update(self) -> None:
        self._update_visuals()
        self.root.after(50, self._schedule_update)

    def _update_visuals(self) -> None:
        if self.controller is None or not self.controller.connected:
            return

        frame = None
        while True:
            item = self.controller.pop_frame()
            if item is None:
                break
            frame = item

        if frame is None:
            return

        points = []
        colors = []
        labels = []
        for i, target in enumerate(frame.targets):
            vars_for_target = self.target_vars[i]
            vars_for_target["x"].set(str(target.x_mm))
            vars_for_target["y"].set(str(target.y_mm))
            vars_for_target["speed"].set(str(target.speed_cms))
            vars_for_target["dist"].set(str(target.distance_resolution_mm))
            vars_for_target["active"].set("Yes" if target.active else "No")

            if target.active:
                points.append((target.x_mm, target.y_mm))
                colors.append(TARGET_COLORS[i])
                range_m = (target.x_mm ** 2 + target.y_mm ** 2) ** 0.5 / 1000.0
                azimuth_deg = np.rad2deg(np.arctan2(target.x_mm, -target.y_mm))
                labels.append(f"T{i+1}  {range_m:.2f} m  {azimuth_deg:+.0f} deg  {target.speed_cms} cm/s")
                self.target_trails[i].append((target.x_mm, target.y_mm))
                if len(self.target_trails[i]) > TRAIL_HISTORY:
                    self.target_trails[i].pop(0)
            else:
                self.target_trails[i].clear()

        self.scatter.set_offsets(np.array(points, dtype=float) if points else np.empty((0, 2)))
        self.scatter.set_color(colors if colors else ["#0A84FF"])

        for i, line in enumerate(self.trail_artists):
            trail = self.target_trails[i]
            if trail:
                arr = np.array(trail, dtype=float)
                line.set_data(arr[:, 0], arr[:, 1])
            else:
                line.set_data([], [])

        for a in self.ann:
            a.remove()
        self.ann.clear()

        for (x, y), text in zip(points, labels):
            self.ann.append(
                self.ax.text(
                    x + 70,
                    y + 70,
                    text,
                    fontsize=8.5,
                    color="#E5FFF2",
                    bbox={"facecolor": "#11262D", "edgecolor": "#3C705E", "alpha": 0.7, "boxstyle": "round,pad=0.22"},
                    zorder=9,
                )
            )

        t = time.time()
        phase = ((t * 40.0) % (2 * AZIMUTH_LIMIT_DEG)) - AZIMUTH_LIMIT_DEG
        sweep_theta = np.deg2rad(phase)
        sx = MAX_RANGE_MM * np.sin(sweep_theta)
        sy = -MAX_RANGE_MM * np.cos(sweep_theta)
        self.sweep_line.set_data([0, sx], [0, sy])

        self.canvas.draw_idle()

    def _log(self, msg: str) -> None:
        self.log_widget.configure(state="normal")
        self.log_widget.insert("end", msg + "\n")
        self.log_widget.see("end")
        self.log_widget.configure(state="disabled")

    def shutdown(self) -> None:
        if self.controller is not None:
            self.controller.disconnect()


def main() -> None:
    root = tk.Tk()
    app = LD2450App(root)

    def on_close() -> None:
        app.shutdown()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
