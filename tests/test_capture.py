"""Tests for real RTU response capture through pymodbus transaction tracing."""

from __future__ import annotations

import logging
import threading

import pytest

from custom_components.modbus_usb.capture import (
    MAX_RESPONSE_FRAMES,
    RX_BUFFER_LIMIT,
    ResponseCapture,
    extract_response_frame,
    extract_response_frames,
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
        "response_frames": [_hex(RX_READ)],
        "response_frame_times_ms": [
            pytest.approx(window["response_frame_times_ms"][0])
        ],
    }
    assert len(window["response_frame_times_ms"]) == 1
    assert window["response_frame_times_ms"][0] >= 0.0
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
        assert window["request_hex"] == _hex(TX_READ)
        assert window["response_hex"] == _hex(RX_READ)
        assert window["response_frames"] == [_hex(RX_READ)]
        assert len(window["response_frame_times_ms"]) == 1
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


# ───────────────── v2.7.0: multi-frame RX stream capture ─────────────────


def test_extract_frames_splits_back_to_back_responses() -> None:
    frames, consumed = extract_response_frames(RX_READ + TX_WRITE)
    assert frames == [RX_READ, TX_WRITE]
    assert consumed == len(RX_READ) + len(TX_WRITE)


def test_extract_frames_keeps_incomplete_tail() -> None:
    buffer = RX_READ + TX_WRITE[:-1]  # second frame missing its last byte
    frames, consumed = extract_response_frames(buffer)
    assert frames == [RX_READ]
    assert consumed == len(RX_READ)


def test_extract_frames_empty_and_short_buffers() -> None:
    assert extract_response_frames(b"") == ([], 0)
    assert extract_response_frames(bytes.fromhex("01 03".replace(" ", ""))) == ([], 0)


def test_extract_frames_drops_desync_after_first_frame() -> None:
    # First frame is fine, second one declares an impossible byte count.
    garbage = bytes.fromhex("0103FF") + bytes(20)
    frames, consumed = extract_response_frames(RX_READ + garbage)
    assert frames == [RX_READ]
    assert consumed == len(RX_READ) + len(garbage)


def test_extract_frames_unknown_function_in_stream() -> None:
    # A CRC-scanned exotic frame followed by a standard response.
    exotic = _frame("010700")
    frames, consumed = extract_response_frames(exotic + RX_READ)
    assert frames == [exotic, RX_READ]
    assert consumed == len(exotic) + len(RX_READ)


def test_extract_frames_caps_at_max() -> None:
    buffer = TX_WRITE * (MAX_RESPONSE_FRAMES + 5)
    frames, consumed = extract_response_frames(buffer)
    assert len(frames) == MAX_RESPONSE_FRAMES
    assert frames == [TX_WRITE] * MAX_RESPONSE_FRAMES
    assert consumed == len(TX_WRITE) * MAX_RESPONSE_FRAMES


# Batch-read style stream: one TX, then two complete read responses that
# arrive as fresh (async-style) chunks straddling a frame boundary.
RX_A = _frame("02040400 0100 02".replace(" ", ""))
RX_B = _frame("020302 002A".replace(" ", ""))


def test_capture_multi_frame_async_chunks() -> None:
    capture = ResponseCapture()
    capture.on_trace(True, TX_READ)
    # First frame arrives in two chunks.
    capture.on_trace(False, RX_A[:5])
    capture.on_trace(False, RX_A[5:])
    # Second frame arrives whole, after the first was consumed.
    capture.on_trace(False, RX_B)
    window = capture.consume()
    assert window["request_hex"] == _hex(TX_READ)
    assert window["response_hex"] == _hex(RX_B)  # last frame, as before
    assert window["response_frames"] == [_hex(RX_A), _hex(RX_B)]


def test_capture_multi_frame_sync_growing_buffers() -> None:
    # pymodbus 3.x sync behaviour: the buffer grows per poll and is reset
    # after each consumed frame, so the second frame arrives as a fresh
    # buffer (not an extension of the first one's).
    capture = ResponseCapture()
    capture.on_trace(True, TX_READ)
    capture.on_trace(False, RX_A[:4])
    capture.on_trace(False, RX_A)  # first frame complete
    capture.on_trace(False, RX_B[:3])
    capture.on_trace(False, RX_B)  # second frame, fresh buffer
    window = capture.consume()
    assert window["response_frames"] == [_hex(RX_A), _hex(RX_B)]
    assert window["response_hex"] == _hex(RX_B)


def test_capture_batch_identical_echo_frames() -> None:
    # A batch writing the same register/value produces identical echo
    # frames; each new TX must not make the identical RX look stale.
    capture = ResponseCapture()
    capture.on_trace(True, TX_WRITE)
    capture.on_trace(False, TX_WRITE)
    capture.on_trace(True, TX_WRITE)
    capture.on_trace(False, TX_WRITE)
    window = capture.consume()
    assert window["request_hex"] == _hex(TX_WRITE)
    assert window["response_frames"] == [_hex(TX_WRITE), _hex(TX_WRITE)]


def test_capture_stale_repass_of_consumed_buffer_is_ignored() -> None:
    # Defensively: a re-trace of the same buffer right after the frame was
    # consumed, with no new TX in between, adds no frame.
    capture = ResponseCapture()
    capture.on_trace(True, TX_READ)
    capture.on_trace(False, RX_READ)
    capture.on_trace(False, RX_READ)  # identical re-pass, no new TX
    window = capture.consume()
    assert window["response_frames"] == [_hex(RX_READ)]


def test_capture_exception_then_followup_frame() -> None:
    exception = _frame("028302")
    capture = ResponseCapture()
    capture.on_trace(True, _frame("0203000A0001"))
    capture.on_trace(False, exception)
    # Board retries with a second request inside the same coordinator call.
    capture.on_trace(True, _frame("020300010001"))
    capture.on_trace(False, _frame("020302002A"))
    window = capture.consume()
    assert window["response_frames"] == [
        _hex(exception),
        _hex(_frame("020302002A")),
    ]


def test_capture_drops_unframed_tail_on_new_tx() -> None:
    capture = ResponseCapture()
    capture.on_trace(True, TX_READ)
    capture.on_trace(False, RX_READ[:4])  # partial, never completed
    capture.on_trace(True, TX_WRITE)
    capture.on_trace(False, TX_WRITE)
    window = capture.consume()
    # The stale partial bytes must not corrupt the second exchange.
    assert window["request_hex"] == _hex(TX_WRITE)
    assert window["response_frames"] == [_hex(TX_WRITE)]


def test_capture_trailing_partial_frame_is_not_recorded() -> None:
    capture = ResponseCapture()
    capture.on_trace(True, TX_READ)
    capture.on_trace(False, RX_A + RX_B[:4])
    window = capture.consume()
    assert window["response_frames"] == [_hex(RX_A)]
    assert window["response_hex"] == _hex(RX_A)


def test_capture_frame_cap_stops_recording() -> None:
    capture = ResponseCapture()
    for _ in range(MAX_RESPONSE_FRAMES + 10):
        capture.on_trace(True, TX_WRITE)
        capture.on_trace(False, TX_WRITE)
    window = capture.consume()
    assert len(window["response_frames"]) == MAX_RESPONSE_FRAMES
    assert window["response_frames"] == [_hex(TX_WRITE)] * MAX_RESPONSE_FRAMES
    # The buffer is reset at the cap, so the window stays bounded.
    assert capture._rx == b""


def test_capture_window_spans_batch_until_consume() -> None:
    # One coordinator batch: two serial exchanges, one consume() — the full
    # RX stream of the transaction is returned, not only the last pair.
    capture = ResponseCapture()
    capture.on_trace(True, _frame("010600010002"))
    capture.on_trace(False, _frame("010600010002"))
    capture.on_trace(True, _frame("010600020004"))
    capture.on_trace(False, _frame("010600020004"))
    window = capture.consume()
    assert window["request_hex"] == _hex(_frame("010600020004"))
    assert window["response_frames"] == [
        _hex(_frame("010600010002")),
        _hex(_frame("010600020004")),
    ]
    assert capture.consume() is None


def test_capture_runaway_noise_between_frames() -> None:
    # Noise in the not-yet-framed part resets only that part; earlier
    # frames survive, and a real frame after the noise is still paired.
    capture = ResponseCapture()
    capture.on_trace(True, TX_READ)
    capture.on_trace(False, RX_A)
    capture.on_trace(False, bytes(RX_BUFFER_LIMIT + 50))
    assert capture._rx == b""
    capture.on_trace(False, RX_B)
    window = capture.consume()
    assert window["response_frames"] == [_hex(RX_A), _hex(RX_B)]


# ───────────────── v2.7.1: per-frame RX arrival timestamps ─────────────────


class _FakeClock:
    """Deterministic monotonic clock: ``tick()`` advances by ``step`` seconds."""

    def __init__(self, start: float = 100.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_capture_records_frame_arrival_relative_to_first_tx() -> None:
    clock = _FakeClock()
    capture = ResponseCapture(clock=clock)
    capture.on_trace(True, TX_READ)  # window opens at t=0
    clock.advance(0.0125)
    capture.on_trace(False, RX_READ)  # arrives 12.5 ms later
    window = capture.consume()
    assert window["response_frames"] == [_hex(RX_READ)]
    assert window["response_frame_times_ms"] == [12.5]


def test_capture_multi_frame_offsets_span_the_whole_batch() -> None:
    # Batch: TX1 t=0, RX1 t=15 ms, TX2 t=20 ms, RX2 t=35 ms. Offsets stay
    # relative to the *first* TX of the window, so the inter-frame gap
    # (RX1 -> RX2) is simply the difference of consecutive entries.
    clock = _FakeClock()
    capture = ResponseCapture(clock=clock)
    tx_one = _frame("010600010002")
    tx_two = _frame("010600020004")
    capture.on_trace(True, tx_one)
    clock.advance(0.015)
    capture.on_trace(False, tx_one)
    clock.advance(0.005)
    capture.on_trace(True, tx_two)
    clock.advance(0.015)
    capture.on_trace(False, tx_two)
    window = capture.consume()
    assert window["response_frames"] == [_hex(tx_one), _hex(tx_two)]
    assert window["response_frame_times_ms"] == [15.0, 35.0]


def test_capture_chunked_frame_is_stamped_when_completed() -> None:
    clock = _FakeClock()
    capture = ResponseCapture(clock=clock)
    capture.on_trace(True, TX_READ)
    clock.advance(0.004)
    capture.on_trace(False, RX_READ[:4])  # partial: no frame yet
    assert capture._frame_times == []
    clock.advance(0.006)
    capture.on_trace(False, RX_READ[4:])  # completes at t=10 ms
    window = capture.consume()
    assert window["response_frame_times_ms"] == [10.0]


def test_capture_frames_completed_by_one_chunk_share_a_timestamp() -> None:
    clock = _FakeClock()
    capture = ResponseCapture(clock=clock)
    capture.on_trace(True, TX_READ)
    clock.advance(0.02)
    capture.on_trace(False, RX_A + RX_B)  # two complete frames in one chunk
    window = capture.consume()
    assert window["response_frames"] == [_hex(RX_A), _hex(RX_B)]
    assert window["response_frame_times_ms"] == [20.0, 20.0]


def test_capture_timestamps_are_bounded_by_frame_cap() -> None:
    clock = _FakeClock()
    capture = ResponseCapture(clock=clock)
    for _ in range(MAX_RESPONSE_FRAMES + 10):
        capture.on_trace(True, TX_WRITE)
        clock.advance(0.001)
        capture.on_trace(False, TX_WRITE)
    assert len(capture._frame_times) == len(capture._frames) == MAX_RESPONSE_FRAMES
    window = capture.consume()
    assert len(window["response_frame_times_ms"]) == MAX_RESPONSE_FRAMES
    # Offsets are monotonic non-decreasing across the batch.
    times = window["response_frame_times_ms"]
    assert all(
        later >= earlier for earlier, later in zip(times, times[1:], strict=False)
    )


def test_capture_timestamps_reset_between_windows() -> None:
    clock = _FakeClock()
    capture = ResponseCapture(clock=clock)
    capture.on_trace(True, TX_READ)
    clock.advance(0.05)
    capture.on_trace(False, RX_READ)
    assert capture.consume()["response_frame_times_ms"] == [50.0]
    assert capture._frame_times == []
    assert capture._window_start is None
    # The next window starts a fresh clock origin at its own first TX.
    clock.advance(10.0)
    capture.on_trace(True, TX_READ)
    clock.advance(0.007)
    capture.on_trace(False, RX_READ)
    assert capture.consume()["response_frame_times_ms"] == [7.0]


def test_capture_unsolicited_rx_opens_window_at_first_byte() -> None:
    clock = _FakeClock()
    capture = ResponseCapture(clock=clock)
    clock.advance(1.0)
    capture.on_trace(False, RX_READ)  # no TX traced: window opens here
    window = capture.consume()
    assert window["request_hex"] is None
    assert window["response_frame_times_ms"] == [0.0]


def test_capture_request_only_window_has_no_timestamps() -> None:
    capture = ResponseCapture(clock=_FakeClock())
    capture.on_trace(True, TX_READ)
    window = capture.consume()
    assert window["response_frames"] is None
    assert window["response_frame_times_ms"] is None


def test_capture_stale_repass_adds_no_timestamp() -> None:
    clock = _FakeClock()
    capture = ResponseCapture(clock=clock)
    capture.on_trace(True, TX_READ)
    clock.advance(0.01)
    capture.on_trace(False, RX_READ)
    clock.advance(0.01)
    capture.on_trace(False, RX_READ)  # identical re-pass, no new TX
    window = capture.consume()
    assert window["response_frames"] == [_hex(RX_READ)]
    assert window["response_frame_times_ms"] == [10.0]


def test_capture_timestamps_never_go_negative() -> None:
    # A clock that (theoretically) steps backwards must not produce
    # negative offsets; they are clamped at zero.
    clock = _FakeClock()
    capture = ResponseCapture(clock=clock)
    capture.on_trace(True, TX_READ)
    clock.advance(-0.5)
    capture.on_trace(False, RX_READ)
    assert capture.consume()["response_frame_times_ms"] == [0.0]


def test_capture_default_clock_is_monotonic() -> None:
    capture = ResponseCapture()
    capture.on_trace(True, TX_READ)
    capture.on_trace(False, RX_READ)
    window = capture.consume()
    times = window["response_frame_times_ms"]
    assert len(times) == 1
    assert 0.0 <= times[0] < 1000.0


def test_capture_timestamps_thread_safe_with_concurrent_traces() -> None:
    capture = ResponseCapture()
    errors: list[Exception] = []
    windows: list[dict] = []

    def worker() -> None:
        try:
            for _ in range(100):
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
    for window in windows:
        frames = window["response_frames"]
        times = window["response_frame_times_ms"]
        # Frames and timestamps always stay aligned, whatever the interleaving.
        assert (frames is None) == (times is None)
        if frames is not None:
            assert len(frames) == len(times)
            assert all(stamp >= 0.0 for stamp in times)
