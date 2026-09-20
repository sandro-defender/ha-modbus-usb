"""Tests for the Traffic Inspector RTU frame analyzer and view builder."""

from __future__ import annotations

from collections import deque
from datetime import datetime
from types import SimpleNamespace

import pytest

from custom_components.modbus_usb.diagnostics import modbus_crc16
from custom_components.modbus_usb.inspector import (
    analyze_transaction,
    build_inspector_view,
    normalize_frame_hex,
    parse_rtu_frame,
)

pytestmark = pytest.mark.fast


def _frame_with_crc(payload_hex: str) -> str:
    payload = bytes.fromhex(payload_hex)
    crc = modbus_crc16(payload)
    frame = payload + bytes((crc & 0xFF, crc >> 8))
    return " ".join(f"{byte:02X}" for byte in frame)


def test_normalize_frame_hex_accepts_common_formats() -> None:
    expected = bytes.fromhex("0103000000 02C40B".replace(" ", ""))
    assert normalize_frame_hex("01 03 00 00 00 02 C4 0B") == expected
    assert normalize_frame_hex("0x01,0x03,0x00,0x00,0x00,0x02,0xC4,0x0B") == expected
    assert normalize_frame_hex("01-03-00-00-00-02-C4-0b") == expected
    assert normalize_frame_hex("0103000000 02C40B") == expected
    assert normalize_frame_hex(expected) == expected


def test_normalize_frame_hex_rejects_bad_input() -> None:
    for bad in ("", "   ", "01 0", "0G", "0xZZ", 1234):
        with pytest.raises(ValueError):
            normalize_frame_hex(bad)


def test_parse_fc03_read_request() -> None:
    frame = _frame_with_crc("01030000000A")
    parsed = parse_rtu_frame(frame)
    assert parsed["slave_id"] == 1
    assert parsed["function_code"] == 0x03
    assert parsed["function_name"] == "Read Holding Registers"
    assert parsed["frame_kind"] == "read_request"
    assert parsed["address"] == 0
    assert parsed["count"] == 10
    assert parsed["crc_valid"] is True
    assert parsed["crc_low"] == parsed["crc_expected"] & 0xFF
    assert parsed["crc_high"] == parsed["crc_expected"] >> 8
    assert parsed["valid"] is True
    assert parsed["errors"] == []
    assert "Read Holding Registers" in parsed["summary"]


def test_parse_fc04_input_request_known_vector() -> None:
    # Known-good vector: CRC of 01 04 00 00 00 02 is C4 0B for FC03; the
    # FC04 frame below is computed fresh by the helper on every run.
    parsed = parse_rtu_frame(_frame_with_crc("010400000002"))
    assert parsed["frame_kind"] == "read_request"
    assert parsed["function_name"] == "Read Input Registers"
    assert parsed["count"] == 2
    assert parsed["valid"] is True


def test_parse_detects_crc_failure() -> None:
    frame = bytearray.fromhex(_frame_with_crc("010300000002").replace(" ", ""))
    frame[-1] ^= 0xFF  # corrupt the CRC high byte
    parsed = parse_rtu_frame(bytes(frame))
    assert parsed["crc_valid"] is False
    assert parsed["valid"] is False
    assert any("CRC mismatch" in error for error in parsed["errors"])
    assert parsed["crc_received"] != parsed["crc_expected"]


def test_parse_fc05_coil_write_variants() -> None:
    on = parse_rtu_frame(_frame_with_crc("0105000AFF00"))
    assert on["frame_kind"] == "write_frame"
    assert on["coil_on"] is True
    assert on["value"] == 0xFF00
    assert on["valid"] is True

    off = parse_rtu_frame(_frame_with_crc("0105000A0000"))
    assert off["coil_on"] is False
    assert off["valid"] is True

    bogus = parse_rtu_frame(_frame_with_crc("0105000A1234"))
    assert bogus["valid"] is False
    assert any("FF00" in error for error in bogus["errors"])


def test_parse_fc06_register_write() -> None:
    parsed = parse_rtu_frame(_frame_with_crc("0306008000 01".replace(" ", "")))
    assert parsed["function_name"] == "Write Single Register"
    assert parsed["address"] == 0x80
    assert parsed["value"] == 1
    assert parsed["valid"] is True


def test_parse_fc03_read_response_registers() -> None:
    # Slave 1, FC03, byte count 4, two register words, then CRC.
    parsed = parse_rtu_frame(_frame_with_crc("01030443666666"))
    assert parsed["frame_kind"] == "read_response"
    assert parsed["byte_count"] == 4
    assert parsed["values"] == [0x4366, 0x6666]
    assert parsed["data_hex"] == "43 66 66 66"
    assert parsed["valid"] is True


def test_parse_fc01_coil_response_bits() -> None:
    parsed = parse_rtu_frame(_frame_with_crc("01010105"))
    assert parsed["frame_kind"] == "read_response"
    assert parsed["values"][:8] == [
        True,
        False,
        True,
        False,
        False,
        False,
        False,
        False,
    ]


def test_parse_fc03_response_byte_count_mismatch() -> None:
    parsed = parse_rtu_frame(_frame_with_crc("01030643 66".replace(" ", "")))
    assert parsed["valid"] is False
    assert any("declares 6" in error for error in parsed["errors"])


def test_parse_fc0f_multi_coil_request() -> None:
    parsed = parse_rtu_frame(_frame_with_crc("010F0000000A02CD01"))
    assert parsed["frame_kind"] == "write_request"
    assert parsed["count"] == 10
    assert parsed["byte_count"] == 2
    # 0xCD = coils 0,2,3,6,7 set; 0x01 = coil 8 set, coil 9 clear.
    assert parsed["values"][0] is True
    assert parsed["values"][7] is True
    assert parsed["values"][8] is True
    assert parsed["values"][9] is False
    assert parsed["valid"] is True


def test_parse_fc0f_byte_count_mismatch_flags_error() -> None:
    parsed = parse_rtu_frame(_frame_with_crc("010F0000000A03CD01 00".replace(" ", "")))
    assert parsed["valid"] is False
    assert any("does not match" in error for error in parsed["errors"])


def test_parse_fc10_multi_register_request() -> None:
    parsed = parse_rtu_frame(
        _frame_with_crc("0110000A00020400 6400C8".replace(" ", ""))
    )
    assert parsed["frame_kind"] == "write_request"
    assert parsed["count"] == 2
    assert parsed["byte_count"] == 4
    assert parsed["values"] == [100, 200]
    assert parsed["valid"] is True


def test_parse_fc10_response_echo() -> None:
    parsed = parse_rtu_frame(_frame_with_crc("011000 0A0002".replace(" ", "")))
    assert parsed["frame_kind"] == "write_response"
    assert parsed["address"] == 0x0A
    assert parsed["count"] == 2
    assert parsed["valid"] is True


def test_parse_exception_response() -> None:
    parsed = parse_rtu_frame(_frame_with_crc("018302"))
    assert parsed["frame_kind"] == "exception_response"
    assert parsed["function_code"] == 0x83
    assert parsed["base_function_code"] == 0x03
    assert parsed["exception_code"] == 2
    assert parsed["exception_name"] == "Illegal Data Address"
    assert parsed["crc_valid"] is True
    assert parsed["valid"] is True
    assert "Exception" in parsed["summary"]


def test_parse_truncated_frame_reports_error() -> None:
    parsed = parse_rtu_frame("01 03")
    assert parsed["valid"] is False
    assert parsed["crc_present"] is False
    assert parsed["errors"]


def test_parse_unknown_function_code() -> None:
    parsed = parse_rtu_frame(_frame_with_crc("01440000000 1".replace(" ", "")))
    assert parsed["frame_kind"] == "unknown"
    assert parsed["valid"] is False
    assert any("not a standard" in error for error in parsed["errors"])


def test_parse_read_request_count_out_of_range() -> None:
    parsed = parse_rtu_frame(_frame_with_crc("01030000F00 0".replace(" ", "")))
    assert parsed["count"] == 0xF000
    assert parsed["valid"] is False
    assert any("outside the Modbus range" in error for error in parsed["errors"])


def test_analyze_transaction_attaches_frame_and_latency() -> None:
    transaction = {
        "timestamp": datetime.now().isoformat(),
        "operation": "read_holding",
        "slave": 1,
        "address": 0,
        "count": 2,
        "status": "ok",
        "function_code": "0x03",
        "request_hex": _frame_with_crc("010300000002"),
        "duration_ms": 21.4,
        "latency": {"lock_wait_ms": 0.2, "request_ms": 21.2},
    }
    analyzed = analyze_transaction(transaction)
    assert analyzed["frame"]["crc_valid"] is True
    assert analyzed["frame"]["count"] == 2
    assert analyzed["transaction"]["duration_ms"] == 21.4
    assert analyzed["transaction"]["latency"] == {
        "lock_wait_ms": 0.2,
        "request_ms": 21.2,
    }


def test_analyze_transaction_handles_missing_and_garbage_frames() -> None:
    no_frame = analyze_transaction({"operation": "scan_found", "status": "ok"})
    assert no_frame["frame"] is None

    garbage = analyze_transaction({"request_hex": "not hex", "status": "ok"})
    assert garbage["frame"]["valid"] is False
    assert garbage["frame"]["errors"]


def _transaction(**overrides):
    base = {
        "timestamp": datetime.now().isoformat(),
        "operation": "read_holding",
        "slave": 1,
        "address": 0,
        "count": 2,
        "status": "ok",
        "request_hex": _frame_with_crc("010300000002"),
        "duration_ms": 10.0,
    }
    base.update(overrides)
    return base


def _coordinator_with_log(transactions):
    return SimpleNamespace(
        entry_id="entry-1",
        slave_id=1,
        client=SimpleNamespace(connected=True),
        transaction_log=deque(transactions),
    )


def test_build_inspector_view_stats_and_per_slave() -> None:
    coordinator = _coordinator_with_log(
        [
            _transaction(duration_ms=10.0),
            _transaction(slave=2, duration_ms=30.0, status="error", error="timeout"),
            _transaction(duration_ms=20.0, latency={"request_ms": 19.0}),
        ]
    )
    view = build_inspector_view(coordinator)
    assert view["entry_id"] == "entry-1"
    assert view["connected"] is True
    assert view["stats"]["total"] == 3
    assert view["stats"]["errors"] == 1
    assert view["stats"]["avg_ms"] == 20.0
    assert view["stats"]["max_ms"] == 30.0
    assert view["per_slave"][1]["count"] == 2
    assert view["per_slave"][1]["errors"] == 0
    assert view["per_slave"][1]["avg_ms"] == 15.0
    assert view["per_slave"][2]["errors"] == 1
    assert len(view["transactions"]) == 3
    assert view["transactions"][0]["frame"]["crc_valid"] is True
    assert view["transactions"][2]["transaction"]["latency"] == {"request_ms": 19.0}


def test_build_inspector_view_respects_limit_and_empty_log() -> None:
    coordinator = _coordinator_with_log([_transaction() for _ in range(5)])
    view = build_inspector_view(coordinator, limit=2)
    assert view["stats"]["total"] == 2

    empty = build_inspector_view(_coordinator_with_log([]))
    assert empty["stats"]["total"] == 0
    assert empty["stats"]["avg_ms"] is None
    assert empty["transactions"] == []
    assert empty["per_slave"] == {}
