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
    capture_coverage,
    frame_timing,
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


# ───────────────── v2.6.0: response frames & request/response pairing ─────────────────


def test_parse_as_response_resolves_eight_byte_ambiguity() -> None:
    # Slave 1, FC01, byte count 3, three data bytes: an 8-byte frame that is
    # a read request by default but a coil response with as_response=True.
    frame = _frame_with_crc("010103D0D1D2")
    as_request = parse_rtu_frame(frame)
    assert as_request["frame_kind"] == "read_request"
    assert as_request["direction"] == "request"

    as_response = parse_rtu_frame(frame, as_response=True)
    assert as_response["frame_kind"] == "read_response"
    assert as_response["direction"] == "response"
    assert as_response["byte_count"] == 3
    # First data byte 0xD0 = 0b1101_0000: coils 4, 6, 7 set (LSB first).
    assert as_response["values"][:8] == [
        False,
        False,
        False,
        False,
        True,
        False,
        True,
        True,
    ]
    assert as_response["valid"] is True


def test_parse_as_response_byte_count_register_frame() -> None:
    parsed = parse_rtu_frame(_frame_with_crc("01030443666666"), as_response=True)
    assert parsed["frame_kind"] == "read_response"
    assert parsed["direction"] == "response"
    assert parsed["byte_count"] == 4
    assert parsed["values"] == [0x4366, 0x6666]
    assert parsed["data_hex"] == "43 66 66 66"
    assert parsed["valid"] is True


def test_parse_as_response_odd_register_byte_count_flags_error() -> None:
    parsed = parse_rtu_frame(
        _frame_with_crc("01030343 6666".replace(" ", "")), as_response=True
    )
    assert parsed["frame_kind"] == "read_response"
    assert parsed["valid"] is False
    assert any("must be even" in error for error in parsed["errors"])


def test_parse_write_echo_response_direction() -> None:
    parsed = parse_rtu_frame(_frame_with_crc("030600800001"), as_response=True)
    assert parsed["frame_kind"] == "write_frame"
    assert parsed["direction"] == "response"
    assert parsed["address"] == 0x80
    assert parsed["value"] == 1
    assert parsed["valid"] is True


def test_parse_exception_direction_is_always_response() -> None:
    for as_response in (False, True):
        parsed = parse_rtu_frame(_frame_with_crc("018302"), as_response=as_response)
        assert parsed["frame_kind"] == "exception_response"
        assert parsed["direction"] == "response"


def test_analyze_transaction_pairs_request_and_response() -> None:
    transaction = {
        "timestamp": datetime.now().isoformat(),
        "operation": "read_holding",
        "slave": 1,
        "address": 7,
        "count": 1,
        "status": "ok",
        "function_code": "0x03",
        "request_hex": _frame_with_crc("010300070001"),
        "request_captured": True,
        "response_hex": _frame_with_crc("010302002A"),
        "duration_ms": 18.3,
        "latency": {"request_ms": 18.0},
    }
    analyzed = analyze_transaction(transaction)
    assert analyzed["frame"]["frame_kind"] == "read_request"
    assert analyzed["frame"]["direction"] == "request"
    assert analyzed["response_frame"]["frame_kind"] == "read_response"
    assert analyzed["response_frame"]["direction"] == "response"
    assert analyzed["response_frame"]["values"] == [0x2A]
    assert analyzed["response_frame"]["crc_valid"] is True
    assert analyzed["transaction"]["response_hex"] == transaction["response_hex"]
    assert analyzed["transaction"]["request_captured"] is True


def test_analyze_transaction_exception_response_pairing() -> None:
    analyzed = analyze_transaction(
        {
            "operation": "read_holding",
            "slave": 1,
            "address": 10,
            "status": "error",
            "error": "ExceptionResponse: Illegal Data Address",
            "request_hex": _frame_with_crc("0103000A0001"),
            "response_hex": _frame_with_crc("018302"),
        }
    )
    assert analyzed["frame"]["frame_kind"] == "read_request"
    assert analyzed["response_frame"]["frame_kind"] == "exception_response"
    assert analyzed["response_frame"]["exception_name"] == "Illegal Data Address"


def test_analyze_transaction_without_response() -> None:
    analyzed = analyze_transaction(
        {
            "operation": "read_holding",
            "status": "error",
            "error": "Modbus Timeout",
            "request_hex": _frame_with_crc("010300070001"),
        }
    )
    assert analyzed["response_frame"] is None
    assert analyzed["transaction"]["response_hex"] is None
    assert analyzed["transaction"]["request_captured"] is False

    garbage = analyze_transaction(
        {
            "status": "ok",
            "request_hex": _frame_with_crc("010300070001"),
            "response_hex": "zz",
        }
    )
    assert garbage["response_frame"]["valid"] is False
    assert garbage["response_frame"]["direction"] == "response"
    assert garbage["response_frame"]["errors"]


def test_build_inspector_view_reports_capture_and_response_stats() -> None:
    coordinator = _coordinator_with_log(
        [
            _transaction(response_hex=_frame_with_crc("010302002A")),
            _transaction(),
        ]
    )
    coordinator.capture_hook = "trace_packet"
    view = build_inspector_view(coordinator)
    assert view["capture"] == {"hook": "trace_packet", "supported": True}
    assert view["stats"]["responses"] == 1
    assert view["transactions"][0]["response_frame"]["crc_valid"] is True
    assert view["transactions"][1]["response_frame"] is None


def test_build_inspector_view_without_capture_support() -> None:
    view = build_inspector_view(_coordinator_with_log([_transaction()]))
    assert view["capture"] == {"hook": None, "supported": False}
    assert view["stats"]["responses"] == 0


# ───────────────── v2.7.0: multi-frame response lists ─────────────────


def test_analyze_transaction_multi_frame_response_list() -> None:
    first = _frame_with_crc("02040400010002")
    second = _frame_with_crc("020302002A")
    analyzed = analyze_transaction(
        {
            "operation": "read_input",
            "slave": 2,
            "address": 0,
            "count": 4,
            "status": "ok",
            "request_hex": _frame_with_crc("020400000004"),
            "request_captured": True,
            "response_hex": second,  # last frame, backward compatibility
            "response_frames": [first, second],
            "duration_ms": 41.0,
        }
    )
    # The last frame remains available under the legacy key.
    assert analyzed["response_frame"]["values"] == [0x2A]
    assert analyzed["transaction"]["response_hex"] == second
    # The full RX stream is decoded frame by frame, in arrival order.
    assert analyzed["transaction"]["response_frames"] == [first, second]
    assert [frame["raw_hex"] for frame in analyzed["response_frames"]] == [
        first,
        second,
    ]
    assert analyzed["response_frames"][0]["frame_kind"] == "read_response"
    assert analyzed["response_frames"][0]["values"] == [0x1, 0x2]
    assert analyzed["response_frames"][1]["frame_kind"] == "read_response"
    assert analyzed["response_frames"][1]["values"] == [0x2A]
    assert all(
        frame["direction"] == "response" for frame in analyzed["response_frames"]
    )
    assert all(frame["crc_valid"] for frame in analyzed["response_frames"])


def test_analyze_transaction_legacy_single_response_synthesizes_list() -> None:
    # Pre-v2.7.0 log entries carry only response_hex; the list then holds
    # exactly that one decoded frame.
    analyzed = analyze_transaction(
        {
            "operation": "read_holding",
            "slave": 1,
            "request_hex": _frame_with_crc("010300070001"),
            "response_hex": _frame_with_crc("010302002A"),
        }
    )
    assert analyzed["response_frames"] == [analyzed["response_frame"]]
    assert analyzed["transaction"]["response_frames"] is None


def test_analyze_transaction_multi_frame_with_exception_in_stream() -> None:
    analyzed = analyze_transaction(
        {
            "operation": "read_input",
            "slave": 2,
            "status": "error",
            "error": "ExceptionResponse: Illegal Data Address",
            "request_hex": _frame_with_crc("0203000A0001"),
            # The coordinator always sets response_hex to the last frame.
            "response_hex": _frame_with_crc("020302002A"),
            "response_frames": [
                _frame_with_crc("028302"),
                _frame_with_crc("020302002A"),
            ],
        }
    )
    kinds = [frame["frame_kind"] for frame in analyzed["response_frames"]]
    assert kinds == ["exception_response", "read_response"]
    assert analyzed["response_frames"][0]["exception_name"] == "Illegal Data Address"
    # response_frame keeps pointing at the last frame of the stream.
    assert analyzed["response_frame"]["frame_kind"] == "read_response"


def test_analyze_transaction_without_any_response() -> None:
    analyzed = analyze_transaction(
        {
            "operation": "read_holding",
            "status": "error",
            "error": "Modbus Timeout",
            "request_hex": _frame_with_crc("010300070001"),
        }
    )
    assert analyzed["response_frames"] is None
    assert analyzed["response_frame"] is None
    assert analyzed["transaction"]["response_frames"] is None


def test_analyze_transaction_garbage_frame_in_multi_frame_stream() -> None:
    # One broken frame must not break the list; it degrades to its own
    # error entry like any single parse failure.
    analyzed = analyze_transaction(
        {
            "operation": "batch_write",
            "slave": 1,
            "status": "ok",
            "request_hex": _frame_with_crc("010600010002"),
            "response_hex": _frame_with_crc("010600010002"),
            "response_frames": ["zz", _frame_with_crc("010600010002")],
        }
    )
    assert analyzed["response_frames"][0]["valid"] is False
    assert analyzed["response_frames"][0]["errors"]
    assert analyzed["response_frames"][1]["valid"] is True


def test_build_inspector_view_propagates_multi_frame_responses() -> None:
    first = _frame_with_crc("02040400010002")
    second = _frame_with_crc("020302002A")
    coordinator = _coordinator_with_log(
        [
            _transaction(
                slave=2,
                response_hex=second,
                response_frames=[first, second],
                duration_ms=41.0,
            ),
            _transaction(),
        ]
    )
    view = build_inspector_view(coordinator)
    assert view["stats"]["responses"] == 1  # counted by response_hex, as before
    analyzed = view["transactions"][0]
    assert analyzed["transaction"]["response_frames"] == [first, second]
    assert len(analyzed["response_frames"]) == 2
    assert analyzed["response_frames"][0]["slave_id"] == 2


# ───────────────── v2.7.1: per-frame RX timing & capture coverage ─────────────────


def test_frame_timing_derives_arrival_and_gaps() -> None:
    assert frame_timing([15.0, 35.0, 35.5], 3) == [
        {"arrival_ms": 15.0, "gap_ms": 15.0},
        {"arrival_ms": 35.0, "gap_ms": 20.0},
        {"arrival_ms": 35.5, "gap_ms": 0.5},
    ]


def test_frame_timing_rejects_mismatched_or_malformed_lists() -> None:
    assert frame_timing(None, 2) == []
    assert frame_timing([], 0) == []
    assert frame_timing([1.0], 2) == []  # length mismatch
    assert frame_timing([1.0, "x"], 2) == []  # non-numeric entry
    assert frame_timing([1.0, True], 2) == []  # bools are not timings
    assert frame_timing("12.0", 1) == []  # not a list at all


def test_frame_timing_never_reports_negative_gaps() -> None:
    timing = frame_timing([10.0, 8.0], 2)
    assert timing[1]["gap_ms"] == 0.0
    assert timing[1]["arrival_ms"] == 8.0


def test_analyze_transaction_attaches_frame_arrival_and_gap() -> None:
    first = _frame_with_crc("02040400010002")
    second = _frame_with_crc("020302002A")
    analyzed = analyze_transaction(
        {
            "operation": "read_input",
            "slave": 2,
            "status": "ok",
            "request_hex": _frame_with_crc("020400000004"),
            "response_hex": second,
            "response_frames": [first, second],
            "response_frame_times_ms": [9.6, 24.1],
            "duration_ms": 25.0,
        }
    )
    frames = analyzed["response_frames"]
    assert frames[0]["arrival_ms"] == 9.6
    assert frames[0]["gap_ms"] == 9.6
    assert frames[1]["arrival_ms"] == 24.1
    assert frames[1]["gap_ms"] == pytest.approx(14.5)
    # The legacy last-frame view carries the last frame's timing too.
    assert analyzed["response_frame"]["arrival_ms"] == 24.1
    assert analyzed["transaction"]["response_frame_times_ms"] == [9.6, 24.1]


def test_analyze_transaction_single_frame_timing() -> None:
    analyzed = analyze_transaction(
        {
            "operation": "read_holding",
            "slave": 1,
            "request_hex": _frame_with_crc("010300070001"),
            "response_hex": _frame_with_crc("010302002A"),
            "response_frames": [_frame_with_crc("010302002A")],
            "response_frame_times_ms": [12.5],
        }
    )
    assert analyzed["response_frame"]["arrival_ms"] == 12.5
    assert analyzed["response_frame"]["gap_ms"] == 12.5
    assert analyzed["response_frames"][0]["arrival_ms"] == 12.5


def test_analyze_transaction_legacy_response_hex_with_timing() -> None:
    # A one-frame capture that only carries response_hex (older entry
    # shape) still gets its arrival stamped when a timing list is present.
    analyzed = analyze_transaction(
        {
            "operation": "read_holding",
            "request_hex": _frame_with_crc("010300070001"),
            "response_hex": _frame_with_crc("010302002A"),
            "response_frame_times_ms": [3.0],
        }
    )
    assert analyzed["response_frame"]["arrival_ms"] == 3.0
    assert analyzed["transaction"]["response_frame_times_ms"] == [3.0]


def test_analyze_transaction_without_timing_decodes_as_before() -> None:
    first = _frame_with_crc("02040400010002")
    second = _frame_with_crc("020302002A")
    analyzed = analyze_transaction(
        {
            "operation": "read_input",
            "request_hex": _frame_with_crc("020400000004"),
            "response_hex": second,
            "response_frames": [first, second],
        }
    )
    assert analyzed["transaction"]["response_frame_times_ms"] is None
    assert all("arrival_ms" not in frame for frame in analyzed["response_frames"])
    assert "arrival_ms" not in analyzed["response_frame"]


def test_analyze_transaction_ignores_mismatched_timing_list() -> None:
    first = _frame_with_crc("02040400010002")
    second = _frame_with_crc("020302002A")
    analyzed = analyze_transaction(
        {
            "operation": "read_input",
            "request_hex": _frame_with_crc("020400000004"),
            "response_hex": second,
            "response_frames": [first, second],
            "response_frame_times_ms": [1.0],  # one stamp for two frames
        }
    )
    assert analyzed["transaction"]["response_frame_times_ms"] is None
    assert all("arrival_ms" not in frame for frame in analyzed["response_frames"])


def test_analyze_transaction_timing_survives_garbage_frame() -> None:
    analyzed = analyze_transaction(
        {
            "operation": "batch_write",
            "request_hex": _frame_with_crc("010600010002"),
            "response_hex": _frame_with_crc("010600010002"),
            "response_frames": ["zz", _frame_with_crc("010600010002")],
            "response_frame_times_ms": [2.0, 9.0],
        }
    )
    assert analyzed["response_frames"][0]["valid"] is False
    assert analyzed["response_frames"][0]["arrival_ms"] == 2.0
    assert analyzed["response_frames"][1]["gap_ms"] == 7.0


def test_analyze_transaction_no_response_has_no_timing() -> None:
    analyzed = analyze_transaction(
        {
            "operation": "read_holding",
            "status": "error",
            "request_hex": _frame_with_crc("010300070001"),
            "response_frame_times_ms": [1.0],  # stale/bogus without frames
        }
    )
    assert analyzed["response_frames"] is None
    assert analyzed["transaction"]["response_frame_times_ms"] is None


def test_capture_coverage_fraction() -> None:
    assert capture_coverage(0, 0) is None
    assert capture_coverage(3, 4) == 0.75
    assert capture_coverage(2, 3) == 0.667
    assert capture_coverage(5, 5) == 1.0
    assert capture_coverage(0, 5) == 0.0
    # Defensive clamping: never above 1.0 or below 0.0.
    assert capture_coverage(9, 4) == 1.0
    assert capture_coverage(-1, 4) == 0.0


def test_build_inspector_view_reports_capture_coverage() -> None:
    coordinator = _coordinator_with_log(
        [
            _transaction(response_hex=_frame_with_crc("010302002A")),
            _transaction(response_hex=_frame_with_crc("010302002A")),
            _transaction(response_hex=_frame_with_crc("010302002A")),
            _transaction(),
        ]
    )
    coordinator.capture_hook = "trace_packet"
    view = build_inspector_view(coordinator)
    assert view["stats"]["responses"] == 3
    assert view["stats"]["total"] == 4
    assert view["stats"]["capture_coverage"] == 0.75


def test_build_inspector_view_capture_coverage_empty_and_full() -> None:
    empty = build_inspector_view(_coordinator_with_log([]))
    assert empty["stats"]["capture_coverage"] is None

    full = build_inspector_view(
        _coordinator_with_log(
            [_transaction(response_hex=_frame_with_crc("010302002A"))]
        )
    )
    assert full["stats"]["capture_coverage"] == 1.0

    none = build_inspector_view(_coordinator_with_log([_transaction()]))
    assert none["stats"]["capture_coverage"] == 0.0


def test_build_inspector_view_propagates_frame_timing() -> None:
    first = _frame_with_crc("02040400010002")
    second = _frame_with_crc("020302002A")
    coordinator = _coordinator_with_log(
        [
            _transaction(
                slave=2,
                response_hex=second,
                response_frames=[first, second],
                response_frame_times_ms=[9.6, 24.1],
            )
        ]
    )
    view = build_inspector_view(coordinator)
    analyzed = view["transactions"][0]
    assert analyzed["transaction"]["response_frame_times_ms"] == [9.6, 24.1]
    assert [frame["gap_ms"] for frame in analyzed["response_frames"]] == [
        9.6,
        pytest.approx(14.5),
    ]
