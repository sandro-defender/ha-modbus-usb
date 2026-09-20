"""Real Modbus RTU response capture through pymodbus transaction tracing.

Pymodbus does not retain raw RTU bytes in its result objects, so before
v2.6.0 the Traffic Inspector could only show *reconstructed* request frames.
This module hooks pymodbus transaction tracing (``trace_packet`` on modern
releases, the ``pymodbus.logging`` frame dumps on older ones) and records the
actual TX and RX bytes of every serial transaction.

The frame extraction logic is pure and Home Assistant-free so it stays
trivially unit-testable: response length is derived from the received bytes
themselves (exception frames, byte-count frames, and write echoes) exactly
like a Modbus RTU master would.
"""

from __future__ import annotations

import logging
import re
import threading
from typing import Any

from .diagnostics import modbus_crc16

# Modbus RTU frames never exceed 256 bytes (253-byte PDU + slave + 2 CRC).
MAX_RTU_FRAME_LENGTH = 256
# Safety cap for the per-transaction receive buffer when frames never
# complete (bus noise, baud mismatch); the buffer is reset at this size.
RX_BUFFER_LIMIT = 1024

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


class ResponseCapture:
    """Records the raw TX/RX bytes of the most recent serial transaction.

    The class is thread-safe: pymodbus tracing may run on the serial
    executor thread while ``consume`` is called by the coordinator when it
    records the finished transaction.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tx: bytes | None = None
        self._rx = b""
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

        Sync pymodbus passes the *growing* receive buffer on every poll,
        async pymodbus passes only the new chunk. Both shapes are normalized
        into one per-transaction buffer before frame extraction.
        """
        try:
            chunk = bytes(data)
        except TypeError:
            return data
        with self._lock:
            if sending:
                # A new request opens a new capture window.
                self._tx = chunk
                self._rx = b""
                self._response = None
                self._active = True
                return data
            if not self._active:
                # RX observed without a TX (hook installed mid-transaction).
                self._active = True
            if self._response is not None:
                # One request/response pair is already complete; ignore
                # trailing bytes until the next TX frame.
                return data
            if chunk.startswith(self._rx):
                self._rx = chunk
            elif self._rx.startswith(chunk):
                pass  # stale re-send of a shorter buffer prefix
            else:
                self._rx += chunk
            frame, consumed = extract_response_frame(self._rx)
            if frame is not None:
                self._response = frame
                self._rx = self._rx[consumed:]
            elif consumed:
                self._rx = self._rx[consumed:]  # unrecoverable desync; drop
            elif len(self._rx) > RX_BUFFER_LIMIT:
                self._rx = b""  # runaway noise guard
        return data

    # ── coordinator entry point ────────────────────────────────────────

    def consume(self) -> dict[str, Any] | None:
        """Pop the recorded TX/RX pair for the transaction that just ended.

        Returns None when no serial I/O was observed since the previous
        consume, so recorded bookkeeping entries never adopt stale frames.
        """
        with self._lock:
            if not self._active:
                return None
            window = {
                "request_hex": _format_bytes(self._tx) if self._tx else None,
                "response_hex": (
                    _format_bytes(self._response) if self._response else None
                ),
            }
            self._tx = None
            self._rx = b""
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
