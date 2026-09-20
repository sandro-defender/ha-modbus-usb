"""Unit tests for diagnostic CRC math and request-frame reconstruction."""

from __future__ import annotations

import pytest

from custom_components.modbus_usb.diagnostics import (
    diagnostic_request_frame,
    modbus_crc16,
)

pytestmark = pytest.mark.fast


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


@pytest.mark.asyncio
async def test_export_activity_log_formats_and_redaction() -> None:
    """Verify JSON, CSV, and text export with filtering and redaction."""
    from unittest.mock import Mock

    from custom_components.modbus_usb.api.diagnostics import ws_export_activity_log
    from custom_components.modbus_usb.const import DOMAIN

    coordinator = Mock()
    coordinator.transaction_log = [
        {
            "timestamp": "2026-09-20T12:00:00Z",
            "operation": "read_holding",
            "slave": 1,
            "address": 100,
            "count": 1,
            "value": None,
            "result": 42,
            "status": "ok",
            "function_code": "0x03",
            "request_hex": "01 03 00 64 00 01 C5 D5",
            "duration_ms": 15.2,
            "retries_configured": 0,
        },
        {
            "timestamp": "2026-09-20T12:01:00Z",
            "operation": "write_coil",
            "slave": 2,
            "address": 5,
            "count": 1,
            "value": True,
            "result": "accepted",
            "status": "ok",
            "function_code": "0x05",
            "request_hex": "02 05 00 05 FF 00 9C 09",
            "duration_ms": 12.0,
            "retries_configured": 0,
        },
        {
            "timestamp": "2026-09-20T12:02:00Z",
            "operation": "read_input",
            "slave": 1,
            "address": 70,
            "count": 2,
            "value": None,
            "result": None,
            "status": "error",
            "error": "Modbus Timeout",
            "function_code": "0x04",
            "request_hex": "01 04 00 46 00 02 91 DF",
            "duration_ms": 3000.0,
            "retries_configured": 0,
        },
    ]

    hass = Mock()
    hass.data = {DOMAIN: {"test_hub": coordinator}}
    connection = Mock()
    connection.send_result = Mock()

    # 1. JSON export with redaction
    await ws_export_activity_log.__wrapped__(
        hass,
        connection,
        {
            "id": 1,
            "type": "modbus_usb/export_activity_log",
            "entry_id": "test_hub",
            "format": "json",
            "redact": True,
        },
    )
    result1 = connection.send_result.call_args[0][1]
    assert result1["format"] == "json"
    assert result1["count"] == 3
    assert "[REDACTED_ADDR]" in result1["data"]
    assert "XX XX" in result1["data"]

    # 2. Filter by slave_id
    await ws_export_activity_log.__wrapped__(
        hass,
        connection,
        {
            "id": 2,
            "type": "modbus_usb/export_activity_log",
            "entry_id": "test_hub",
            "format": "csv",
            "slave_id": 2,
        },
    )
    result2 = connection.send_result.call_args[0][1]
    assert result2["format"] == "csv"
    assert result2["count"] == 1
    assert "write_coil" in result2["data"]
    assert "read_holding" not in result2["data"]

    # 3. Filter by error status
    await ws_export_activity_log.__wrapped__(
        hass,
        connection,
        {
            "id": 3,
            "type": "modbus_usb/export_activity_log",
            "entry_id": "test_hub",
            "format": "text",
            "filter": "error",
        },
    )
    result3 = connection.send_result.call_args[0][1]
    assert result3["format"] == "text"
    assert result3["count"] == 1
    assert "ERROR" in result3["data"]
    assert "Modbus Timeout" in result3["data"]
