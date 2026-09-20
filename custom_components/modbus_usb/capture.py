"""Real Modbus RTU response capture through pymodbus transaction tracing.

Pymodbus does not retain raw RTU bytes in its result objects, so before
v2.6.0 the Traffic Inspector could only show *reconstructed* request frames.
This module hooks pymodbus transaction tracing (``trace_packet`` on modern
releases, the ``pymodbus.logging`` frame dumps on older ones) and records the
actual TX and RX bytes of every serial transaction.

Since v2.7.0 the capture keeps the *full* raw RX stream of one coordinator
transaction, already split into complete RTU frames: a batch write or a
board block reader that performs several serial exchanges still produces
one log entry, and every response frame that traveled for it (write
echoes, the frames of a batch read, an exception plus a follow-up read)
is captured and handed to the inspector as a list of decoded frames.

Since v2.7.1 every captured response frame also carries its *arrival time*
(milliseconds since the transaction window opened, i.e. since the first TX
of the operation), so the inspector can render the inter-frame gaps of a
multi-frame stream as a mini waterfall and exports can list them per frame.

The frame extraction logic is pure and Home Assistant-free so it stays
trivially unit-testable: response length is derived from the received bytes
themselves (exception frames, byte-count frames, and write echoes) exactly
like a Modbus RTU master would.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable
from typing import Any

from .diagnostics import modbus_crc16

# Modbus RTU frames never exceed 256 bytes (253-byte PDU + slave + 2 CRC).
MAX_RTU_FRAME_LENGTH = 256
# Safety cap for the per-transaction receive buffer when frames never
# complete (bus noise, baud mismatch); the buffer is reset at this size.
RX_BUFFER_LIMIT = 1024
# Safety cap for the number of complete response frames kept per coordinator
# transaction. Batch writes of N items produce N echo frames; the cap keeps
# the log bounded when a runaway device answers a batch with a flood of
# frames.
MAX_RESPONSE_FRAMES = 64

_READ_FUNCTIONS = frozenset({0x01, 0x02, 0x03, 0x04})
_ECHO_RESPONSE_FUNCTIONS = frozenset({0x05, 0x06, 0x0F, 0x10})

PYMODBUS_LOGGER_NAME = "pymodbus.logging"

# Minimum hex tokens a log line must carry before the logging fallback
# treats it as a frame dump (a real frame is at least 4 bytes long).
_MIN_LOG_FRAME_TOKENS = 4

# "send: 0x1 0x3 ..." (pymodbus >= 3.7) or "SEND: 01 03 ..." (older).
_FRAME_LOG_RE = re.compile(
    r"^(?P<direction>send|recv|tx|rx)\b[^0-9a-fA-Fx]*"
    r"(?P<bytes>(?:0x[0-9a-fA-F]{1,2}[\s,]*)+|(?:(?:[0-9a-fA-F]{2})[\s,]*)+)",
    re.IGNORECASE,
)
_HEX_TOKEN_RE = re.compile(r"0x([0-9a-fA-F]{1,2})|([0-9a-fA-F]{2})")


def _format_bytes(data: bytes) -> str:
    return " ".join(f"{byte:02X}" for byte in data)


def _crc_scan_length(rx_buffer: bytes) -> int | None:
    """Locate a frame end for an unknown function code by CRC validation."""
    maximum = min(len(rx_buffer), MAX_RTU_FRAME_LENGTH)
    for length in range(4, maximum + 1):
        candidate = rx_buffer[:length]
        if modbus_crc16(candidate[:-2]) == (candidate[-2] | (candidate[-1] << 8)):
            return length
    return None


def extract_response_frame(rx_buffer: bytes) -> tuple[bytes | None, int]:
    """Return ``(frame, consumed)`` once the buffer starts with a full response.

    Length determination mirrors the Modbus RTU specification:

    - exception responses (function code high bit set) are always 5 bytes;
    - FC01–FC04 responses carry a byte count at offset 2, so the complete
      frame is ``3 + byte_count + 2`` bytes long;
    - FC05/FC06/FC0F/FC10 responses echo the 8-byte request;
    - unknown function codes fall back to a CRC scan.

    Returns ``(None, 0)`` while the frame is still incomplete.
    """
    if len(rx_buffer) < 3:
        return None, 0
    function_code = rx_buffer[1]
    if function_code >= 0x80:
        expected = 5
    elif function_code in _READ_FUNCTIONS:
        expected = 5 + rx_buffer[2]
    elif function_code in _ECHO_RESPONSE_FUNCTIONS:
        expected = 8
    else:
        expected = _crc_scan_length(rx_buffer) or 0
        if not expected:
            return None, 0
    if expected > MAX_RTU_FRAME_LENGTH:
        # A declared byte count that can never form a legal frame means the
        # buffer is out of sync; drop it so the next chunk starts fresh.
        return None, len(rx_buffer)
    if len(rx_buffer) < expected:
        return None, 0
    return rx_buffer[:expected], expected


def extract_response_frames(rx_buffer: bytes) -> tuple[list[bytes], int]:
    """Split the start of a receive buffer into complete RTU response frames.

    Returns ``(frames, consumed)`` where ``frames`` is the list of complete
    frames found at the start of the buffer (in arrival order, capped at
    ``MAX_RESPONSE_FRAMES``) and ``consumed`` is the length of the buffer
    prefix that is accounted for — complete frames plus any desynced
    garbage that had to be dropped. ``rx_buffer[consumed:]`` is the part
    that is still being received and must be kept for the next chunk.

    Extraction stops at the first incomplete frame, so a partial tail stays
    in the buffer; a desync (declared byte count above the RTU maximum)
    drops everything behind it.
    """
    frames: list[bytes] = []
    offset = 0
    while offset < len(rx_buffer) and len(frames) < MAX_RESPONSE_FRAMES:
        frame, consumed = extract_response_frame(rx_buffer[offset:])
        if frame is not None:
            frames.append(frame)
        offset += consumed
        if frame is None:
            # Either still incomplete (consumed == 0) or desynced garbage
            # that was dropped (consumed > 0); both end this pass.
            break
    return frames, offset


class ResponseCapture:
    """Records the raw TX/RX bytes of the most recent serial transaction.

    A *window* runs from ``consume`` until the next ``consume`` and covers
    one whole coordinator transaction — including batch operations that
    perform several serial exchanges before a single log entry is recorded.
    Each TX frame updates the recorded request (the last TX wins, mirroring
    the single ``request_hex`` field of the log entry), while every
    complete response frame received for the window is kept, so multi-frame
    responses (batch reads, block readers, exception plus follow-up) arrive
    at the inspector as a list instead of only the last pair.

    Every complete frame is stamped with its arrival time (the trace call
    that completed it) relative to the moment the window opened, so the
    per-frame timing of a multi-frame stream survives into the log entry.
    ``clock`` defaults to ``time.monotonic`` and is injectable for tests.

    The class is thread-safe: pymodbus tracing may run on the serial
    executor thread while ``consume`` is called by the coordinator when it
    records the finished transaction.
    """

    def __init__(self, clock: Callable[[], float] | None = None) -> None:
        self._lock = threading.Lock()
        self._clock = clock or time.monotonic
        self._tx: bytes | None = None
        self._rx = b""
        # Offset into ``_rx`` up to which bytes have already been framed
        # (or dropped as desync). pymodbus re-passes growing buffers, so the
        # same bytes must never be framed twice.
        self._rx_offset = 0
        # Set when a TX frame arrived and no RX bytes merged since; used to
        # tell a fresh (possibly identical) response frame apart from a
        # stale re-pass of an already fully framed buffer.
        self._tx_pending = False
        self._frames: list[bytes] = []
        # Arrival time of every frame in ``_frames`` (same index), in
        # seconds on ``clock``; appended in lockstep so the frame cap bounds
        # both lists.
        self._frame_times: list[float] = []
        # Clock reading when the current window opened (first TX, or the
        # first RX byte when no TX was observed); None between windows.
        self._window_start: float | None = None
        # Last complete frame of the window; kept for callers that only
        # care about the most recent response (backward compatibility).
        self._response: bytes | None = None
        self._active = False
        # Name of the installed hook ("trace_packet", "client_trace_packet",
        # or "logging"); None until install_response_capture succeeds.
        self.hook: str | None = None
        self._log_handler: logging.Handler | None = None
        self._previous_log_level: int | None = None

    # ── tracing entry point ────────────────────────────────────────────

    def on_trace(self, sending: bool, data: bytes) -> bytes:
        """Consume one pymodbus trace call; always return ``data`` unchanged.

        Sync pymodbus passes the *growing* receive buffer on every poll and
        resets it after each consumed frame; async pymodbus passes only the
        new chunk. Both shapes are normalized into one per-transaction
        buffer that is reframed on every chunk, so a single coordinator
        transaction keeps its full raw RX stream.
        """
        try:
            chunk = bytes(data)
        except TypeError:
            return data
        now = self._clock()
        with self._lock:
            if sending:
                # A new request updates the recorded TX frame. The receive
                # window stays open until consume() so multi-frame
                # transactions (batch writes, board block readers) keep
                # every response frame of the whole operation — but any
                # still-incomplete RX tail belongs to the previous request
                # and is dropped, exactly like pymodbus's per-request
                # receive buffer.
                self._tx = chunk
                self._tx_pending = True
                if not self._active:
                    self._window_start = now
                self._active = True
                self._rx = self._rx[: self._rx_offset]
                return data
            if not self._active:
                # RX observed without a TX (hook installed mid-transaction).
                self._active = True
                self._window_start = now
            self._merge_rx(chunk, now)
        return data

    def _merge_rx(self, chunk: bytes, now: float) -> None:
        """Merge one RX chunk into the per-transaction buffer and reframe it.

        ``now`` is the clock reading of the trace call delivering ``chunk``;
        every frame completed by this chunk is stamped with it.
        """
        if not chunk:
            return
        fully_consumed = self._rx_offset == len(self._rx)
        if fully_consumed and self._rx:
            if chunk == self._rx and not self._tx_pending:
                # Stale re-pass of an already fully framed buffer (some
                # pymodbus revisions re-trace the buffer after the framer
                # consumed it); no new bytes arrived.
                return
            # The previous buffer is fully framed; start fresh so a new
            # frame — even one identical to the previous echo — is not
            # mistaken for a re-pass of the old buffer.
            self._rx = b""
            self._rx_offset = 0
        self._tx_pending = False
        if chunk.startswith(self._rx):
            # Sync pymodbus re-passes the growing buffer on every poll.
            self._rx = chunk
        elif self._rx.startswith(chunk):
            return  # stale re-send of a shorter buffer prefix
        elif chunk in self._rx:
            return  # re-send of an already-buffered chunk; nothing new
        else:
            self._rx += chunk

        frames, consumed = extract_response_frames(self._rx[self._rx_offset :])
        for frame in frames:
            if len(self._frames) >= MAX_RESPONSE_FRAMES:
                break
            self._frames.append(frame)
            self._frame_times.append(now)
            self._response = frame
        self._rx_offset += consumed
        if len(self._frames) >= MAX_RESPONSE_FRAMES:
            # Frame cap reached; stop buffering the rest of this window.
            self._rx = b""
            self._rx_offset = 0
        elif len(self._rx) - self._rx_offset > RX_BUFFER_LIMIT:
            # Runaway noise in the not-yet-framed part; drop it, keep frames.
            self._rx = self._rx[: self._rx_offset]

    # ── coordinator entry point ────────────────────────────────────────

    def consume(self) -> dict[str, Any] | None:
        """Pop the recorded TX/RX stream for the transaction that just ended.

        Returns a dictionary with:

        - ``request_hex``: the last TX frame of the window (hex string) —
          for batch operations this is the final serial request;
        - ``response_hex``: the last complete response frame, matching the
          pre-v2.7.0 behaviour;
        - ``response_frames``: every complete response frame of the window
          (list of hex strings, oldest first), or None when no frame was
          captured;
        - ``response_frame_times_ms``: the arrival time of every frame in
          ``response_frames`` (same index), in milliseconds since the window
          opened — the first TX of the operation, or the first RX byte when
          no TX was traced — or None when no frame was captured. Consecutive
          differences are the inter-frame gaps of the stream.

        Returns None when no serial I/O was observed since the previous
        consume, so recorded bookkeeping entries never adopt stale frames.
        """
        with self._lock:
            if not self._active:
                return None
            start = self._window_start
            window: dict[str, Any] = {
                "request_hex": _format_bytes(self._tx) if self._tx else None,
                "response_hex": (
                    _format_bytes(self._response) if self._response else None
                ),
                "response_frames": (
                    [_format_bytes(frame) for frame in self._frames]
                    if self._frames
                    else None
                ),
                "response_frame_times_ms": (
                    [
                        round(max(0.0, (stamp - start) * 1000.0), 3)
                        for stamp in self._frame_times
                    ]
                    if self._frames and start is not None
                    else None
                ),
            }
            self._tx = None
            self._rx = b""
            self._rx_offset = 0
            self._tx_pending = False
            self._frames = []
            self._frame_times = []
            self._window_start = None
            self._response = None
            self._active = False
            return window

    # ── logging fallback (pymodbus without trace_packet) ───────────────

    def install_logging_fallback(self) -> bool:
        """Capture frames from pymodbus debug log lines instead of tracing."""
        logger = logging.getLogger(PYMODBUS_LOGGER_NAME)
        handler = _FrameLogCaptureHandler(self)
        logger.addHandler(handler)
        self._log_handler = handler
        if logger.level == logging.NOTSET or logger.level > logging.DEBUG:
            self._previous_log_level = logger.level
            logger.setLevel(logging.DEBUG)
        return True

    def detach(self) -> None:
        """Remove a logging fallback handler and restore the logger level."""
        if self._log_handler is not None:
            logging.getLogger(PYMODBUS_LOGGER_NAME).removeHandler(self._log_handler)
            self._log_handler = None
        if self._previous_log_level is not None:
            logging.getLogger(PYMODBUS_LOGGER_NAME).setLevel(self._previous_log_level)
            self._previous_log_level = None
        self.hook = None


def parse_frame_log_line(text: str) -> tuple[bool, bytes] | None:
    """Extract ``(sending, frame_bytes)`` from one pymodbus frame log line.

    Handles both hex styles pymodbus has used for frame dumps: ``0x``-prefixed
    tokens with one or two digits and bare two-digit hex pairs. Returns None
    for lines that are not frame dumps.
    """
    match = _FRAME_LOG_RE.match(text.strip())
    if not match:
        return None
    tokens = [
        prefixed or bare
        for prefixed, bare in _HEX_TOKEN_RE.findall(match.group("bytes"))
        if prefixed or bare
    ]
    if len(tokens) < _MIN_LOG_FRAME_TOKENS:
        return None
    try:
        data = bytes(int(token, 16) for token in tokens)
    except ValueError:
        return None
    sending = match.group("direction").lower() in ("send", "tx")
    return sending, data


class _FrameLogCaptureHandler(logging.Handler):
    """Feeds pymodbus frame log lines into a ResponseCapture window."""

    def __init__(self, capture: ResponseCapture) -> None:
        super().__init__(logging.DEBUG)
        self.capture = capture

    def emit(self, record: logging.LogRecord) -> None:
        try:
            parsed = parse_frame_log_line(record.getMessage())
        except Exception:  # a logging hook must never break serial I/O
            return
        if parsed is not None:
            sending, data = parsed
            self.capture.on_trace(sending, data)


def _chained_trace(capture: ResponseCapture, original: Any) -> Any:
    """Wrap an existing trace_packet callable without losing its behaviour."""

    def _trace(sending: bool, data: bytes) -> bytes:
        captured = capture.on_trace(sending, data)
        if callable(original):
            return original(sending, captured)
        return captured

    return _trace


def install_response_capture(client: Any, capture: ResponseCapture) -> str | None:
    """Hook one pymodbus client so ``capture`` sees its raw TX/RX bytes.

    Tries, in order:

    1. ``client.transaction.trace_packet`` / ``client.ctx.trace_packet`` —
       the transaction-manager trace hook used by pymodbus >= 3.7 (sync
       clients expose the manager as ``transaction``, async ones as ``ctx``);
    2. ``client.trace_packet`` — clients exposing the hook directly;
    3. the ``pymodbus.logging`` frame dumps — the only source of raw bytes on
       older pymodbus releases.

    Returns the name of the installed hook, or None when tracing is
    unavailable. Never raises: response capture is a diagnostic nicety and
    must not jeopardize serial communication.
    """
    try:
        for manager_name in ("transaction", "ctx"):
            manager = getattr(client, manager_name, None)
            if manager is not None and hasattr(manager, "trace_packet"):
                manager.trace_packet = _chained_trace(capture, manager.trace_packet)
                capture.hook = "trace_packet"
                return capture.hook
        if hasattr(client, "trace_packet"):
            client.trace_packet = _chained_trace(capture, client.trace_packet)
            capture.hook = "client_trace_packet"
            return capture.hook
        if capture.install_logging_fallback():
            capture.hook = "logging"
            return capture.hook
    except Exception:
        return None
    return None
