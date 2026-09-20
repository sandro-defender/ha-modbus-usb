"""Numeric writes must preserve the configured wire representation."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.modbus_usb.number import ModbusUsbNumber


def make_number(data_type, scale=1, **overrides):
    coordinator = Mock(data={}, last_update_success=True)
    coordinator.async_request_refresh = AsyncMock()
    entry = SimpleNamespace(entry_id="hub", title="Hub", options={})
    entity = ModbusUsbNumber(coordinator, entry, {
        "id": "number", "name": "Setpoint", "address": 10,
        "data_type": data_type, "scale": scale,
        "min_value": -1e12, "max_value": 1e12, **overrides,
    })
    entity.hass = SimpleNamespace(async_add_executor_job=AsyncMock())
    return entity, coordinator


@pytest.mark.parametrize(("data_type", "value", "scale", "expected"), [
    ("float32", 12.75, 1, 12.75),
    ("float32", 1.25, 0.1, 12.5),
    ("int16", -1, 1, 65535),
    ("int16", -32768, 1, 32768),
    ("uint16", 65535, 1, 65535),
    ("uint16", 1.25, 0.1, 12),
    ("int32", -123, 1, -123),
    ("uint32", 4294967295, 1, 4294967295),
])
async def test_number_write_encoding(data_type, value, scale, expected):
    entity, coordinator = make_number(data_type, scale)
    await entity.async_set_native_value(value)
    args = (10, expected, None) if data_type.endswith("16") else (10, expected, data_type, None)
    method = coordinator.write_register if data_type.endswith("16") else coordinator.write_registers_32bit
    entity.hass.async_add_executor_job.assert_awaited_once_with(method, *args)
    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.parametrize(("data_type", "value"), [
    ("uint16", -1), ("uint16", 65536), ("int16", -32769), ("int16", 32768),
    ("uint32", -1), ("int32", 2147483648), ("float32", float("nan")),
    ("float32", float("inf")), ("unsupported", 1),
])
async def test_invalid_number_never_reaches_bus(data_type, value):
    entity, coordinator = make_number(data_type)
    with pytest.raises(HomeAssistantError):
        await entity.async_set_native_value(value)
    entity.hass.async_add_executor_job.assert_not_awaited()
    coordinator.async_request_refresh.assert_not_awaited()


async def test_configured_range_is_enforced():
    entity, _ = make_number("uint16", min_value=0, max_value=100)
    with pytest.raises(HomeAssistantError, match="configured range"):
        await entity.async_set_native_value(101)
    entity.hass.async_add_executor_job.assert_not_awaited()
