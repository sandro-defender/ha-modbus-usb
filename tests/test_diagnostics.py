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


# ───────────── v2.7.1: response frames in the activity-log export ─────────────


def test_redact_response_frame_masks_only_address_echoes() -> None:
    from custom_components.modbus_usb.diagnostics import redact_response_frame

    # Write echoes repeat the register address at bytes 2-3 → masked.
    assert redact_response_frame("01 06 00 01 00 02 59 CB") == "01 06 XX XX 00 02 59 CB"
    assert redact_response_frame("02 05 00 05 FF 00 9C 09") == "02 05 XX XX FF 00 9C 09"
    assert redact_response_frame("01 0F 00 13 00 0A 24 09") == "01 0F XX XX 00 0A 24 09"
    assert redact_response_frame("01 10 00 01 00 02 10 08") == "01 10 XX XX 00 02 10 08"
    # Read responses carry byte-count + data, no address → unchanged.
    assert redact_response_frame("01 03 02 00 2A 38 49") == "01 03 02 00 2A 38 49"
    assert redact_response_frame("01 04 04 43 66 66 66 A5 95") == (
        "01 04 04 43 66 66 66 A5 95"
    )
    # Exception frames and short/garbage input are returned untouched.
    assert redact_response_frame("01 83 02 C0 F1") == "01 83 02 C0 F1"
    assert redact_response_frame("01") == "01"
    assert redact_response_frame("zz yy 00 01") == "zz yy 00 01"


def test_transaction_response_frames_handles_every_entry_shape() -> None:
    from custom_components.modbus_usb.diagnostics import (
        transaction_frame_times,
        transaction_response_frames,
    )

    assert transaction_response_frames({}) == []
    assert transaction_response_frames({"response_hex": "01 03 02 00 2A 38 49"}) == [
        "01 03 02 00 2A 38 49"
    ]
    # response_frames wins over the legacy single response_hex.
    assert transaction_response_frames(
        {"response_hex": "B", "response_frames": ["A", "B"]}
    ) == ["A", "B"]
    # Empty list falls back to response_hex; falsy frames are skipped.
    assert transaction_response_frames(
        {"response_frames": [], "response_hex": "X"}
    ) == ["X"]
    assert transaction_response_frames({"response_frames": ["A", "", None]}) == ["A"]

    assert transaction_frame_times({}, 0) == []
    assert transaction_frame_times({"response_frame_times_ms": [1.0, 2.5]}, 2) == [
        1.0,
        2.5,
    ]
    assert transaction_frame_times({"response_frame_times_ms": [1.0]}, 2) == ["", ""]
    assert transaction_frame_times({"response_frame_times_ms": "1.0"}, 1) == [""]


def test_activity_log_csv_rows_one_row_per_frame() -> None:
    from custom_components.modbus_usb.diagnostics import (
        ACTIVITY_LOG_CSV_FIELDS,
        activity_log_csv_rows,
    )

    rows = activity_log_csv_rows(
        [
            {
                "timestamp": "t0",
                "status": "ok",
                "operation": "batch_write",
                "function_code": "0x06",
                "request_hex": "01 06 00 02 00 04 29 CA",
                "slave": 1,
                "address": 1,
                "count": 2,
                "value": None,
                "result": "ok",
                "duration_ms": 35.0,
                "retries_configured": 0,
                "response_hex": "01 06 00 02 00 04 29 CA",
                "response_frames": [
                    "01 06 00 01 00 02 59 CB",
                    "01 06 00 02 00 04 29 CA",
                ],
                "response_frame_times_ms": [15.0, 35.0],
            },
            {
                "timestamp": "t1",
                "status": "error",
                "operation": "read_input",
                "function_code": "0x04",
                "request_hex": "01 04 00 46 00 02 91 DF",
                "slave": 1,
                "address": 70,
                "count": 2,
                "error": "Modbus Timeout",
                "duration_ms": 3000.0,
            },
            {
                "timestamp": "t2",
                "status": "ok",
                "operation": "read_holding",
                "function_code": "0x03",
                "request_hex": "01 03 00 07 00 01 35 CB",
                "slave": 1,
                "address": 7,
                "count": 1,
                "response_hex": "01 03 02 00 2A 38 49",  # pre-v2.7.0 shape
            },
        ]
    )
    assert len(rows) == 4
    assert all(set(row) == set(ACTIVITY_LOG_CSV_FIELDS) for row in rows)

    first, second, timeout, legacy = rows
    assert (first["frame_index"], first["frame_count"]) == (1, 2)
    assert first["response_hex"] == "01 06 00 01 00 02 59 CB"
    assert first["frame_time_ms"] == 15.0
    assert (second["frame_index"], second["frame_count"]) == (2, 2)
    assert second["response_hex"] == "01 06 00 02 00 04 29 CA"
    assert second["frame_time_ms"] == 35.0
    # Transaction-level columns repeat on every frame row.
    assert first["timestamp"] == second["timestamp"] == "t0"
    assert first["request_hex"] == second["request_hex"]
    assert first["duration_ms"] == second["duration_ms"] == 35.0

    # No RX captured: still one row so the transaction is not lost.
    assert timeout["frame_index"] == ""
    assert timeout["frame_count"] == 0
    assert timeout["response_hex"] == ""
    assert timeout["frame_time_ms"] == ""
    assert timeout["error"] == "Modbus Timeout"

    # Legacy single response_hex → one frame row without timing.
    assert (legacy["frame_index"], legacy["frame_count"]) == (1, 1)
    assert legacy["response_hex"] == "01 03 02 00 2A 38 49"
    assert legacy["frame_time_ms"] == ""


def test_activity_log_csv_rows_mismatched_times_leave_cells_empty() -> None:
    from custom_components.modbus_usb.diagnostics import activity_log_csv_rows

    rows = activity_log_csv_rows(
        [
            {
                "timestamp": "t0",
                "response_frames": ["A", "B"],
                "response_frame_times_ms": [1.0],
            }
        ]
    )
    assert [row["frame_time_ms"] for row in rows] == ["", ""]
    assert [row["response_hex"] for row in rows] == ["A", "B"]


async def test_export_activity_log_csv_expands_response_frames() -> None:
    """CSV export emits one row per captured RX frame; JSON keeps the list."""
    import csv
    import io
    import json
    from unittest.mock import Mock

    from custom_components.modbus_usb.api.diagnostics import ws_export_activity_log
    from custom_components.modbus_usb.const import DOMAIN

    coordinator = Mock()
    coordinator.transaction_log = [
        {
            "timestamp": "2026-09-20T12:00:00Z",
            "operation": "batch_write",
            "slave": 1,
            "address": 1,
            "count": 2,
            "value": None,
            "result": "ok",
            "status": "ok",
            "function_code": "0x06",
            "request_hex": "01 06 00 02 00 04 29 CA",
            "response_hex": "01 06 00 02 00 04 29 CA",
            "response_frames": ["01 06 00 01 00 02 59 CB", "01 06 00 02 00 04 29 CA"],
            "response_frame_times_ms": [15.0, 35.0],
            "duration_ms": 35.0,
            "retries_configured": 0,
        },
        {
            "timestamp": "2026-09-20T12:01:00Z",
            "operation": "read_holding",
            "slave": 1,
            "address": 7,
            "count": 1,
            "value": None,
            "result": 42,
            "status": "ok",
            "function_code": "0x03",
            "request_hex": "01 03 00 07 00 01 35 CB",
            "response_hex": "01 03 02 00 2A 38 49",
            "response_frames": ["01 03 02 00 2A 38 49"],
            "response_frame_times_ms": [12.5],
            "duration_ms": 13.0,
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

    # CSV: 2 + 1 + 1 rows for 3 transactions.
    await ws_export_activity_log.__wrapped__(
        hass,
        connection,
        {
            "id": 1,
            "type": "modbus_usb/export_activity_log",
            "entry_id": "test_hub",
            "format": "csv",
        },
    )
    result = connection.send_result.call_args[0][1]
    assert result["count"] == 3
    assert result["rows"] == 4
    parsed = list(csv.DictReader(io.StringIO(result["data"])))
    assert len(parsed) == 4
    assert parsed[0]["frame_index"] == "1"
    assert parsed[0]["frame_count"] == "2"
    assert parsed[0]["response_hex"] == "01 06 00 01 00 02 59 CB"
    assert parsed[0]["frame_time_ms"] == "15.0"
    assert parsed[1]["frame_index"] == "2"
    assert parsed[1]["response_hex"] == "01 06 00 02 00 04 29 CA"
    assert parsed[1]["frame_time_ms"] == "35.0"
    assert parsed[1]["timestamp"] == parsed[0]["timestamp"]
    assert parsed[2]["frame_count"] == "1"
    assert parsed[2]["frame_time_ms"] == "12.5"
    assert parsed[3]["frame_count"] == "0"
    assert parsed[3]["response_hex"] == ""
    assert parsed[3]["error"] == "Modbus Timeout"
    header = result["data"].splitlines()[0]
    assert header.endswith("frame_index,frame_count,response_hex,frame_time_ms")

    # JSON keeps the nested list shape and reports rows == count.
    await ws_export_activity_log.__wrapped__(
        hass,
        connection,
        {
            "id": 2,
            "type": "modbus_usb/export_activity_log",
            "entry_id": "test_hub",
            "format": "json",
        },
    )
    result = connection.send_result.call_args[0][1]
    assert result["rows"] == result["count"] == 3
    data = json.loads(result["data"])
    assert data[0]["response_frames"] == [
        "01 06 00 01 00 02 59 CB",
        "01 06 00 02 00 04 29 CA",
    ]
    assert data[0]["response_frame_times_ms"] == [15.0, 35.0]
    assert "response_frames" not in data[2]

    # Text export: unchanged single line per transaction.
    await ws_export_activity_log.__wrapped__(
        hass,
        connection,
        {
            "id": 3,
            "type": "modbus_usb/export_activity_log",
            "entry_id": "test_hub",
            "format": "text",
        },
    )
    result = connection.send_result.call_args[0][1]
    assert result["rows"] == 3
    assert len(result["data"].splitlines()) == 3


async def test_export_activity_log_redacts_response_frames() -> None:
    import csv
    import io
    import json
    from unittest.mock import Mock

    from custom_components.modbus_usb.api.diagnostics import ws_export_activity_log
    from custom_components.modbus_usb.const import DOMAIN

    coordinator = Mock()
    coordinator.transaction_log = [
        {
            "timestamp": "2026-09-20T12:00:00Z",
            "operation": "batch_write",
            "slave": 1,
            "address": 1,
            "status": "ok",
            "function_code": "0x06",
            "request_hex": "01 06 00 02 00 04 29 CA",
            "response_hex": "01 06 00 02 00 04 29 CA",
            "response_frames": ["01 06 00 01 00 02 59 CB", "01 06 00 02 00 04 29 CA"],
            "response_frame_times_ms": [15.0, 35.0],
        },
        {
            "timestamp": "2026-09-20T12:01:00Z",
            "operation": "read_holding",
            "slave": 1,
            "address": 7,
            "status": "ok",
            "function_code": "0x03",
            "request_hex": "01 03 00 07 00 01 35 CB",
            "response_hex": "01 03 02 00 2A 38 49",
            "response_frames": ["01 03 02 00 2A 38 49"],
        },
    ]
    hass = Mock()
    hass.data = {DOMAIN: {"test_hub": coordinator}}
    connection = Mock()
    connection.send_result = Mock()

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
    data = json.loads(connection.send_result.call_args[0][1]["data"])
    # Write echoes are masked in both the legacy field and the frame list …
    assert data[0]["request_hex"] == "01 06 XX XX 00 04 29 CA"
    assert data[0]["response_hex"] == "01 06 XX XX 00 04 29 CA"
    assert data[0]["response_frames"] == [
        "01 06 XX XX 00 02 59 CB",
        "01 06 XX XX 00 04 29 CA",
    ]
    # … while read responses (no address bytes) stay intact.
    assert data[1]["request_hex"] == "01 03 XX XX 00 01 35 CB"
    assert data[1]["response_hex"] == "01 03 02 00 2A 38 49"
    assert data[1]["response_frames"] == ["01 03 02 00 2A 38 49"]
    # Timings are not sensitive and survive redaction.
    assert data[0]["response_frame_times_ms"] == [15.0, 35.0]

    await ws_export_activity_log.__wrapped__(
        hass,
        connection,
        {
            "id": 2,
            "type": "modbus_usb/export_activity_log",
            "entry_id": "test_hub",
            "format": "csv",
            "redact": True,
        },
    )
    result = connection.send_result.call_args[0][1]
    parsed = list(csv.DictReader(io.StringIO(result["data"])))
    assert [row["response_hex"] for row in parsed] == [
        "01 06 XX XX 00 02 59 CB",
        "01 06 XX XX 00 04 29 CA",
        "01 03 02 00 2A 38 49",
    ]
    assert all(row["address"] == "[REDACTED_ADDR]" for row in parsed)
    # The raw register addresses never leak into the redacted CSV.
    assert "01 06 00 01 00 02" not in result["data"]
    assert "01 06 00 02 00 04" not in result["data"]
