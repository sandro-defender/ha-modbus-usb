"""One config entry must own a serial adapter, regardless of slave ID."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from homeassistant.data_entry_flow import AbortFlow

from custom_components.modbus_usb.config_flow import ModbusUsbConfigFlow


async def test_duplicate_port_with_different_slave_is_rejected():
    flow = ModbusUsbConfigFlow()
    flow._async_current_entries = Mock(
        return_value=[
            SimpleNamespace(data={"port": "/dev/ttyUSB0", "slave_id": 1}, options={}),
        ]
    )
    with pytest.raises(AbortFlow) as error:
        await flow.async_step_user({"port": "/dev/ttyUSB0", "slave_id": 2})
    assert error.value.reason == "already_configured"


# ─────────────── v2.7.0: read-only traffic capture status step ───────────────


class _FakeEntry:
    def __init__(self, entry_id: str = "entry-capture") -> None:
        self.entry_id = entry_id
        self.options = {}
        self.data = {}


def _options_flow(hass, entry=None):
    from custom_components.modbus_usb.config_flow import ModbusUsbOptionsFlow

    flow = ModbusUsbOptionsFlow()
    flow.hass = hass
    flow.config_entry = entry or _FakeEntry()
    return flow


def _hass_with_coordinator(capture_hook):
    from types import SimpleNamespace

    from custom_components.modbus_usb.const import DOMAIN

    coordinator = SimpleNamespace(capture_hook=capture_hook)
    return SimpleNamespace(
        data={DOMAIN: {"entry-capture": coordinator}},
        config_entries=SimpleNamespace(async_get_entry=lambda entry_id: None),
    )


async def test_options_menu_includes_capture_info():
    from custom_components.modbus_usb.config_flow import ModbusUsbOptionsFlow

    flow = ModbusUsbOptionsFlow()
    result = await flow.async_step_init()
    assert result["type"] == "menu"
    assert "capture_info" in result["menu_options"]


async def test_capture_info_reports_active_trace_hook():
    flow = _options_flow(_hass_with_coordinator("trace_packet"))
    result = await flow.async_step_capture_info()
    assert result["type"] == "form"
    assert result["step_id"] == "capture_info"
    details = result["description_placeholders"]["capture_details"]
    assert "ACTIVE" in details
    assert "trace_packet" in details


async def test_capture_info_reports_logging_fallback():
    flow = _options_flow(_hass_with_coordinator("logging"))
    result = await flow.async_step_capture_info()
    details = result["description_placeholders"]["capture_details"]
    assert "ACTIVE" in details
    assert "debug-log" in details


async def test_capture_info_reports_unsupported_pymodbus():
    flow = _options_flow(_hass_with_coordinator(None))
    result = await flow.async_step_capture_info()
    details = result["description_placeholders"]["capture_details"]
    assert "UNAVAILABLE" in details
    assert "hook: none" in details


async def test_capture_info_handles_missing_coordinator():
    from types import SimpleNamespace

    from custom_components.modbus_usb.const import DOMAIN

    flow = _options_flow(
        SimpleNamespace(
            data={DOMAIN: {}},
            config_entries=SimpleNamespace(async_get_entry=lambda entry_id: None),
        )
    )
    result = await flow.async_step_capture_info()
    assert result["step_id"] == "capture_info"
    details = result["description_placeholders"]["capture_details"]
    assert "UNAVAILABLE" in details


async def test_capture_info_submit_returns_to_menu():
    flow = _options_flow(_hass_with_coordinator("trace_packet"))
    result = await flow.async_step_capture_info({})
    assert result["type"] == "menu"
