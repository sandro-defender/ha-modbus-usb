"""Unit tests for the pymodbus unit-ID compatibility shim."""

from __future__ import annotations

import pytest

from custom_components.modbus_usb.bus import call_modbus_on_client

pytestmark = pytest.mark.fast


class _ModernClient:
    """Pymodbus 3.8+ style: accepts device_id."""

    def __init__(self) -> None:
        self.kwargs: dict = {}

    def read_holding_registers(self, *args, **kwargs):
        self.kwargs = kwargs
        return "ok"


class _LegacyClient:
    """Older pymodbus style: only accepts slave."""

    def __init__(self) -> None:
        self.kwargs: dict = {}

    def read_holding_registers(self, *args, **kwargs):
        if "device_id" in kwargs:
            raise TypeError("unexpected keyword argument 'device_id'")
        self.kwargs = kwargs
        return "ok"


def test_call_modbus_prefers_device_id() -> None:
    client = _ModernClient()
    assert call_modbus_on_client(client, "read_holding_registers", 0, slave=3) == "ok"
    assert client.kwargs["device_id"] == 3


def test_call_modbus_falls_back_to_legacy_slave() -> None:
    client = _LegacyClient()
    assert call_modbus_on_client(client, "read_holding_registers", 0, slave=3) == "ok"
    assert client.kwargs["slave"] == 3


def test_call_modbus_reraises_unrelated_type_errors() -> None:
    class _BrokenClient:
        def read_holding_registers(self, *args, **kwargs):
            raise TypeError("boom")

    with pytest.raises(TypeError, match="boom"):
        call_modbus_on_client(_BrokenClient(), "read_holding_registers", 0, slave=1)
