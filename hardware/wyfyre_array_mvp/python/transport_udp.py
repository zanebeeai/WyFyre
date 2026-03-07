from __future__ import annotations

import json
import socket
import threading
from dataclasses import dataclass
from queue import Empty, Queue
from typing import Optional


@dataclass(frozen=True)
class NodePacket:
    data: dict
    source_ip: str
    source_port: int


class UdpNodeReceiver:
    def __init__(self, bind_host: str, bind_port: int) -> None:
        self.bind_host = bind_host
        self.bind_port = bind_port

        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind((bind_host, bind_port))
        self._sock.settimeout(0.2)

        self._running = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._queue: Queue[NodePacket] = Queue(maxsize=4000)
        self._node_addr: dict[str, tuple[str, int]] = {}

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._running.set()
        self._thread = threading.Thread(target=self._rx_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running.clear()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        self._sock.close()

    def _rx_loop(self) -> None:
        while self._running.is_set():
            try:
                payload, addr = self._sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break

            try:
                msg = json.loads(payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue

            node_id = msg.get("node_id")
            if isinstance(node_id, str) and node_id:
                cmd_port = int(msg.get("cmd_port", addr[1]))
                self._node_addr[node_id] = (addr[0], cmd_port)

            if self._queue.full():
                try:
                    self._queue.get_nowait()
                except Empty:
                    pass
            self._queue.put(NodePacket(data=msg, source_ip=addr[0], source_port=addr[1]))

    def pop(self) -> Optional[NodePacket]:
        try:
            return self._queue.get_nowait()
        except Empty:
            return None

    def send_command(self, node_id: str, command: dict) -> bool:
        addr = self._node_addr.get(node_id)
        if addr is None:
            return False

        payload = json.dumps(command, separators=(",", ":")).encode("utf-8")
        self._sock.sendto(payload, addr)
        return True

    @property
    def known_nodes(self) -> dict[str, tuple[str, int]]:
        return dict(self._node_addr)
