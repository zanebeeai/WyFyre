from __future__ import annotations

import asyncio
import base64
import json
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

from config_loader import ConfigBundle
from cv_feedback import CvFeedbackAdapter
from fusion import FusionEngine
from models import RawDetection
from transport_serial import SerialNodeReceiver
from transport_udp import UdpNodeReceiver


@dataclass
class ClientState:
    ws: WebSocket
    client_id: str
    role: str = "slave"
    last_pose: dict | None = None


class WebsocketHub:
    def __init__(self) -> None:
        self.clients: dict[WebSocket, ClientState] = {}
        self.master_ws: WebSocket | None = None
        self.master_pose: dict | None = None

    async def connect(self, ws: WebSocket) -> ClientState:
        await ws.accept()
        state = ClientState(ws=ws, client_id=f"c_{uuid.uuid4().hex[:8]}")
        self.clients[ws] = state
        await ws.send_text(
            json.dumps(
                {
                    "type": "welcome",
                    "client_id": state.client_id,
                    "master_present": self.master_ws in self.clients,
                },
                separators=(",", ":"),
            )
        )
        return state

    def disconnect(self, ws: WebSocket) -> None:
        if ws == self.master_ws:
            self.master_ws = None
            self.master_pose = None
        self.clients.pop(ws, None)

    async def handle_message(self, ws: WebSocket, raw_text: str) -> None:
        state = self.clients.get(ws)
        if state is None:
            return
        try:
            msg = json.loads(raw_text)
        except json.JSONDecodeError:
            return

        msg_type = msg.get("type")
        if msg_type == "register":
            desired = str(msg.get("role", "slave")).lower()
            if desired == "master":
                if self.master_ws is not None and self.master_ws in self.clients and self.master_ws != ws:
                    state.role = "slave"
                    await ws.send_text(
                        json.dumps(
                            {
                                "type": "role_denied",
                                "reason": "master_taken",
                                "role_assigned": "slave",
                                "master_client_id": self.clients[self.master_ws].client_id,
                            },
                            separators=(",", ":"),
                        )
                    )
                else:
                    self.master_ws = ws
                    self.master_pose = None
                    state.role = "master"
                    await ws.send_text(
                        json.dumps(
                            {
                                "type": "registered",
                                "role_assigned": "master",
                                "client_id": state.client_id,
                            },
                            separators=(",", ":"),
                        )
                    )
            else:
                state.role = "slave"
                await ws.send_text(
                    json.dumps(
                        {
                            "type": "registered",
                            "role_assigned": "slave",
                            "client_id": state.client_id,
                            "master_present": self.master_ws in self.clients,
                        },
                        separators=(",", ":"),
                    )
                )
            return

        if msg_type == "pose":
            pose = msg.get("pose")
            if not isinstance(pose, dict):
                return
            state.last_pose = pose
            if state.role == "master" and ws == self.master_ws:
                self.master_pose = pose
            return

        if msg_type == "release_master" and ws == self.master_ws:
            self.master_ws = None
            self.master_pose = None
            state.role = "slave"

    async def broadcast_fusion(self, frame: dict) -> None:
        if not self.clients:
            return

        master_present = self.master_ws in self.clients
        master_client_id = self.clients[self.master_ws].client_id if master_present and self.master_ws is not None else None
        dead: list[WebSocket] = []

        for ws, state in self.clients.items():
            payload = dict(frame)
            payload["you"] = {
                "client_id": state.client_id,
                "role": state.role,
            }
            payload["master"] = {
                "present": master_present,
                "client_id": master_client_id,
                "pose": self.master_pose,
            }
            try:
                await ws.send_text(json.dumps(payload, separators=(",", ":")))
            except Exception:
                dead.append(ws)

        for ws in dead:
            self.disconnect(ws)


class RadarFusionBridge:
    def __init__(self, project_root: Path, hub: WebsocketHub) -> None:
        self.project_root = project_root
        self.hub = hub
        self.config = ConfigBundle(project_root)
        self.fusion = FusionEngine(self.config, cv_feedback=CvFeedbackAdapter())

        self.raw_buffer: list[RawDetection] = []
        self.max_buffer_age_ms = 350
        self.mode = self.config.runtime["app"].get("default_mode", "multi")
        self.refresh_ms = int(self.config.runtime["app"].get("refresh_ms", 80))
        self.last_frame: dict | None = None

        self.node_last_seen: dict[str, int] = {}
        self.remote_link_ms: int | None = None
        self.remote_drop_count = 0
        self.remote_rx_count = 0
        self.remote_sensor_frame_mask: int | None = None
        self.remote_sensor_active_mask: int | None = None
        self.local_sensor_frame_mask: int | None = None
        self.local_sensor_active_mask: int | None = None
        self.sensor_active_counts: dict[str, int] = {f"S{i}": 0 for i in range(5)}

        receiver_mode = self.config.runtime["transport"]["mode"]
        self.receiver_mode = receiver_mode
        if receiver_mode == "serial":
            serial_cfg = self.config.runtime["transport"]["serial_fallback"]
            self.receiver = SerialNodeReceiver(ports=serial_cfg["ports"], baud=int(serial_cfg["baud"]))
        else:
            self.receiver = UdpNodeReceiver(
                bind_host=self.config.runtime["transport"]["udp_bind_host"],
                bind_port=int(self.config.runtime["transport"]["udp_data_port"]),
            )

        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        self.receiver.start()
        self._running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self.receiver.stop()

    async def _loop(self) -> None:
        while self._running:
            self._drain_packets()
            now_ms = int(time.time() * 1000)
            self.raw_buffer = [d for d in self.raw_buffer if (now_ms - d.timestamp_ms) <= self.max_buffer_age_ms]
            result = self.fusion.process(self.raw_buffer, now_ms, self.mode)
            frame = self._build_frame(result, now_ms)
            self.last_frame = frame
            await self.hub.broadcast_fusion(frame)
            await asyncio.sleep(self.refresh_ms / 1000.0)

    def _drain_packets(self) -> None:
        while True:
            pkt = self.receiver.pop()
            if pkt is None:
                return

            data = pkt.data
            msg = data.get("msg")
            node_id = data.get("node_id", "?")

            if msg != "detections":
                continue

            self.node_last_seen[node_id] = int(time.time() * 1000)
            receipt_ms = int(time.time() * 1000)
            per_sensor_counts = {f"S{i}": 0 for i in range(5)}

            if node_id == "A":
                if "remote_link_ms" in data:
                    self.remote_link_ms = int(data.get("remote_link_ms", 0))
                self.remote_drop_count = int(data.get("remote_drop_count", self.remote_drop_count))
                self.remote_rx_count = int(data.get("remote_rx_count", self.remote_rx_count))
                if "local_sensor_frame_mask" in data:
                    self.local_sensor_frame_mask = int(data.get("local_sensor_frame_mask", 0))
                if "local_sensor_active_mask" in data:
                    self.local_sensor_active_mask = int(data.get("local_sensor_active_mask", 0))
                if "remote_sensor_frame_mask" in data:
                    self.remote_sensor_frame_mask = int(data.get("remote_sensor_frame_mask", 0))
                if "remote_sensor_active_mask" in data:
                    self.remote_sensor_active_mask = int(data.get("remote_sensor_active_mask", 0))

            for det in data.get("detections", []):
                try:
                    sid = str(det.get("sensor_id"))
                    is_active = bool(det.get("active", False))
                    if sid in per_sensor_counts and is_active:
                        per_sensor_counts[sid] += 1
                    self.raw_buffer.append(
                        RawDetection(
                            node_id=node_id,
                            sensor_id=sid,
                            sensor_index=int(det.get("sensor_index", -1)),
                            timestamp_ms=receipt_ms,
                            target_id=int(det.get("target_id", -1)),
                            x_mm=int(det.get("x_mm", 0)),
                            y_mm=int(det.get("y_mm", 0)),
                            speed_cms=int(det.get("speed_cms", 0)),
                            distance_resolution_mm=int(det.get("distance_resolution_mm", 0)),
                            active=is_active,
                        )
                    )
                except Exception:
                    continue

            self.sensor_active_counts = per_sensor_counts

    def _build_frame(self, result, now_ms: int) -> dict:
        heat = np.nan_to_num(result.heatmap, nan=0.0, posinf=0.0, neginf=0.0)
        heat_clipped = np.clip(heat, 0.0, 1.0)
        heat_peak = float(np.max(heat_clipped)) if heat_clipped.size else 0.0
        heat_u8 = np.clip(heat_clipped * 255.0, 0, 255).astype(np.uint8)

        heat_blob = base64.b64encode(heat_u8.tobytes()).decode("ascii")
        fused_targets = [
            {
                "track_id": t.track_id,
                "x_mm": t.x_mm,
                "y_mm": t.y_mm,
                "confidence": t.confidence,
                "speed_cms": t.speed_cms,
                "sensors": t.sensors,
                "member_count": t.member_count,
                "persistence": t.persistence,
            }
            for t in result.fused_targets
        ]
        rejected_near = [
            {
                "x_mm": d.global_x_mm,
                "y_mm": d.global_y_mm,
                "sensor_id": d.raw.sensor_id,
            }
            for d in result.rejected_near_detections
        ]

        expected_nodes = self.config.runtime["transport"].get("expected_nodes", ["A", "B"])
        node_status = []
        for n in expected_nodes:
            last = self.node_last_seen.get(n)
            if last is None:
                node_status.append({"node_id": n, "online": False, "age_ms": None})
            else:
                node_status.append({"node_id": n, "online": True, "age_ms": now_ms - last})

        return {
            "type": "fusion_frame",
            "timestamp_ms": now_ms,
            "receiver_mode": self.receiver_mode,
            "fused_targets": fused_targets,
            "rejected_near": rejected_near,
            "radar": {
                "max_range_mm": self.fusion.max_range_mm,
                "azimuth_half_deg": self.fusion.azimuth_half_deg,
                "min_valid_range_mm": self.fusion.min_valid_range_mm,
            },
            "heatmap": {
                "width": int(heat_u8.shape[1]),
                "height": int(heat_u8.shape[0]),
                "extent": {
                    "x_min_mm": result.heatmap_extent[0],
                    "x_max_mm": result.heatmap_extent[1],
                    "y_min_mm": result.heatmap_extent[2],
                    "y_max_mm": result.heatmap_extent[3],
                },
                "peak": heat_peak,
                "u8_b64": heat_blob,
            },
            "status": {
                "nodes": node_status,
                "remote_link_ms": self.remote_link_ms,
                "remote_rx_count": self.remote_rx_count,
                "remote_drop_count": self.remote_drop_count,
                "local_sensor_frame_mask": self.local_sensor_frame_mask,
                "local_sensor_active_mask": self.local_sensor_active_mask,
                "remote_sensor_frame_mask": self.remote_sensor_frame_mask,
                "remote_sensor_active_mask": self.remote_sensor_active_mask,
                "sensor_active_counts": self.sensor_active_counts,
            },
        }


BASE_DIR = Path(__file__).resolve().parent
hub = WebsocketHub()
bridge = RadarFusionBridge(project_root=BASE_DIR, hub=hub)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await bridge.start()
    try:
        yield
    finally:
        await bridge.stop()


app = FastAPI(lifespan=lifespan)


@app.get("/")
async def root() -> FileResponse:
    return FileResponse(BASE_DIR / "index.html")


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse(
        {
            "ok": True,
            "clients": len(hub.clients),
            "receiver_mode": bridge.receiver_mode,
            "has_frame": bridge.last_frame is not None,
            "master_present": hub.master_ws in hub.clients,
        }
    )


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await hub.connect(ws)
    try:
        if bridge.last_frame is not None:
            await ws.send_text(json.dumps(bridge.last_frame, separators=(",", ":")))
        while True:
            incoming = await ws.receive_text()
            await hub.handle_message(ws, incoming)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        hub.disconnect(ws)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=False)
