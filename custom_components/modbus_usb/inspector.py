"""Live bus traffic inspector and Modbus RTU frame analyzer.

Pure parsing helpers for the Traffic Inspector panel tab. The coordinator
keeps a rolling log of request frames (captured TX bytes when pymodbus
tracing is available, otherwise reconstructed requests) and of real response
frames captured from the wire (see ``capture.py``); these helpers decode
each frame byte-by-byte (slave ID, function code, address, count, payload,
CRC16 low/high), pair request/response per transaction, and assemble the
latency waterfall used by the panel.

The module intentionally avoids Home Assistant imports so it stays trivially
unit-testable.
"""

from __future__ import annotations

import itertools
import statistics
from typing import Any

from .diagnostics import modbus_crc16

FUNCTION_CODE_NAMES = {
    0x01: "Read Coils",
    0x02: "Read Discrete Inputs",
    0x03: "Read Holding Registers",
    0x04: "Read Input Registers",
    0x05: "Write Single Coil",
    0x06: "Write Single Register",
    0x0F: "Write Multiple Coils",
    0x10: "Write Multiple Registers",
}

EXCEPTION_CODE_NAMES = {
    0x01: "Illegal Function",
    0x02: "Illegal Data Address",
    0x03: "Illegal Data Value",
    0x04: "Slave Device Failure",
    0x05: "Acknowledge",
    0x06: "Slave Device Busy",
    0x08: "Memory Parity Error",
    0x0A: "Gateway Path Unavailable",
    0x0B: "Gateway Target Device Failed to Respond",
}

# Waterfall stages recorded by the coordinator around every serial I/O.
LATENCY_STAGE_LABELS = {
    "lock_wait_ms": "Bus lock wait",
    "connect_ms": "Port connect",
    "frame_delay_ms": "Inter-frame delay",
    "request_ms": "Serial request",
}

_HEX_DIGITS = set("0123456789abcdefABCDEF")


def normalize_frame_hex(frame_hex: str | bytes) -> bytes:
    """Accept user-pasted hex in common formats and return raw frame bytes.

    Whitespace, ``0x`` prefixes, commas, dashes, and colons are tolerated.
    Raises ValueError for non-hex characters or an odd number of digits.
    """
    if isinstance(frame_hex, (bytes, bytearray)):
        return bytes(frame_hex)
    if not isinstance(frame_hex, str):
        raise ValueError("Frame must be a hexadecimal string or bytes")
    compact = (
        frame_hex.replace("0x", "")
        .replace("0X", "")
        .replace(",", "")
        .replace("-", "")
        .replace(":", "")
    )
    compact = "".join(compact.split())
    if not compact:
        raise ValueError("Frame is empty")
    if len(compact) % 2:
        raise ValueError("Hexadecimal frame must contain a whole number of bytes")
    if any(char not in _HEX_DIGITS for char in compact):
        raise ValueError("Frame contains non-hexadecimal characters")
    return bytes.fromhex(compact)


def _format_bytes(data: bytes) -> str:
    return " ".join(f"{byte:02X}" for byte in data)


def _read_request_fields(frame: bytes, parsed: dict[str, Any]) -> None:
    parsed["address"] = (frame[2] << 8) | frame[3]
    parsed["count"] = (frame[4] << 8) | frame[5]
    if not 1 <= parsed["count"] <= 0x7D0:
        parsed["errors"].append(
            f"Read count {parsed['count']} is outside the Modbus range 1-2000"
        )


def _parse_single_write(frame: bytes, parsed: dict[str, Any]) -> None:
    parsed["address"] = (frame[2] << 8) | frame[3]
    value = (frame[4] << 8) | frame[5]
    parsed["value"] = value
    if parsed["function_code"] == 0x05:
        parsed["coil_on"] = value == 0xFF00
        if value not in (0x0000, 0xFF00):
            parsed["errors"].append(
                f"FC05 coil value must be FF00 (on) or 0000 (off), got {value:04X}"
            )


def _parse_multi_coil_request(frame: bytes, parsed: dict[str, Any]) -> None:
    parsed["address"] = (frame[2] << 8) | frame[3]
    parsed["count"] = (frame[4] << 8) | frame[5]
    byte_count = frame[6]
    parsed["byte_count"] = byte_count
    expected_bytes = (parsed["count"] + 7) // 8
    if not 1 <= parsed["count"] <= 1968:
        parsed["errors"].append(
            f"Coil count {parsed['count']} is outside the Modbus range 1-1968"
        )
    if byte_count != expected_bytes:
        parsed["errors"].append(
            f"Byte count {byte_count} does not match {parsed['count']} coils "
            f"(expected {expected_bytes})"
        )
    data = frame[7:-2]
    parsed["data_hex"] = _format_bytes(data)
    if len(data) == byte_count:
        parsed["values"] = [
            bool(data[index // 8] & (1 << (index % 8)))
            for index in range(parsed["count"])
        ]
    else:
        parsed["errors"].append(
            f"Frame carries {len(data)} data bytes but declares {byte_count}"
        )


def _parse_multi_register_request(frame: bytes, parsed: dict[str, Any]) -> None:
    parsed["address"] = (frame[2] << 8) | frame[3]
    parsed["count"] = (frame[4] << 8) | frame[5]
    byte_count = frame[6]
    parsed["byte_count"] = byte_count
    if not 1 <= parsed["count"] <= 123:
        parsed["errors"].append(
            f"Register count {parsed['count']} is outside the Modbus range 1-123"
        )
    if byte_count != parsed["count"] * 2:
        parsed["errors"].append(
            f"Byte count {byte_count} does not match {parsed['count']} registers "
            f"(expected {parsed['count'] * 2})"
        )
    data = frame[7:-2]
    parsed["data_hex"] = _format_bytes(data)
    if len(data) == byte_count and byte_count % 2 == 0:
        parsed["values"] = [
            (data[index] << 8) | data[index + 1] for index in range(0, byte_count, 2)
        ]
    else:
        parsed["errors"].append(
            f"Frame carries {len(data)} data bytes but declares {byte_count}"
        )


def _parse_byte_count_response(
    frame: bytes, function_code: int, parsed: dict[str, Any]
) -> None:
    byte_count = frame[2]
    parsed["byte_count"] = byte_count
    data = frame[3:-2]
    parsed["data_hex"] = _format_bytes(data)
    if len(data) != byte_count:
        parsed["errors"].append(
            f"Frame carries {len(data)} data bytes but declares {byte_count}"
        )
        return
    if function_code in (0x01, 0x02):
        parsed["values"] = [
            bool(data[index // 8] & (1 << (index % 8)))
            for index in range(byte_count * 8)
        ]
    else:
        if byte_count % 2:
            parsed["errors"].append(
                f"Register response byte count must be even (got {byte_count})"
            )
            return
        parsed["values"] = [
            (data[index] << 8) | data[index + 1] for index in range(0, byte_count, 2)
        ]


def parse_rtu_frame(
    frame_hex: str | bytes, *, as_response: bool = False
) -> dict[str, Any]:
    """Decode one Modbus RTU frame into a field-by-field breakdown.

    Returns a dictionary with the slave ID, function code, address/count or
    data payload fields, and the CRC16 low/high bytes with pass/fail
    validation. Structural problems are collected in ``errors`` instead of
    raising, so the panel can display a partially decoded frame.

    Note: an 8-byte FC01-FC04 frame is ambiguous (read request vs. a response
    carrying 5 data bytes). Pass ``as_response=True`` for captured RX bytes so
    every FC01-FC04 frame decodes as a byte-count response; by default frames
    decode as the reconstructed requests the integration logs.
    """
    frame = normalize_frame_hex(frame_hex)
    parsed: dict[str, Any] = {
        "raw_hex": _format_bytes(frame),
        "frame_length": len(frame),
        "direction": "response" if as_response else "request",
        "errors": [],
    }

    if len(frame) < 4:
        parsed["errors"].append(
            "A Modbus RTU frame needs at least slave, function, and two CRC bytes"
        )
        parsed["crc_present"] = False
        parsed["crc_valid"] = False
        parsed["valid"] = False
        return parsed

    payload = frame[:-2]
    crc_received = frame[-2] | (frame[-1] << 8)
    crc_expected = modbus_crc16(payload)
    parsed.update(
        {
            "slave_id": frame[0],
            "function_code": frame[1],
            "crc_present": True,
            "crc_low": frame[-2],
            "crc_high": frame[-1],
            "crc_received": crc_received,
            "crc_expected": crc_expected,
            "crc_valid": crc_received == crc_expected,
        }
    )

    slave = frame[0]
    function_code = frame[1]
    if not 0 <= slave <= 247:
        parsed["errors"].append(f"Slave ID {slave} is outside the valid range 0-247")

    if function_code >= 0x80:
        base_code = function_code - 0x80
        parsed["frame_kind"] = "exception_response"
        # Exception frames only ever travel from slave to master.
        parsed["direction"] = "response"
        parsed["function_code"] = function_code
        parsed["base_function_code"] = base_code
        parsed["function_name"] = FUNCTION_CODE_NAMES.get(
            base_code, f"Function 0x{base_code:02X}"
        )
        exception_code = frame[2] if len(frame) >= 5 else None
        parsed["exception_code"] = exception_code
        parsed["exception_name"] = (
            EXCEPTION_CODE_NAMES.get(exception_code, "Unknown exception")
            if exception_code is not None
            else "Truncated exception response"
        )
        if len(frame) != 5:
            parsed["errors"].append(
                f"Exception responses are 5 bytes long, got {len(frame)}"
            )
        parsed["valid"] = parsed["crc_valid"] and not parsed["errors"]
        parsed["summary"] = (
            f"Exception {exception_code:02X} ({parsed['exception_name']}) from "
            f"slave {slave} for {parsed['function_name']}"
        )
        return parsed

    function_name = FUNCTION_CODE_NAMES.get(function_code)
    parsed["function_name"] = function_name or f"Function 0x{function_code:02X}"

    if function_code in (0x01, 0x02, 0x03, 0x04):
        if len(frame) == 8 and not as_response:
            parsed["frame_kind"] = "read_request"
            _read_request_fields(frame, parsed)
        elif len(frame) >= 5:
            parsed["frame_kind"] = "read_response"
            _parse_byte_count_response(frame, function_code, parsed)
        else:
            parsed["frame_kind"] = "unknown"
            parsed["errors"].append("Frame is too short for a read request/response")
    elif function_code in (0x05, 0x06):
        parsed["frame_kind"] = "write_frame"
        if len(frame) == 8:
            _parse_single_write(frame, parsed)
        else:
            parsed["errors"].append(
                f"Single-write frames are 8 bytes long, got {len(frame)}"
            )
    elif function_code == 0x0F:
        if len(frame) == 8:
            parsed["frame_kind"] = "write_response"
            parsed["address"] = (frame[2] << 8) | frame[3]
            parsed["count"] = (frame[4] << 8) | frame[5]
        elif len(frame) >= 9:
            parsed["frame_kind"] = "write_request"
            _parse_multi_coil_request(frame, parsed)
        else:
            parsed["frame_kind"] = "unknown"
            parsed["errors"].append("Frame is too short for an FC0F write")
    elif function_code == 0x10:
        if len(frame) == 8:
            parsed["frame_kind"] = "write_response"
            parsed["address"] = (frame[2] << 8) | frame[3]
            parsed["count"] = (frame[4] << 8) | frame[5]
        elif len(frame) >= 9:
            parsed["frame_kind"] = "write_request"
            _parse_multi_register_request(frame, parsed)
        else:
            parsed["frame_kind"] = "unknown"
            parsed["errors"].append("Frame is too short for an FC10 write")
    else:
        parsed["frame_kind"] = "unknown"
        parsed["errors"].append(
            f"Function code 0x{function_code:02X} is not a standard Modbus RTU function"
        )

    if not parsed["crc_valid"]:
        parsed["errors"].append(
            f"CRC mismatch: frame carries {crc_received:04X}, "
            f"computed {crc_expected:04X}"
        )

    parsed["valid"] = parsed["crc_valid"] and not parsed["errors"]

    detail_parts: list[str] = []
    if "address" in parsed:
        detail_parts.append(f"addr {parsed['address']:04X}")
    if "count" in parsed:
        detail_parts.append(f"count {parsed['count']}")
    if "value" in parsed and parsed["frame_kind"] == "write_frame":
        detail_parts.append(f"value {parsed['value']:04X}")
    if "byte_count" in parsed and parsed["frame_kind"] != "write_response":
        detail_parts.append(f"{parsed['byte_count']} data bytes")
    detail = " · ".join(detail_parts)
    parsed["summary"] = f"{parsed['function_name']} · slave {slave}" + (
        f" · {detail}" if detail else ""
    )
    return parsed


def _safe_parse_frame(
    frame_hex: str | bytes | None, *, as_response: bool
) -> dict[str, Any] | None:
    """Parse one captured/reconstructed frame, downgrading errors to results."""
    if not frame_hex:
        return None
    try:
        return parse_rtu_frame(frame_hex, as_response=as_response)
    except ValueError as err:
        return {
            "valid": False,
            "direction": "response" if as_response else "request",
            "errors": [str(err)],
            "raw_hex": str(frame_hex),
        }


def frame_timing(frame_times_ms: Any, frame_count: int) -> list[dict[str, float]]:
    """Turn per-frame arrival offsets into ``arrival_ms``/``gap_ms`` pairs.

    ``frame_times_ms`` is the ``response_frame_times_ms`` list recorded by
    ``capture.py`` (milliseconds since the transaction window opened, one
    entry per response frame). The result holds one dict per frame with the
    arrival offset and the gap since the previous frame (for the first frame
    the gap is measured from the window start, i.e. the request). An
    absent, malformed, or mismatched list yields an empty result so
    pre-v2.7.1 log entries decode exactly as before.
    """
    if not isinstance(frame_times_ms, (list, tuple)) or not frame_times_ms:
        return []
    if len(frame_times_ms) != frame_count:
        return []
    timing: list[dict[str, float]] = []
    previous = 0.0
    for raw in frame_times_ms:
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            return []
        arrival = float(raw)
        timing.append(
            {
                "arrival_ms": round(arrival, 3),
                "gap_ms": round(max(0.0, arrival - previous), 3),
            }
        )
        previous = arrival
    return timing


def analyze_transaction(transaction: dict[str, Any]) -> dict[str, Any]:
    """Attach decoded request/response frame breakdowns to one transaction.

    ``request_hex`` decodes as a request frame (reconstructed or captured TX
    bytes); the captured RX bytes decode as response frame(s), so the panel
    can show the paired request/response of one bus transaction.

    Since v2.7.0 a transaction may carry the *full* raw RX stream of the
    whole coordinator operation (batch reads, board block readers, exception
    plus follow-up): ``response_frames`` (a list of hex strings when present)
    decodes to ``response_frames`` in the result — one breakdown per frame,
    in arrival order. ``response_hex``/``response_frame`` keep the last
    frame for backward compatibility; when only ``response_hex`` is present
    (pre-v2.7.0 log entries, single-frame capture) the list holds exactly
    that one frame.

    Since v2.7.1 the per-frame arrival times recorded by the capture
    (``response_frame_times_ms``) are passed through and each decoded
    response frame gains ``arrival_ms`` (since the window opened) and
    ``gap_ms`` (since the previous frame), which the panel renders as a
    mini waterfall of inter-frame gaps.
    """
    request_hex = transaction.get("request_hex")
    response_hex = transaction.get("response_hex")
    response_frames_hex = transaction.get("response_frames")
    frame_times_ms = transaction.get("response_frame_times_ms")
    last_response_frame = _safe_parse_frame(response_hex, as_response=True)
    if response_frames_hex:
        response_frames = [
            _safe_parse_frame(frame_hex, as_response=True)
            for frame_hex in response_frames_hex
        ]
        timing = frame_timing(frame_times_ms, len(response_frames))
        if timing:
            for frame, stamps in zip(response_frames, timing, strict=True):
                if frame is not None:
                    frame.update(stamps)
            if last_response_frame is not None:
                last_response_frame.update(timing[-1])
    elif response_hex:
        response_frames = [last_response_frame]
        timing = frame_timing(frame_times_ms, 1)
        if timing and last_response_frame is not None:
            last_response_frame.update(timing[0])
    else:
        response_frames = None
        timing = []
    return {
        "transaction": {
            "timestamp": transaction.get("timestamp"),
            "operation": transaction.get("operation"),
            "slave": transaction.get("slave"),
            "address": transaction.get("address"),
            "count": transaction.get("count"),
            "status": transaction.get("status"),
            "error": transaction.get("error"),
            "function_code": transaction.get("function_code"),
            "request_hex": request_hex,
            "request_captured": bool(transaction.get("request_captured")),
            "response_hex": response_hex,
            "response_frames": (
                list(response_frames_hex) if response_frames_hex else None
            ),
            "response_frame_times_ms": (
                [stamps["arrival_ms"] for stamps in timing] if timing else None
            ),
            "duration_ms": transaction.get("duration_ms"),
            "latency": dict(transaction.get("latency") or {}),
        },
        "frame": _safe_parse_frame(request_hex, as_response=False),
        "response_frame": last_response_frame,
        "response_frames": response_frames,
    }


def _duration_stats(durations: list[float]) -> dict[str, Any]:
    if not durations:
        return {
            "samples": 0,
            "avg_ms": None,
            "min_ms": None,
            "max_ms": None,
            "p95_ms": None,
        }
    ordered = sorted(durations)
    p95_index = min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))
    return {
        "samples": len(ordered),
        "avg_ms": round(statistics.fmean(ordered), 1),
        "min_ms": round(ordered[0], 1),
        "max_ms": round(ordered[-1], 1),
        "p95_ms": round(ordered[p95_index], 1),
    }


def build_inspector_view(coordinator: Any, limit: int = 100) -> dict[str, Any]:
    """Build the Traffic Inspector view from a coordinator's rolling log."""
    limit = max(1, min(int(limit), len(coordinator.transaction_log) or 1))
    transactions = list(itertools.islice(coordinator.transaction_log, limit))
    analyzed = [analyze_transaction(transaction) for transaction in transactions]

    durations = [
        float(transaction["duration_ms"])
        for transaction in transactions
        if isinstance(transaction.get("duration_ms"), (int, float))
    ]
    per_slave: dict[int, dict[str, Any]] = {}
    for transaction in transactions:
        slave = transaction.get("slave")
        if slave is None:
            continue
        bucket = per_slave.setdefault(
            int(slave),
            {"count": 0, "errors": 0, "durations": [], "last_seen": None},
        )
        bucket["count"] += 1
        if transaction.get("status") == "error":
            bucket["errors"] += 1
        if isinstance(transaction.get("duration_ms"), (int, float)):
            bucket["durations"].append(float(transaction["duration_ms"]))
        bucket["last_seen"] = transaction.get("timestamp")

    for bucket in per_slave.values():
        stats = _duration_stats(bucket.pop("durations"))
        bucket.update(stats)

    capture_hook = getattr(coordinator, "capture_hook", None)
    responses = sum(
        1 for transaction in transactions if transaction.get("response_hex")
    )
    return {
        "entry_id": getattr(coordinator, "entry_id", None),
        "connected": bool(getattr(coordinator.client, "connected", False)),
        "default_slave_id": getattr(coordinator, "slave_id", None),
        "capture": {
            "hook": capture_hook,
            "supported": capture_hook is not None,
        },
        "stats": {
            "total": len(transactions),
            "errors": sum(
                1
                for transaction in transactions
                if transaction.get("status") == "error"
            ),
            "responses": responses,
            # Fraction of transactions whose RX bytes were captured from the
            # wire (0.0–1.0); None when the log is empty.
            "capture_coverage": capture_coverage(responses, len(transactions)),
            **_duration_stats(durations),
        },
        "per_slave": per_slave,
        "transactions": analyzed,
    }


def capture_coverage(responses: int, total: int) -> float | None:
    """Return the fraction of transactions with RX captured, or None if empty."""
    if total <= 0:
        return None
    return round(min(max(responses, 0), total) / total, 3)
