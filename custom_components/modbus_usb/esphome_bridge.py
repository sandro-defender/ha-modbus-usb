"""Pure Modbus RTU framing and request/response pairing for the ESPHome API hub.

The ESPHome native-API transport cannot use pymodbus's own transaction
machinery (there is no byte stream — frames travel as service calls and HA
events), so this module provides the minimal, fully unit-testable pieces:

- :func:`build_rtu_request` — encode FC01–FC06 / FC0F / FC10 requests with CRC;
- :func:`parse_rtu_response` — validate and decode a reply into a
  :class:`ModbusResponse` exposing the attributes the coordinator reads from
  pymodbus results (``registers``, ``bits``, ``isError()``, ``exception_code``,
  ``function_code``);
- :class:`FramePairer` — a thread-safe, bounded mailbox that hands the reply
  of the single in-flight request to its waiter and drops stale or
  unsolicited frames.

No Home Assistant, pymodbus, or aioesphomeapi imports live here.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from .diagnostics import modbus_crc16

FC_READ_COILS = 0x01
FC_READ_DISCRETE = 0x02
FC_READ_HOLDING = 0x03
FC_READ_INPUT = 0x04
FC_WRITE_COIL = 0x05
FC_WRITE_REGISTER = 0x06
FC_WRITE_COILS = 0x0F
FC_WRITE_REGISTERS = 0x10

_READ_FUNCTIONS = frozenset(
    {FC_READ_COILS, FC_READ_DISCRETE, FC_READ_HOLDING, FC_READ_INPUT}
)
_BIT_FUNCTIONS = frozenset({FC_READ_COILS, FC_READ_DISCRETE})
_ECHO_FUNCTIONS = frozenset(
    {FC_WRITE_COIL, FC_WRITE_REGISTER, FC_WRITE_COILS, FC_WRITE_REGISTERS}
)

MAX_REGISTERS_PER_READ = 125
MAX_COILS_PER_READ = 2000
MAX_REGISTERS_PER_WRITE = 123
MAX_COILS_PER_WRITE = 1968

# pymodbus produces "No response received" in its ModbusIOException; the bus
# scanner and the activity log look for that wording.
NO_RESPONSE_MESSAGE = "No response received"


class ModbusFrameError(ValueError):
    """A malformed request parameter or reply frame."""


class ModbusNoResponse(TimeoutError):
    """The slave did not answer within the response timeout."""

    def __init__(self, message: str = NO_RESPONSE_MESSAGE) -> None:
        super().__init__(message)


def append_crc(payload: bytes) -> bytes:
    crc = modbus_crc16(payload)
    return payload + bytes((crc & 0xFF, crc >> 8))


def crc_valid(frame: bytes) -> bool:
    if len(frame) < 4:
        return False
    payload, low, high = frame[:-2], frame[-2], frame[-1]
    return modbus_crc16(payload) == (low | (high << 8))


def frame_to_hex(frame: bytes) -> str:
    return " ".join(f"{byte:02X}" for byte in frame)


def hex_to_frame(text: Any) -> bytes:
    """Parse ``"01 03 02 00 2A 38 49"`` / ``"0103..."`` / int lists into bytes."""
    if isinstance(text, (bytes, bytearray)):
        return bytes(text)
    if isinstance(text, (list, tuple)):
        return bytes(int(item) & 0xFF for item in text)
    cleaned = "".join(str(text or "").replace("0x", "").replace(":", " ").split())
    if len(cleaned) % 2:
        raise ModbusFrameError("Odd number of hex digits in frame")
    try:
        return bytes.fromhex(cleaned)
    except ValueError as err:
        raise ModbusFrameError(f"Invalid hex frame: {text!r}") from err


def _check_slave(slave: int) -> int:
    if not isinstance(slave, int) or isinstance(slave, bool) or not 0 <= slave <= 247:
        raise ModbusFrameError(f"Slave ID must be 0–247, got {slave!r}")
    return slave


def _check_address(address: int) -> int:
    if not isinstance(address, int) or not 0 <= address <= 0xFFFF:
        raise ModbusFrameError(f"Address must be 0–65535, got {address!r}")
    return address


def build_rtu_request(
    slave: int,
    function_code: int,
    address: int,
    *,
    count: int | None = None,
    value: int | bool | None = None,
    values: list[int] | list[bool] | None = None,
) -> bytes:
    """Encode one RTU request (with CRC) for the supported function codes."""
    slave = _check_slave(slave)
    address = _check_address(address)
    header = bytes((slave, function_code, address >> 8, address & 0xFF))
    if function_code in _READ_FUNCTIONS:
        if count is None:
            raise ModbusFrameError("count is required for read requests")
        limit = (
            MAX_COILS_PER_READ
            if function_code in _BIT_FUNCTIONS
            else MAX_REGISTERS_PER_READ
        )
        if not 1 <= int(count) <= limit:
            raise ModbusFrameError(f"count must be 1–{limit} for FC{function_code:02X}")
        return append_crc(header + bytes((int(count) >> 8, int(count) & 0xFF)))
    if function_code == FC_WRITE_COIL:
        coil = 0xFF00 if bool(value) else 0x0000
        return append_crc(header + bytes((coil >> 8, coil & 0xFF)))
    if function_code == FC_WRITE_REGISTER:
        if value is None:
            raise ModbusFrameError("value is required for FC06")
        register = int(value) & 0xFFFF
        return append_crc(header + bytes((register >> 8, register & 0xFF)))
    if function_code == FC_WRITE_COILS:
        bits = [bool(item) for item in (values or [])]
        if not 1 <= len(bits) <= MAX_COILS_PER_WRITE:
            raise ModbusFrameError("FC0F needs 1–1968 coil values")
        packed = bytearray((len(bits) + 7) // 8)
        for index, bit in enumerate(bits):
            if bit:
                packed[index // 8] |= 1 << (index % 8)
        body = bytes((len(bits) >> 8, len(bits) & 0xFF, len(packed))) + bytes(packed)
        return append_crc(header + body)
    if function_code == FC_WRITE_REGISTERS:
        words = [int(item) & 0xFFFF for item in (values or [])]
        if not 1 <= len(words) <= MAX_REGISTERS_PER_WRITE:
            raise ModbusFrameError("FC10 needs 1–123 register values")
        data = b"".join(bytes((word >> 8, word & 0xFF)) for word in words)
        body = bytes((len(words) >> 8, len(words) & 0xFF, len(data))) + data
        return append_crc(header + body)
    raise ModbusFrameError(f"Unsupported function code 0x{function_code:02X}")


@dataclass
class ModbusResponse:
    """Decoded reply with the pymodbus result attributes the integration uses."""

    slave_id: int
    function_code: int
    raw: bytes = b""
    registers: list[int] = field(default_factory=list)
    bits: list[bool] = field(default_factory=list)
    address: int | None = None
    value: int | bool | None = None
    count: int | None = None
    exception_code: int | None = None

    def isError(self) -> bool:
        return self.exception_code is not None

    @property
    def dev_id(self) -> int:  # pymodbus >= 3.8 spelling
        return self.slave_id

    def __str__(self) -> str:
        if self.exception_code is not None:
            return (
                f"Exception Response({self.function_code}, "
                f"{self.function_code & 0x7F}, {self.exception_code})"
            )
        return f"ModbusResponse(fc={self.function_code}, slave={self.slave_id})"


def parse_rtu_response(
    frame: bytes,
    *,
    expected_slave: int | None = None,
    expected_function: int | None = None,
    requested_count: int | None = None,
) -> ModbusResponse:
    """Validate CRC/addressing and decode one RTU reply frame."""
    frame = bytes(frame)
    if len(frame) < 4:
        raise ModbusFrameError(f"Frame too short ({len(frame)} bytes)")
    if not crc_valid(frame):
        raise ModbusFrameError(f"CRC mismatch in frame {frame_to_hex(frame)}")
    slave, function_code = frame[0], frame[1]
    if expected_slave is not None and slave != expected_slave:
        raise ModbusFrameError(f"Reply from slave {slave}, expected {expected_slave}")
    base = function_code & 0x7F
    if expected_function is not None and base != expected_function:
        raise ModbusFrameError(
            f"Reply for FC{base:02X}, expected FC{expected_function:02X}"
        )
    body = frame[2:-2]
    if function_code & 0x80:
        if len(body) != 1:
            raise ModbusFrameError("Malformed exception response")
        return ModbusResponse(
            slave_id=slave,
            function_code=function_code,
            raw=frame,
            exception_code=body[0],
        )
    if function_code in _READ_FUNCTIONS:
        if not body or len(body) - 1 != body[0]:
            raise ModbusFrameError("Byte count does not match payload length")
        data = body[1:]
        if function_code in _BIT_FUNCTIONS:
            bits = [bool(byte & (1 << bit)) for byte in data for bit in range(8)]
            if requested_count is not None:
                bits = bits[:requested_count]
            return ModbusResponse(
                slave_id=slave, function_code=function_code, raw=frame, bits=bits
            )
        if len(data) % 2:
            raise ModbusFrameError("Register payload has an odd byte count")
        registers = [
            (data[index] << 8) | data[index + 1] for index in range(0, len(data), 2)
        ]
        return ModbusResponse(
            slave_id=slave,
            function_code=function_code,
            raw=frame,
            registers=registers,
        )
    if function_code in _ECHO_FUNCTIONS:
        if len(body) != 4:
            raise ModbusFrameError("Write echo must carry address and value/count")
        address = (body[0] << 8) | body[1]
        word = (body[2] << 8) | body[3]
        response = ModbusResponse(
            slave_id=slave, function_code=function_code, raw=frame, address=address
        )
        if function_code == FC_WRITE_COIL:
            response.value = word == 0xFF00
        elif function_code == FC_WRITE_REGISTER:
            response.value = word
        else:
            response.count = word
        return response
    raise ModbusFrameError(f"Unsupported function code 0x{function_code:02X}")


class FramePairer:
    """Hand incoming RX frames to the single waiting request, thread-safely.

    ``begin(slave, fc)`` opens a slot for the request that is about to be
    sent; ``deliver(frame)`` (called from the event-loop thread when an
    ``esphome.modbus_rx`` event arrives) matches the frame against the open
    slot and wakes ``wait(timeout)``. Frames that arrive with no open slot,
    for another slave, or for a different function code are counted as
    ``dropped`` and discarded — an RTU bus has exactly one request in flight.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._slave: int | None = None
        self._function: int | None = None
        self._frame: bytes | None = None
        self.dropped = 0
        self.received = 0

    @property
    def open(self) -> bool:
        with self._lock:
            return self._slave is not None

    def begin(self, slave: int, function_code: int) -> None:
        with self._lock:
            self._slave = slave
            self._function = function_code & 0x7F
            self._frame = None
            self._ready.clear()

    def deliver(self, frame: bytes) -> bool:
        """Return True when the frame was accepted for the open request."""
        frame = bytes(frame)
        with self._lock:
            if (
                self._slave is None
                or self._frame is not None
                or len(frame) < 4
                or frame[0] != self._slave
                or (frame[1] & 0x7F) != self._function
            ):
                self.dropped += 1
                return False
            self._frame = frame
            self.received += 1
            self._ready.set()
            return True

    def wait(self, timeout: float) -> bytes:
        """Block until the reply arrives; raise ModbusNoResponse on timeout."""
        try:
            if not self._ready.wait(timeout):
                raise ModbusNoResponse()
            with self._lock:
                frame = self._frame
            if frame is None:  # pragma: no cover - defensive
                raise ModbusNoResponse()
            return frame
        finally:
            self.close()

    def close(self) -> None:
        with self._lock:
            self._slave = None
            self._function = None
            self._frame = None
            self._ready.clear()


# Changelog:
# 2026-09-21 — v2.8.0: introduced for the ESPHome native-API hub transport.
