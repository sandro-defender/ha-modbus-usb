"""Diagnostic helpers: CRC math, reconstructed request frames, log export.

Pure and Home Assistant-free so the activity-log export shape (one CSV row
per captured response frame, redaction rules) stays trivially unit-testable.
"""

from __future__ import annotations

from typing import Any

# Column order of the activity-log CSV export. One row is written per
# captured response frame (``frame_index``/``frame_count``/``response_hex``/
# ``frame_time_ms``); transactions without any captured RX still produce
# exactly one row with empty frame columns.
ACTIVITY_LOG_CSV_FIELDS = [
    "timestamp",
    "status",
    "operation",
    "function_code",
    "request_hex",
    "slave",
    "address",
    "count",
    "value",
    "result",
    "duration_ms",
    "retries_configured",
    "error",
    "frame_index",
    "frame_count",
    "response_hex",
    "frame_time_ms",
]

# Response frames that echo the request carry the register address at
# bytes 2-3, exactly like the request frame; read responses do not.
_ADDRESS_ECHO_FUNCTIONS = frozenset({0x05, 0x06, 0x0F, 0x10})
_REDACTED_ADDRESS_BYTES = ("XX", "XX")


def modbus_crc16(payload: bytes) -> int:
    """Return the standard Modbus RTU CRC16 for a request payload."""
    crc = 0xFFFF
    for byte in payload:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def diagnostic_request_frame(
    operation: str, slave: int, address: int, count: int, value: Any
) -> tuple[str | None, str | None]:
    """Return a reconstructed request frame for the Diagnostics display.

    Pymodbus does not make raw RTU request/response buffers portable across
    its transports.  These bytes are therefore clearly labelled as a
    reconstructed request; response bytes are never invented.
    """
    operations = {
        "read_coil": 0x01,
        "read_discrete": 0x02,
        "read_holding": 0x03,
        "read_input": 0x04,
        "write_coil": 0x05,
        "write_holding": 0x06,
    }
    function_code = operations.get(operation)
    if function_code is None:
        return None, None
    if function_code in {0x01, 0x02, 0x03, 0x04}:
        payload = bytes(
            (
                slave,
                function_code,
                address >> 8,
                address & 0xFF,
                count >> 8,
                count & 0xFF,
            )
        )
    elif function_code == 0x05:
        coil_value = 0xFF00 if bool(value) else 0x0000
        payload = bytes(
            (
                slave,
                function_code,
                address >> 8,
                address & 0xFF,
                coil_value >> 8,
                coil_value & 0xFF,
            )
        )
    else:
        try:
            register_value = int(value) & 0xFFFF
        except (TypeError, ValueError):
            return f"0x{function_code:02X}", None
        payload = bytes(
            (
                slave,
                function_code,
                address >> 8,
                address & 0xFF,
                register_value >> 8,
                register_value & 0xFF,
            )
        )
    crc = modbus_crc16(payload)
    frame = payload + bytes((crc & 0xFF, crc >> 8))
    return f"0x{function_code:02X}", " ".join(f"{byte:02X}" for byte in frame)


def redact_frame_address(frame_hex: str | None) -> str | None:
    """Mask the address bytes (offsets 2-3) of a space-separated hex frame."""
    if not frame_hex:
        return frame_hex
    parts = str(frame_hex).split()
    if len(parts) < 4:
        return frame_hex
    parts[2], parts[3] = _REDACTED_ADDRESS_BYTES
    return " ".join(parts)


def _frame_function_code(frame_hex: str) -> int | None:
    parts = str(frame_hex).split()
    if len(parts) < 2:
        return None
    try:
        return int(parts[1], 16)
    except ValueError:
        return None


def redact_response_frame(frame_hex: str) -> str:
    """Redact one captured response frame like the request frame.

    Write echoes (FC05/FC06/FC0F/FC10) repeat the register address at bytes
    2-3, so those are masked; read responses and exception frames carry no
    address and are returned unchanged.
    """
    function_code = _frame_function_code(frame_hex)
    if function_code in _ADDRESS_ECHO_FUNCTIONS:
        return redact_frame_address(frame_hex) or frame_hex
    return frame_hex


def transaction_response_frames(item: dict[str, Any]) -> list[str]:
    """Return the captured RX frames of one log entry, oldest first.

    ``response_frames`` (v2.7.0+) wins; pre-v2.7.0 entries that only carry
    ``response_hex`` yield a one-item list; entries without any captured RX
    yield an empty list.
    """
    frames = item.get("response_frames")
    if isinstance(frames, (list, tuple)) and frames:
        return [str(frame) for frame in frames if frame]
    single = item.get("response_hex")
    return [str(single)] if single else []


def transaction_frame_times(item: dict[str, Any], frame_count: int) -> list[Any]:
    """Return per-frame arrival offsets aligned with the frame list.

    Entries whose ``response_frame_times_ms`` is missing or does not match
    the frame count produce empty cells rather than misaligned timings.
    """
    times = item.get("response_frame_times_ms")
    if isinstance(times, (list, tuple)) and len(times) == frame_count:
        return list(times)
    return [""] * frame_count


def activity_log_csv_rows(transactions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten log entries into CSV rows: one row per captured response frame.

    Transaction-level columns repeat on every row of the same transaction;
    ``frame_index`` is 1-based, ``frame_count`` is the number of captured
    frames (0 when nothing was captured, in which case a single row with
    empty frame columns is emitted so every transaction still appears).
    """
    rows: list[dict[str, Any]] = []
    for item in transactions:
        base = {
            field: item.get(field)
            for field in ACTIVITY_LOG_CSV_FIELDS
            if field
            not in ("frame_index", "frame_count", "response_hex", "frame_time_ms")
        }
        frames = transaction_response_frames(item)
        if not frames:
            rows.append(
                {
                    **base,
                    "frame_index": "",
                    "frame_count": 0,
                    "response_hex": "",
                    "frame_time_ms": "",
                }
            )
            continue
        times = transaction_frame_times(item, len(frames))
        for index, frame_hex in enumerate(frames):
            rows.append(
                {
                    **base,
                    "frame_index": index + 1,
                    "frame_count": len(frames),
                    "response_hex": frame_hex,
                    "frame_time_ms": times[index],
                }
            )
    return rows
