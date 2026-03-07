from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


REPORT_HEADER = bytes.fromhex("AA FF 03 00")
REPORT_TAIL = bytes.fromhex("55 CC")
REPORT_FRAME_SIZE = 30


def decode_signed15(raw: int) -> int:
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


def parse_report_frame(frame: bytes) -> Optional[RadarFrame]:
    if len(frame) != REPORT_FRAME_SIZE:
        return None
    if not frame.startswith(REPORT_HEADER) or not frame.endswith(REPORT_TAIL):
        return None

    targets: list[Target] = []
    for offset in (4, 12, 20):
        raw_x = int.from_bytes(frame[offset : offset + 2], "little", signed=False)
        raw_y = int.from_bytes(frame[offset + 2 : offset + 4], "little", signed=False)
        raw_speed = int.from_bytes(frame[offset + 4 : offset + 6], "little", signed=False)
        distance_resolution = int.from_bytes(frame[offset + 6 : offset + 8], "little", signed=False)
        targets.append(
            Target(
                x_mm=decode_signed15(raw_x),
                y_mm=decode_signed15(raw_y),
                speed_cms=decode_signed15(raw_speed),
                distance_resolution_mm=distance_resolution,
            )
        )

    return RadarFrame(raw=frame, targets=(targets[0], targets[1], targets[2]))
