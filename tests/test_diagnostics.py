"""Unit tests for diagnostic CRC math and request-frame reconstruction."""

from __future__ import annotations

from custom_components.modbus_usb.diagnostics import (
    diagnostic_request_frame,
    modbus_crc16,
)


def test_modbus_crc16_known_vectors() -> None:
    # Canonical textbook vector: 01 03 00 00 00 01 + CRC 84 0A (low byte first).
    assert modbus_crc16(bytes.fromhex("010300000001")) == 0x0A84
    assert modbus_crc16(bytes.fromhex("010600800001")) == 0xE249


def test_diagnostic_request_frame_read_holding() -> None:
    # Textbook example: slave 1, FC03, address 0, count 1.
    function_code, frame = diagnostic_request_frame("read_holding", 1, 0, 1, None)
    assert function_code == "0x03"
    assert frame == "01 03 00 00 00 01 84 0A"


def test_diagnostic_request_frame_write_coil() -> None:
    function_code, frame = diagnostic_request_frame("write_coil", 1, 0, 1, True)
    assert function_code == "0x05"
    assert frame is not None and frame.startswith("01 05 00 00 FF 00")


def test_diagnostic_request_frame_unknown_operation() -> None:
    assert diagnostic_request_frame("scan_found", 1, 0, 1, None) == (None, None)


def test_diagnostic_request_frame_garbage_write_value() -> None:
    function_code, frame = diagnostic_request_frame(
        "write_holding", 1, 0, 1, "not-a-number"
    )
    assert function_code == "0x06"
    assert frame is None
