"""HA service calls for the Modbus USB Controller integration.

Provides:
  - modbus_usb.read_register  → fire-and-forget read, result in event
  - modbus_usb.write_register → write a coil or holding register
"""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv

from .const import (
    CONF_ADDRESS,
    CONF_DATA_TYPE,
    CONF_REGISTER_TYPE,
    CONF_SLAVE_ID,
    DATA_TYPES,
    DATA_TYPE_UINT16,
    DOMAIN,
    EVENT_REGISTER_READ,
    REGISTER_TYPE_COIL,
    REGISTER_TYPE_DISCRETE,
    REGISTER_TYPE_HOLDING,
    REGISTER_TYPE_INPUT,
    SERVICE_READ_REGISTER,
    SERVICE_WRITE_REGISTER,
)
from .coordinator import ModbusUsbCoordinator

_LOGGER = logging.getLogger(__name__)

_ALL_REGISTER_TYPES = [
    REGISTER_TYPE_HOLDING,
    REGISTER_TYPE_INPUT,
    REGISTER_TYPE_COIL,
    REGISTER_TYPE_DISCRETE,
]

READ_REGISTER_SCHEMA = vol.Schema(
    {
        vol.Required("entry_id"): cv.string,
        vol.Required(CONF_ADDRESS): vol.All(vol.Coerce(int), vol.Range(min=0, max=65535)),
        vol.Required(CONF_REGISTER_TYPE, default=REGISTER_TYPE_HOLDING): vol.In(
            _ALL_REGISTER_TYPES
        ),
        vol.Optional(CONF_DATA_TYPE, default=DATA_TYPE_UINT16): vol.In(DATA_TYPES),
        vol.Optional(CONF_SLAVE_ID): vol.All(vol.Coerce(int), vol.Range(min=1, max=247)),
    }
)

WRITE_REGISTER_SCHEMA = vol.Schema(
    {
        vol.Required("entry_id"): cv.string,
        vol.Required(CONF_ADDRESS): vol.All(vol.Coerce(int), vol.Range(min=0, max=65535)),
        vol.Required(CONF_REGISTER_TYPE, default=REGISTER_TYPE_HOLDING): vol.In(
            [REGISTER_TYPE_HOLDING, REGISTER_TYPE_COIL]
        ),
        vol.Required("value"): vol.Coerce(int),
        vol.Optional(CONF_SLAVE_ID): vol.All(vol.Coerce(int), vol.Range(min=1, max=247)),
    }
)


def _get_coordinator(hass: HomeAssistant, entry_id: str) -> ModbusUsbCoordinator:
    domain_data = hass.data.get(DOMAIN, {})
    coordinator = domain_data.get(entry_id)
    if coordinator is None:
        raise ValueError(f"No Modbus USB entry found with id '{entry_id}'")
    return coordinator


async def async_register_services(hass: HomeAssistant) -> None:
    """Register modbus_usb services. Safe to call multiple times (checks first)."""
    if hass.services.has_service(DOMAIN, SERVICE_READ_REGISTER):
        return

    async def handle_read_register(call: ServiceCall) -> None:
        entry_id: str = call.data["entry_id"]
        address: int = call.data[CONF_ADDRESS]
        register_type: str = call.data[CONF_REGISTER_TYPE]
        data_type: str = call.data.get(CONF_DATA_TYPE, DATA_TYPE_UINT16)
        slave: int | None = call.data.get(CONF_SLAVE_ID)

        coordinator = _get_coordinator(hass, entry_id)
        try:
            value = await hass.async_add_executor_job(
                coordinator.read_register_raw, address, register_type, data_type, slave
            )
            hass.bus.async_fire(
                EVENT_REGISTER_READ,
                {
                    "entry_id": entry_id,
                    "address": address,
                    "register_type": register_type,
                    "data_type": data_type,
                    "slave_id": slave or coordinator.slave_id,
                    "value": value,
                    "success": True,
                },
            )
            _LOGGER.debug(
                "Read register %s (%s) slave=%s = %s",
                address,
                register_type,
                slave or coordinator.slave_id,
                value,
            )
        except Exception as err:  # noqa: BLE001
            hass.bus.async_fire(
                EVENT_REGISTER_READ,
                {
                    "entry_id": entry_id,
                    "address": address,
                    "register_type": register_type,
                    "data_type": data_type,
                    "slave_id": slave or coordinator.slave_id,
                    "value": None,
                    "success": False,
                    "error": str(err),
                },
            )
            _LOGGER.warning("Service read_register failed: %s", err)

    async def handle_write_register(call: ServiceCall) -> None:
        entry_id: str = call.data["entry_id"]
        address: int = call.data[CONF_ADDRESS]
        register_type: str = call.data[CONF_REGISTER_TYPE]
        value: int = call.data["value"]
        slave: int | None = call.data.get(CONF_SLAVE_ID)

        coordinator = _get_coordinator(hass, entry_id)
        try:
            if register_type == REGISTER_TYPE_COIL:
                await hass.async_add_executor_job(
                    coordinator.write_coil, address, bool(value), slave
                )
            else:
                await hass.async_add_executor_job(
                    coordinator.write_register, address, value, slave
                )
            await coordinator.async_request_refresh()
            _LOGGER.debug(
                "Wrote register %s (%s) slave=%s = %s",
                address,
                register_type,
                slave or coordinator.slave_id,
                value,
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Service write_register failed: %s", err)

    hass.services.async_register(
        DOMAIN,
        SERVICE_READ_REGISTER,
        handle_read_register,
        schema=READ_REGISTER_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_WRITE_REGISTER,
        handle_write_register,
        schema=WRITE_REGISTER_SCHEMA,
    )


async def async_unregister_services(hass: HomeAssistant) -> None:
    """Unregister services when the last entry is removed."""
    if hass.services.has_service(DOMAIN, SERVICE_READ_REGISTER):
        hass.services.async_remove(DOMAIN, SERVICE_READ_REGISTER)
    if hass.services.has_service(DOMAIN, SERVICE_WRITE_REGISTER):
        hass.services.async_remove(DOMAIN, SERVICE_WRITE_REGISTER)
