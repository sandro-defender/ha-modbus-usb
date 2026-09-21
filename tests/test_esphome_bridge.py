"""Unit tests for the pure RTU framing / pairing used by the ESPHome API hub."""

from __future__ import annotations

import threading

import pytest

from custom_components.modbus_usb.diagnostics import modbus_crc16
from custom_components.modbus_usb.esphome_bridge import (
    FramePairer,
    ModbusFrameError,
    ModbusNoResponse,
    append_crc,
    build_rtu_request,
    crc_valid,
    frame_to_hex,
    hex_to_frame,
    parse_rtu_response,
)

pytestmark = pytest.mark.fast


def _frame(payload_hex: str) -> bytes:
    return append_crc(bytes.fromhex(payload_hex.replace(" ", "")))


# ───────────────────────────── helpers ─────────────────────────────


def test_append_crc_matches_textbook_vector() -> None:
    assert append_crc(bytes.fromhex("010300000001")) == bytes.fromhex(
        "010300000001840A"
    )
    assert crc_valid(append_crc(b"\x01\x06\x00\x80\x00\x01"))
    assert not crc_valid(b"\x01\x03")
    assert not crc_valid(b"\x01\x03\x00\x00")


def test_hex_round_trip_accepts_common_spellings() -> None:
    frame = _frame("01 03 02 00 2A")
    text = frame_to_hex(frame)
    assert text.startswith("01 03 02 00 2A ") and len(text.split()) == 7
    assert hex_to_frame(text) == frame
    assert hex_to_frame(text.replace(" ", "")) == frame
    assert hex_to_frame(text.replace(" ", ":")) == frame
    assert hex_to_frame(list(frame)) == frame
    assert hex_to_frame(frame) == frame
    with pytest.raises(ModbusFrameError):
        hex_to_frame("010")
    with pytest.raises(ModbusFrameError):
        hex_to_frame("zz")


# ───────────────────────── request encoding ─────────────────────────


@pytest.mark.parametrize(
    ("function_code", "kwargs", "payload_hex"),
    [
        (0x01, {"count": 8}, "01 01 00 13 00 08"),
        (0x02, {"count": 2}, "01 02 00 13 00 02"),
        (0x03, {"count": 1}, "01 03 00 13 00 01"),
        (0x04, {"count": 125}, "01 04 00 13 00 7D"),
        (0x05, {"value": True}, "01 05 00 13 FF 00"),
        (0x05, {"value": False}, "01 05 00 13 00 00"),
        (0x06, {"value": 0x1234}, "01 06 00 13 12 34"),
        (0x06, {"value": -1}, "01 06 00 13 FF FF"),
        (0x0F, {"values": [True, False, True, True]}, "01 0F 00 13 00 04 01 0D"),
        (0x10, {"values": [1, 0xBEEF]}, "01 10 00 13 00 02 04 00 01 BE EF"),
    ],
)
def test_build_rtu_request_encodes_every_supported_function(
    function_code: int, kwargs: dict, payload_hex: str
) -> None:
    frame = build_rtu_request(1, function_code, 0x13, **kwargs)
    expected = bytes.fromhex(payload_hex.replace(" ", ""))
    assert frame[:-2] == expected
    crc = modbus_crc16(expected)
    assert frame[-2:] == bytes((crc & 0xFF, crc >> 8))
    assert crc_valid(frame)


def test_build_rtu_request_packs_multi_byte_coils_lsb_first() -> None:
    values = [True] * 9 + [False] * 6 + [True]  # 16 coils, bits 0-8 and 15
    frame = build_rtu_request(3, 0x0F, 0, values=values)
    assert frame[2:9] == bytes.fromhex("0000001002FF81")


@pytest.mark.parametrize(
    ("args", "kwargs", "message"),
    [
        ((248, 0x03, 0), {"count": 1}, "Slave ID"),
        ((True, 0x03, 0), {"count": 1}, "Slave ID"),
        ((1, 0x03, 70000), {"count": 1}, "Address"),
        ((1, 0x03, 0), {}, "count is required"),
        ((1, 0x03, 0), {"count": 126}, "count must be"),
        ((1, 0x01, 0), {"count": 2001}, "count must be"),
        ((1, 0x06, 0), {}, "value is required"),
        ((1, 0x0F, 0), {"values": []}, "FC0F"),
        ((1, 0x10, 0), {"values": list(range(124))}, "FC10"),
        ((1, 0x2B, 0), {"count": 1}, "Unsupported function code"),
    ],
)
def test_build_rtu_request_rejects_bad_parameters(
    args: tuple, kwargs: dict, message: str
) -> None:
    with pytest.raises(ModbusFrameError, match=message):
        build_rtu_request(*args, **kwargs)


# ───────────────────────── response decoding ─────────────────────────


def test_parse_read_registers_response() -> None:
    response = parse_rtu_response(
        _frame("01 03 04 00 2A 12 34"), expected_slave=1, expected_function=3
    )
    assert response.registers == [42, 0x1234]
    assert response.bits == []
    assert not response.isError()
    assert response.exception_code is None
    assert response.function_code == 3
    assert response.slave_id == response.dev_id == 1
    assert "fc=3" in str(response)


def test_parse_read_bits_response_truncates_to_requested_count() -> None:
    response = parse_rtu_response(
        _frame("01 01 01 0D"), expected_function=1, requested_count=4
    )
    assert response.bits == [True, False, True, True]
    untruncated = parse_rtu_response(_frame("01 02 01 0D"))
    assert len(untruncated.bits) == 8


def test_parse_write_echoes() -> None:
    coil = parse_rtu_response(_frame("01 05 00 13 FF 00"))
    assert (coil.address, coil.value) == (0x13, True)
    register = parse_rtu_response(_frame("01 06 00 13 12 34"))
    assert (register.address, register.value) == (0x13, 0x1234)
    coils = parse_rtu_response(_frame("01 0F 00 13 00 04"))
    assert (coils.address, coils.count) == (0x13, 4)
    registers = parse_rtu_response(_frame("01 10 00 13 00 02"))
    assert (registers.address, registers.count) == (0x13, 2)


def test_parse_exception_response_is_error_result() -> None:
    response = parse_rtu_response(
        _frame("01 83 02"), expected_slave=1, expected_function=3
    )
    assert response.isError()
    assert response.exception_code == 2
    assert response.function_code == 0x83
    assert "Exception Response(131, 3, 2)" == str(response)


@pytest.mark.parametrize(
    ("frame", "kwargs", "message"),
    [
        (b"\x01\x03\x00", {}, "too short"),
        (_frame("01 03 02 00 2A")[:-1] + b"\x00", {}, "CRC mismatch"),
        (_frame("02 03 02 00 2A"), {"expected_slave": 1}, "Reply from slave 2"),
        (_frame("01 04 02 00 2A"), {"expected_function": 3}, "expected FC03"),
        (_frame("01 84 02"), {"expected_function": 3}, "expected FC03"),
        (_frame("01 83 02 03"), {}, "Malformed exception"),
        (_frame("01 03 04 00 2A"), {}, "Byte count"),
        (_frame("01 03 01 2A"), {}, "odd byte count"),
        (_frame("01 06 00 13 12"), {}, "Write echo"),
        (_frame("01 2B 0E 01"), {}, "Unsupported function code"),
    ],
)
def test_parse_rtu_response_rejects_malformed_frames(
    frame: bytes, kwargs: dict, message: str
) -> None:
    with pytest.raises(ModbusFrameError, match=message):
        parse_rtu_response(frame, **kwargs)


# ───────────────────────────── pairing ─────────────────────────────


def test_pairer_delivers_matching_frame_to_waiter() -> None:
    pairer = FramePairer()
    reply = _frame("01 03 02 00 2A")
    pairer.begin(1, 3)
    assert pairer.open
    timer = threading.Timer(0.02, pairer.deliver, args=(reply,))
    timer.start()
    assert pairer.wait(1.0) == reply
    assert not pairer.open
    assert pairer.received == 1 and pairer.dropped == 0


def test_pairer_accepts_exception_for_base_function() -> None:
    pairer = FramePairer()
    pairer.begin(1, 3)
    assert pairer.deliver(_frame("01 83 02"))
    assert pairer.wait(0.1) == _frame("01 83 02")


def test_pairer_drops_stale_unsolicited_and_mismatched_frames() -> None:
    pairer = FramePairer()
    # No request open → unsolicited.
    assert not pairer.deliver(_frame("01 03 02 00 2A"))
    pairer.begin(1, 3)
    assert not pairer.deliver(b"\x01\x03")  # too short
    assert not pairer.deliver(_frame("02 03 02 00 2A"))  # other slave
    assert not pairer.deliver(_frame("01 04 02 00 2A"))  # other function
    assert pairer.deliver(_frame("01 03 02 00 2A"))
    # A second frame for the same request is dropped (one reply per request).
    assert not pairer.deliver(_frame("01 03 02 00 2B"))
    assert pairer.dropped == 5
    assert pairer.wait(0.1) == _frame("01 03 02 00 2A")


def test_pairer_times_out_and_closes_the_slot() -> None:
    pairer = FramePairer()
    pairer.begin(1, 3)
    with pytest.raises(ModbusNoResponse, match="No response received"):
        pairer.wait(0.01)
    assert not pairer.open
    # A late reply after the timeout is discarded, not paired with the next.
    assert not pairer.deliver(_frame("01 03 02 00 2A"))


def test_pairer_begin_resets_previous_state() -> None:
    pairer = FramePairer()
    pairer.begin(1, 3)
    pairer.deliver(_frame("01 03 02 00 2A"))
    pairer.begin(1, 4)  # new request before the previous reply was consumed
    with pytest.raises(ModbusNoResponse):
        pairer.wait(0.01)


def test_pairer_is_thread_safe_under_concurrent_delivery() -> None:
    pairer = FramePairer()
    good = _frame("05 03 02 00 01")
    noise = [_frame(f"{slave:02X} 03 02 00 01") for slave in range(1, 20) if slave != 5]
    results: list[bytes] = []

    def waiter() -> None:
        pairer.begin(5, 3)
        barrier.wait()
        results.append(pairer.wait(2.0))

    def spammer(frame: bytes) -> None:
        barrier.wait()
        for _ in range(50):
            pairer.deliver(frame)

    threads = [threading.Thread(target=spammer, args=(frame,)) for frame in noise]
    threads.append(threading.Thread(target=spammer, args=(good,)))
    barrier = threading.Barrier(len(threads) + 1)
    waiter_thread = threading.Thread(target=waiter)
    waiter_thread.start()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    waiter_thread.join()
    assert results == [good]
    assert pairer.received == 1
