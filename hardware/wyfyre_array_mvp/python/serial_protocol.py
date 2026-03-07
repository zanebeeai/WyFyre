from __future__ import annotations

"""
Extended protocol helpers for the array MVP.

This keeps compatibility with the original ld2450_test protocol style while
separating raw frame parsing (ld2450_protocol.py) from node-level transport.
"""

from ld2450_protocol import RadarFrame, Target, parse_report_frame

COMMAND_HEADER = bytes.fromhex("FD FC FB FA")
COMMAND_TAIL = bytes.fromhex("04 03 02 01")


def build_command(command_word: bytes, command_value: bytes = b"") -> bytes:
    if len(command_word) != 2:
        raise ValueError("command_word must be exactly 2 bytes")
    payload_len = 2 + len(command_value)
    return COMMAND_HEADER + payload_len.to_bytes(2, "little", signed=False) + command_word + command_value + COMMAND_TAIL


def command_enable_config() -> bytes:
    return build_command(bytes.fromhex("FF 00"), bytes.fromhex("01 00"))


def command_end_config() -> bytes:
    return build_command(bytes.fromhex("FE 00"))


def command_single_target() -> bytes:
    return build_command(bytes.fromhex("80 00"))


def command_multi_target() -> bytes:
    return build_command(bytes.fromhex("90 00"))


__all__ = [
    "RadarFrame",
    "Target",
    "parse_report_frame",
    "build_command",
    "command_enable_config",
    "command_end_config",
    "command_single_target",
    "command_multi_target",
]
