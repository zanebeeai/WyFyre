from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from queue import Empty, Queue
from typing import Optional

import serial
from serial.tools import list_ports


@dataclass(frozen=True)
class SerialPacket:
    node_id: str
    data: dict
    raw_line: str = ""
    parsed: bool = True


class SerialNodeReceiver:
    def __init__(self, ports: dict[str, str], baud: int = 115200) -> None:
        self.ports = ports
        self.baud = baud
        self._running = threading.Event()
        self._threads: list[threading.Thread] = []
        self._queue: Queue[SerialPacket] = Queue(maxsize=4000)
        self._serial_by_node: dict[str, serial.Serial] = {}

    def start(self) -> None:
        if self._threads:
            return
        self._running.set()
        for node_id, port in self.ports.items():
            t = threading.Thread(target=self._reader_loop, args=(node_id, port), daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        self._running.clear()
        for t in self._threads:
            t.join(timeout=1.0)
        self._threads.clear()
        for ser in self._serial_by_node.values():
            try:
                ser.close()
            except serial.SerialException:
                pass
        self._serial_by_node.clear()

    def _reader_loop(self, node_id: str, port: str) -> None:
        active_port = port
        while self._running.is_set():
            try:
                if active_port.strip().upper() == "AUTO":
                    ser, resolved_port = self._open_auto_port(node_id)
                    if ser is None:
                        time.sleep(0.7)
                        continue
                    active_port = resolved_port
                else:
                    ser = serial.Serial(active_port, self.baud, timeout=0.2)

                self._serial_by_node[node_id] = ser
                while self._running.is_set():
                    line = ser.readline()
                    if not line:
                        continue
                    text = line.decode("utf-8", errors="replace").strip()
                    if not text:
                        continue
                    try:
                        payload = json.loads(text)
                        if isinstance(payload, dict):
                            packet = SerialPacket(node_id=node_id, data=payload, raw_line=text, parsed=True)
                        else:
                            packet = SerialPacket(
                                node_id=node_id,
                                data={"msg": "raw_serial", "node_id": node_id, "raw_line": text},
                                raw_line=text,
                                parsed=False,
                            )
                    except json.JSONDecodeError:
                        payload = {"msg": "raw_serial", "node_id": node_id, "raw_line": text}
                        packet = SerialPacket(node_id=node_id, data=payload, raw_line=text, parsed=False)

                    if self._queue.full():
                        try:
                            self._queue.get_nowait()
                        except Empty:
                            pass
                    self._queue.put(packet)
            except serial.SerialException:
                if node_id in self._serial_by_node:
                    self._serial_by_node.pop(node_id, None)
                if port.strip().upper() == "AUTO":
                    active_port = "AUTO"
                time.sleep(0.7)

    def _open_auto_port(self, node_id: str) -> tuple[Optional[serial.Serial], str]:
        ports = [p.device for p in list_ports.comports()]
        for candidate in ports:
            try:
                ser = serial.Serial(candidate, self.baud, timeout=0.2)
            except serial.SerialException:
                continue

            start = time.time()
            matched = False
            while (time.time() - start) < 1.2:
                line = ser.readline()
                if not line:
                    continue
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError:
                    continue

                if not isinstance(payload, dict):
                    continue

                if payload.get("node_id") == node_id:
                    if self._queue.full():
                        try:
                            self._queue.get_nowait()
                        except Empty:
                            pass
                    self._queue.put(
                        SerialPacket(
                            node_id=node_id,
                            data=payload,
                            raw_line=json.dumps(payload, separators=(",", ":")),
                            parsed=True,
                        )
                    )
                    matched = True
                    break

            if matched:
                return ser, candidate

            ser.close()

        return None, "AUTO"

    def pop(self) -> Optional[SerialPacket]:
        try:
            return self._queue.get_nowait()
        except Empty:
            return None

    def send_command(self, node_id: str, command: dict) -> bool:
        ser = self._serial_by_node.get(node_id)
        if ser is None:
            return False
        data = json.dumps(command, separators=(",", ":")) + "\n"
        ser.write(data.encode("utf-8"))
        return True
