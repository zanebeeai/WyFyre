from __future__ import annotations

from dataclasses import dataclass
from queue import Empty, Queue
import threading
import time
from typing import Optional

import serial


COMMAND_HEADER = bytes.fromhex("FD FC FB FA")
COMMAND_TAIL = bytes.fromhex("04 03 02 01")

REPORT_HEADER = bytes.fromhex("AA FF 03 00")
REPORT_TAIL = bytes.fromhex("55 CC")

REPORT_FRAME_SIZE = 30


def _decode_signed15(raw: int) -> int:
    mag = raw & 0x7FFF
    return -mag if (raw & 0x8000) else mag


@dataclass(frozen=True)
class Target:
    x_mm: int
    y_mm: int
    speed_cms: int
    distance_resolution_mm: int

    @property
    def active(self) -> bool:
        return self.distance_resolution_mm != 0


@dataclass(frozen=True)
class RadarFrame:
    raw: bytes
    targets: tuple[Target, Target, Target]


@dataclass(frozen=True)
class ZoneFilteringConfig:
    mode: int
    region1: tuple[int, int, int, int]
    region2: tuple[int, int, int, int]
    region3: tuple[int, int, int, int]


class LD2450Controller:
    def __init__(self, port: str, baudrate: int = 115200, timeout: float = 0.05) -> None:
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout

        self._ser: Optional[serial.Serial] = None
        self._rx_buffer = bytearray()
        self._thread: Optional[threading.Thread] = None
        self._running = threading.Event()

        self._frames: Queue[RadarFrame] = Queue(maxsize=200)
        self._latest_frame: Optional[RadarFrame] = None
        self._latest_lock = threading.Lock()

        self._cmd_lock = threading.Lock()
        self._pending_cmd_event: Optional[threading.Event] = None
        self._pending_cmd_response: Optional[bytes] = None

    @property
    def connected(self) -> bool:
        return self._ser is not None and self._ser.is_open

    def connect(self) -> None:
        if self.connected:
            return

        self._ser = serial.Serial(self.port, self.baudrate, timeout=self.timeout)
        self._ser.reset_input_buffer()
        self._ser.reset_output_buffer()

        self._running.set()
        self._thread = threading.Thread(target=self._io_loop, daemon=True)
        self._thread.start()

    def disconnect(self) -> None:
        self._running.clear()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None

        if self._ser is not None:
            try:
                self._ser.close()
            finally:
                self._ser = None

    def get_latest_frame(self) -> Optional[RadarFrame]:
        with self._latest_lock:
            return self._latest_frame

    def pop_frame(self) -> Optional[RadarFrame]:
        try:
            frame = self._frames.get_nowait()
        except Empty:
            return None

        with self._latest_lock:
            self._latest_frame = frame
        return frame

    def send_command(self, command_word: bytes, command_value: bytes = b"", timeout: float = 1.5) -> bytes:
        if not self.connected or self._ser is None:
            raise RuntimeError("Serial port is not connected")
        if len(command_word) != 2:
            raise ValueError("command_word must be exactly 2 bytes")

        payload_len = 2 + len(command_value)
        frame = COMMAND_HEADER + payload_len.to_bytes(2, "little", signed=False) + command_word + command_value + COMMAND_TAIL

        with self._cmd_lock:
            done = threading.Event()
            self._pending_cmd_event = done
            self._pending_cmd_response = None
            self._ser.write(frame)
            if not done.wait(timeout):
                self._pending_cmd_event = None
                raise TimeoutError("No command response from LD2450")

            response = self._pending_cmd_response
            self._pending_cmd_event = None
            self._pending_cmd_response = None

        if response is None:
            raise RuntimeError("Command response event set but no response captured")
        return response

    def _io_loop(self) -> None:
        while self._running.is_set() and self._ser is not None:
            try:
                chunk = self._ser.read(self._ser.in_waiting or 1)
                if chunk:
                    self._rx_buffer.extend(chunk)
                    self._drain_rx_buffer()
                else:
                    time.sleep(0.002)
            except serial.SerialException:
                self._running.clear()
                break

    def _drain_rx_buffer(self) -> None:
        while True:
            parsed = self._extract_one_frame()
            if parsed is None:
                return

            frame_kind, frame_bytes = parsed
            if frame_kind == "report":
                report = parse_report_frame(frame_bytes)
                if report is None:
                    continue

                with self._latest_lock:
                    self._latest_frame = report
                if self._frames.full():
                    try:
                        self._frames.get_nowait()
                    except Empty:
                        pass
                self._frames.put(report)
            else:
                if self._pending_cmd_event is not None:
                    self._pending_cmd_response = frame_bytes
                    self._pending_cmd_event.set()

    def _extract_one_frame(self) -> Optional[tuple[str, bytes]]:
        if not self._rx_buffer:
            return None

        report_at = self._rx_buffer.find(REPORT_HEADER)
        cmd_at = self._rx_buffer.find(COMMAND_HEADER)

        starts = [pos for pos in (report_at, cmd_at) if pos >= 0]
        if not starts:
            keep = max(len(REPORT_HEADER), len(COMMAND_HEADER)) - 1
            if len(self._rx_buffer) > keep:
                del self._rx_buffer[:-keep]
            return None

        start = min(starts)
        if start > 0:
            del self._rx_buffer[:start]

        if self._rx_buffer.startswith(REPORT_HEADER):
            if len(self._rx_buffer) < REPORT_FRAME_SIZE:
                return None

            candidate = bytes(self._rx_buffer[:REPORT_FRAME_SIZE])
            if candidate[-2:] == REPORT_TAIL:
                del self._rx_buffer[:REPORT_FRAME_SIZE]
                return ("report", candidate)

            del self._rx_buffer[0]
            return None

        if self._rx_buffer.startswith(COMMAND_HEADER):
            tail_at = self._rx_buffer.find(COMMAND_TAIL, len(COMMAND_HEADER) + 2)
            if tail_at < 0:
                return None

            end = tail_at + len(COMMAND_TAIL)
            candidate = bytes(self._rx_buffer[:end])
            del self._rx_buffer[:end]
            return ("command", candidate)

        del self._rx_buffer[0]
        return None

    @staticmethod
    def _command_success(response: bytes) -> bool:
        if len(response) < 12:
            return False
        return int.from_bytes(response[8:10], byteorder="little", signed=False) == 0

    def enable_configuration_mode(self) -> bool:
        return self._command_success(self.send_command(bytes.fromhex("FF 00"), bytes.fromhex("01 00")))

    def end_configuration_mode(self) -> bool:
        return self._command_success(self.send_command(bytes.fromhex("FE 00")))

    def single_target_tracking(self) -> bool:
        return self._command_success(self.send_command(bytes.fromhex("80 00")))

    def multi_target_tracking(self) -> bool:
        return self._command_success(self.send_command(bytes.fromhex("90 00")))

    def query_target_tracking(self) -> int:
        response = self.send_command(bytes.fromhex("91 00"))
        if not self._command_success(response):
            raise RuntimeError("Query target tracking failed")
        return int.from_bytes(response[10:12], byteorder="little", signed=False)

    def read_firmware_version(self) -> str:
        response = self.send_command(bytes.fromhex("A0 00"))
        if not self._command_success(response):
            raise RuntimeError("Read firmware version failed")

        fw_type = int.from_bytes(response[10:12], byteorder="little", signed=False)
        major = int.from_bytes(response[12:14], byteorder="little", signed=False)
        minor = int.from_bytes(response[14:18], byteorder="little", signed=False)
        return f"V{fw_type}.{major}.{minor}"

    def set_serial_port_baud_rate(self, baud_rate: int = 256000) -> bool:
        possible_baud_rates = [9600, 19200, 38400, 57600, 115200, 230400, 256000, 460800]
        if baud_rate not in possible_baud_rates:
            raise ValueError(f"Unsupported baud rate: {baud_rate}")
        baud_idx = possible_baud_rates.index(baud_rate)
        return self._command_success(self.send_command(bytes.fromhex("A1 00"), baud_idx.to_bytes(2, "little", signed=False)))

    def restore_factory_settings(self) -> bool:
        return self._command_success(self.send_command(bytes.fromhex("A2 00")))

    def restart_module(self) -> bool:
        return self._command_success(self.send_command(bytes.fromhex("A3 00")))

    def bluetooth_setup(self, bluetooth_on: bool = True) -> bool:
        payload = bytes.fromhex("01 00") if bluetooth_on else bytes.fromhex("00 00")
        return self._command_success(self.send_command(bytes.fromhex("A4 00"), payload))

    def get_mac_address(self) -> str:
        response = self.send_command(bytes.fromhex("A5 00"), bytes.fromhex("01 00"))
        if not self._command_success(response):
            raise RuntimeError("Get MAC address failed")

        body = response[10:-4]
        return body.decode("utf-8", errors="ignore").strip("\x00\r\n ")

    def query_zone_filtering(self) -> ZoneFilteringConfig:
        response = self.send_command(bytes.fromhex("C1 00"))
        if not self._command_success(response):
            raise RuntimeError("Query zone filtering failed")

        vals = [int.from_bytes(response[i : i + 2], byteorder="little", signed=True) for i in range(10, 36, 2)]
        if len(vals) != 13:
            raise RuntimeError("Unexpected zone filtering payload")

        return ZoneFilteringConfig(
            mode=vals[0],
            region1=(vals[1], vals[2], vals[3], vals[4]),
            region2=(vals[5], vals[6], vals[7], vals[8]),
            region3=(vals[9], vals[10], vals[11], vals[12]),
        )

    def set_zone_filtering(self, cfg: ZoneFilteringConfig) -> bool:
        values = [
            cfg.mode,
            *cfg.region1,
            *cfg.region2,
            *cfg.region3,
        ]
        payload = b"".join(int(v).to_bytes(2, byteorder="little", signed=True) for v in values)
        return self._command_success(self.send_command(bytes.fromhex("C2 00"), payload))


def parse_report_frame(frame: bytes) -> Optional[RadarFrame]:
    if len(frame) != REPORT_FRAME_SIZE:
        return None
    if not frame.startswith(REPORT_HEADER) or not frame.endswith(REPORT_TAIL):
        return None

    targets: list[Target] = []
    for offset in (4, 12, 20):
        raw_x = int.from_bytes(frame[offset : offset + 2], byteorder="little", signed=False)
        raw_y = int.from_bytes(frame[offset + 2 : offset + 4], byteorder="little", signed=False)
        raw_speed = int.from_bytes(frame[offset + 4 : offset + 6], byteorder="little", signed=False)
        distance_resolution = int.from_bytes(frame[offset + 6 : offset + 8], byteorder="little", signed=False)

        targets.append(
            Target(
                x_mm=_decode_signed15(raw_x),
                y_mm=_decode_signed15(raw_y),
                speed_cms=_decode_signed15(raw_speed),
                distance_resolution_mm=distance_resolution,
            )
        )

    return RadarFrame(raw=frame, targets=(targets[0], targets[1], targets[2]))
