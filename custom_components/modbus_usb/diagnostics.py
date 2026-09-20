"""Diagnostic helpers: CRC math and reconstructed request frames."""

from __future__ import annotations

from typing import Any


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
