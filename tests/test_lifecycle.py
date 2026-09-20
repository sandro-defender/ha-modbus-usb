"""Unload and registration must preserve other live hubs and HA instances."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components import modbus_usb
from custom_components.modbus_usb.const import DATA_PRESERVE_SERIAL_RELOAD, DOMAIN
from custom_components.modbus_usb.coordinator import ModbusUsbCoordinator


def unload_hass(*, success=True, preserve=False, other=False):
    coordinator = Mock(spec=ModbusUsbCoordinator)
    domain_data = {"hub": coordinator}
    if preserve:
        domain_data[DATA_PRESERVE_SERIAL_RELOAD] = {"hub"}
    if other:
        domain_data["other-hub"] = Mock(spec=ModbusUsbCoordinator)
    hass = SimpleNamespace(
        data={DOMAIN: domain_data},
        config_entries=SimpleNamespace(
            async_unload_platforms=AsyncMock(return_value=success)
        ),
        async_add_executor_job=AsyncMock(),
    )
    return hass, coordinator


async def test_failed_unload_keeps_services_and_coordinator():
    hass, coordinator = unload_hass(success=False, preserve=True)
    with patch.object(
        modbus_usb, "async_unregister_services", new_callable=AsyncMock
    ) as unregister:
        assert not await modbus_usb.async_unload_entry(
            hass, SimpleNamespace(entry_id="hub")
        )
        unregister.assert_not_awaited()
    assert hass.data[DOMAIN]["hub"] is coordinator
    assert hass.data[DOMAIN][DATA_PRESERVE_SERIAL_RELOAD] == {"hub"}
    hass.async_add_executor_job.assert_not_awaited()


@pytest.mark.parametrize("other", [False, True])
async def test_services_follow_loaded_hubs(other):
    hass, coordinator = unload_hass(other=other)
    with patch.object(
        modbus_usb, "async_unregister_services", new_callable=AsyncMock
    ) as unregister:
        assert await modbus_usb.async_unload_entry(
            hass, SimpleNamespace(entry_id="hub")
        )
        assert unregister.await_count == (0 if other else 1)
    hass.async_add_executor_job.assert_awaited_once_with(coordinator.close)
    if not other:
        assert DOMAIN not in hass.data


async def test_entity_reload_preserves_serial_owner():
    hass, coordinator = unload_hass(preserve=True)
    with patch.object(
        modbus_usb, "async_unregister_services", new_callable=AsyncMock
    ) as unregister:
        assert await modbus_usb.async_unload_entry(
            hass, SimpleNamespace(entry_id="hub")
        )
        unregister.assert_not_awaited()
    assert hass.data[DOMAIN]["hub"] is coordinator
    assert not hass.data[DOMAIN][DATA_PRESERVE_SERIAL_RELOAD]
    hass.async_add_executor_job.assert_not_awaited()


async def test_panel_registration_is_per_instance_and_retries_frontend_failure():
    hass = SimpleNamespace(data={}, http=SimpleNamespace(register_static_path=Mock()))
    other = SimpleNamespace(data={}, http=SimpleNamespace(register_static_path=Mock()))
    with patch(
        "homeassistant.components.frontend.async_register_built_in_panel",
        side_effect=[RuntimeError("not ready"), None, None],
    ) as register:
        await modbus_usb._async_register_panel(hass)
        await modbus_usb._async_register_panel(hass)
        await modbus_usb._async_register_panel(hass)
        await modbus_usb._async_register_panel(other)
        assert register.call_count == 3
    hass.http.register_static_path.assert_called_once()
    other.http.register_static_path.assert_called_once()
