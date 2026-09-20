"""Tests for real RTU response capture through pymodbus transaction tracing."""

from __future__ import annotations

import logging
import threading

import pytest

from custom_components.modbus_usb.capture import (
    RX_BUFFER_LIMIT,
    ResponseCapture,
    extract_response_frame,
    install_response_capture,
    parse_frame_log_line,
)
from custom_components.modbus_usb.diagnostics import modbus_crc16

pytestmark = pytest.mark.fast


def _frame(payload_hex: str) -> bytes:
    payload = bytes.fromhex(payload_hex.replace(" ", ""))
    crc = modbus_crc16(payload)
    return payload + bytes((crc & 0xFF, crc >> 8))


def _hex(data: bytes) -> str:
    return " ".join(f"{byte:02X}" for byte in data)


# Read request slave 1, FC03, address 7, count 1 and its 1-register response.
TX_READ = _frame("010300070001")
RX_READ = _frame("010302002A")
# FC06 write and its echo response (identical bytes).
TX_WRITE = _frame("030600800001")


# ─────────────────────── extract_response_frame ───────────────────────


def test_extract_byte_count_response() -> None:
    frame, consumed = extract_response_frame(RX_READ)
    assert frame == RX_READ
    assert consumed == len(RX_READ)


def test_extract_waits_for_incomplete_frame() -> None:
    assert extract_response_frame(RX_READ[:2]) == (None, 0)
    # Slave, function, byte count seen but data still missing.
    assert extract_response_frame(RX_READ[:3]) == (None, 0)
    assert extract_response_frame(RX_READ[:-1]) == (None, 0)


def test_extract_exception_response() -> None:
    exception = _frame("018302")
    assert len(exception) == 5
    frame, consumed = extract_response_frame(exception + b"\xaa\xbb")
    assert frame == exception
    assert consumed == 5


def test_extract_exception_needs_five_bytes() -> None:
    assert extract_response_frame(bytes.fromhex("018302C0")) == (None, 0)


def test_extract_write_echo_response() -> None:
    frame, consumed = extract_response_frame(TX_WRITE)
    assert frame == TX_WRITE
    assert consumed == 8


def test_extract_unknown_function_uses_crc_scan() -> None:
    exotic = _frame("010700")  # FC07 Read Exception Status
    frame, consumed = extract_response_frame(exotic)
    assert frame == exotic
    assert consumed == len(exotic)
    # Nothing extractable while the CRC cannot close a frame.
    assert extract_response_frame(bytes.fromhex("0107")) == (None, 0)


def test_extract_impossible_byte_count_discards_buffer() -> None:
    # byte_count 0xFF would mean a 260-byte frame, above the RTU maximum.
    garbage = bytes.fromhex("0103FF") + bytes(20)
    frame, consumed = extract_response_frame(garbage)
    assert frame is None
    assert consumed == len(garbage)


# ─────────────────────────── ResponseCapture ──────────────────────────


def test_capture_pairs_request_and_response() -> None:
    capture = ResponseCapture()
    assert capture.on_trace(True, TX_READ) == TX_READ
    assert capture.on_trace(False, RX_READ) == RX_READ
    window = capture.consume()
    assert window == {
        "request_hex": _hex(TX_READ),
        "response_hex": _hex(RX_READ),
    }
    # The window is consumed exactly once.
    assert capture.consume() is None


def test_capture_accepts_chunked_async_rx() -> None:
    capture = ResponseCapture()
    capture.on_trace(True, TX_READ)
    capture.on_trace(False, RX_READ[:4])
    assert capture._response is None  # frame not complete yet
    capture.on_trace(False, RX_READ[4:])
    window = capture.consume()
    assert window["response_hex"] == _hex(RX_READ)


def test_capture_normalizes_growing_sync_rx_buffer() -> None:
    # Sync pymodbus re-passes the whole growing buffer on every poll.
    capture = ResponseCapture()
    capture.on_trace(True, TX_READ)
    capture.on_trace(False, RX_READ[:3])
    capture.on_trace(False, RX_READ[:3])  # repeated poll, no new bytes
    capture.on_trace(False, RX_READ[:6])
    capture.on_trace(False, RX_READ)
    window = capture.consume()
    assert window["response_hex"] == _hex(RX_READ)


def test_capture_ignores_trailing_bytes_after_complete_response() -> None:
    capture = ResponseCapture()
    capture.on_trace(True, TX_READ)
    capture.on_trace(False, RX_READ + b"\xde\xad")
    capture.on_trace(False, b"\xbe\xef")
    window = capture.consume()
    assert window["response_hex"] == _hex(RX_READ)


def test_capture_records_request_only_on_timeout() -> None:
    capture = ResponseCapture()
    capture.on_trace(True, TX_READ)
    window = capture.consume()
    assert window["request_hex"] == _hex(TX_READ)
    assert window["response_hex"] is None


def test_capture_records_unsolicited_response() -> None:
    capture = ResponseCapture()
    capture.on_trace(False, RX_READ)
    window = capture.consume()
    assert window["request_hex"] is None
    assert window["response_hex"] == _hex(RX_READ)


def test_capture_new_request_resets_previous_window() -> None:
    capture = ResponseCapture()
    capture.on_trace(True, TX_READ)
    capture.on_trace(False, RX_READ[:4])
    # pymodbus retry: a second TX replaces the incomplete window.
    capture.on_trace(True, TX_WRITE)
    capture.on_trace(False, TX_WRITE)
    window = capture.consume()
    assert window["request_hex"] == _hex(TX_WRITE)
    assert window["response_hex"] == _hex(TX_WRITE)


def test_capture_resets_runaway_noise_buffer() -> None:
    capture = ResponseCapture()
    capture.on_trace(True, TX_READ)
    capture.on_trace(False, bytes(RX_BUFFER_LIMIT + 50))  # zero noise
    assert capture._rx == b""
    # A real response still pairs after the noise reset.
    capture.on_trace(False, RX_READ)
    assert capture.consume()["response_hex"] == _hex(RX_READ)


def test_capture_rejects_non_bytes_payloads() -> None:
    capture = ResponseCapture()
    assert capture.on_trace(True, "not bytes") == "not bytes"
    assert capture.consume() is None


def test_capture_is_thread_safe() -> None:
    capture = ResponseCapture()
    windows: list[dict] = []
    errors: list[Exception] = []

    def worker() -> None:
        try:
            for _ in range(50):
                capture.on_trace(True, TX_READ)
                capture.on_trace(False, RX_READ)
                window = capture.consume()
                if window is not None:
                    windows.append(window)
        except Exception as err:  # pragma: no cover - failure path
            errors.append(err)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    assert windows
    for window in windows:
        assert window["request_hex"] in (_hex(TX_READ), None)
        assert window["response_hex"] in (_hex(RX_READ), None)


# ───────────────────────── logging fallback ───────────────────────────


def test_parse_frame_log_line_modern_style() -> None:
    sending, data = parse_frame_log_line("send: 0x1 0x3 0x0 0x7 0x0 0x1 0x35 0xcb")
    assert sending is True
    assert data == TX_READ
    sending, data = parse_frame_log_line(
        "recv: 0x1 0x3 0x2 0x0 0x2a 0x39 0x9b extra data: "
    )
    assert sending is False
    assert data == RX_READ


def test_parse_frame_log_line_legacy_style() -> None:
    sending, data = parse_frame_log_line("SEND: 01 03 00 07 00 01 35 CB")
    assert sending is True
    assert data == TX_READ
    sending, data = parse_frame_log_line("RECV: 01 03 02 00 2A 39 9B")
    assert sending is False
    assert data == RX_READ


def test_parse_frame_log_line_rejects_non_frames() -> None:
    assert parse_frame_log_line("Connecting to /dev/ttyUSB0:1234.") is None
    assert parse_frame_log_line("send: 0x1 0x3") is None  # too short
    assert parse_frame_log_line("") is None


def test_logging_fallback_captures_frames() -> None:
    logger = logging.getLogger("pymodbus.logging")
    original_level = logger.level
    capture = ResponseCapture()
    try:
        assert capture.install_logging_fallback() is True
        assert logger.level == logging.DEBUG
        logger.debug("send: 0x1 0x3 0x0 0x7 0x0 0x1 0x35 0xcb")
        logger.debug("recv: 0x1 0x3 0x2 0x0 0x2a 0x39 0x9b")
        window = capture.consume()
        assert window == {
            "request_hex": _hex(TX_READ),
            "response_hex": _hex(RX_READ),
        }
    finally:
        capture.detach()
    assert capture._log_handler is None
    assert logger.level == original_level


# ─────────────────────── install_response_capture ─────────────────────


class _FakeManager:
    def __init__(self) -> None:
        self.trace_packet = None


class _FakeSyncClient:
    def __init__(self) -> None:
        self.transaction = _FakeManager()


class _FakeAsyncClient:
    def __init__(self) -> None:
        self.ctx = _FakeManager()


class _FakeLegacyClient:
    trace_packet = None


class _ExplodingClient:
    @property
    def transaction(self):
        raise RuntimeError("boom")


def test_install_hooks_sync_transaction_manager() -> None:
    client = _FakeSyncClient()
    capture = ResponseCapture()
    assert install_response_capture(client, capture) == "trace_packet"
    assert capture.hook == "trace_packet"
    client.transaction.trace_packet(True, TX_READ)
    client.transaction.trace_packet(False, RX_READ)
    assert capture.consume()["response_hex"] == _hex(RX_READ)


def test_install_hooks_async_ctx_manager() -> None:
    client = _FakeAsyncClient()
    capture = ResponseCapture()
    assert install_response_capture(client, capture) == "trace_packet"
    client.ctx.trace_packet(False, RX_READ)
    assert capture.consume()["response_hex"] == _hex(RX_READ)


def test_install_chains_existing_trace_callable() -> None:
    client = _FakeSyncClient()
    seen: list[tuple[bool, bytes]] = []

    def original(sending: bool, data: bytes) -> bytes:
        seen.append((sending, data))
        return data

    client.transaction.trace_packet = original
    capture = ResponseCapture()
    assert install_response_capture(client, capture) == "trace_packet"
    returned = client.transaction.trace_packet(True, TX_READ)
    assert returned == TX_READ
    assert seen == [(True, TX_READ)]
    assert capture.consume()["request_hex"] == _hex(TX_READ)


def test_install_hooks_direct_client_attribute() -> None:
    client = _FakeLegacyClient()
    capture = ResponseCapture()
    assert install_response_capture(client, capture) == "client_trace_packet"
    client.trace_packet(True, TX_WRITE)
    client.trace_packet(False, TX_WRITE)
    window = capture.consume()
    assert window["request_hex"] == window["response_hex"] == _hex(TX_WRITE)


def test_install_falls_back_to_logging() -> None:
    logger = logging.getLogger("pymodbus.logging")
    handlers_before = list(logger.handlers)

    class _OpaqueClient:
        pass

    capture = ResponseCapture()
    try:
        assert install_response_capture(_OpaqueClient(), capture) == "logging"
        assert any(handler not in handlers_before for handler in logger.handlers)
    finally:
        capture.detach()
    assert logger.handlers == handlers_before


def test_install_never_raises_on_broken_client() -> None:
    capture = ResponseCapture()
    assert install_response_capture(_ExplodingClient(), capture) is None
    assert capture.hook is None


def test_install_on_real_pymodbus_serial_client() -> None:
    """The hook must attach to the pymodbus version pinned for tests."""
    pytest.importorskip("pymodbus")
    from pymodbus.client import ModbusSerialClient

    client = ModbusSerialClient(port="/dev/ttyUSB-test", baudrate=9600)
    capture = ResponseCapture()
    try:
        hook = install_response_capture(client, capture)
        assert hook in ("trace_packet", "logging")
        if hook == "trace_packet":
            manager = getattr(client, "transaction", None) or client.ctx
            manager.trace_packet(True, TX_READ)
            manager.trace_packet(False, RX_READ)
            window = capture.consume()
            assert window["response_hex"] == _hex(RX_READ)
    finally:
        capture.detach()
