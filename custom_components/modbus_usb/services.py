"""HA service calls for the Modbus USB Controller integration.

Provides:
  - modbus_usb.read_register         → fire-and-forget read, result in event
  - modbus_usb.write_register        → write a coil or holding register
  - modbus_usb.batch_write           → several writes under one coordinator lock
  - modbus_usb.boost_polling         → temporary high-frequency polling
  - modbus_usb.reset_circuit_breaker → restore an offline/degraded slave
"""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .const import (
    BATCH_WRITE_MAX_ITEMS,
    BOOST_MAX_DURATION,
    BOOST_MAX_INTERVAL,
    BOOST_MIN_DURATION,
    BOOST_MIN_INTERVAL,
    CONF_ADDRESS,
    CONF_DATA_TYPE,
    CONF_REGISTER_TYPE,
    CONF_SLAVE_ID,
    DATA_TYPE_FLOAT32,
    DATA_TYPE_INT16,
    DATA_TYPE_INT32,
    DATA_TYPE_UINT16,
    DATA_TYPE_UINT32,
    DATA_TYPES,
    DOMAIN,
    EVENT_REGISTER_READ,
    REGISTER_TYPE_COIL,
    REGISTER_TYPE_DISCRETE,
    REGISTER_TYPE_HOLDING,
    REGISTER_TYPE_INPUT,
    SERVICE_BATCH_WRITE,
    SERVICE_BOOST_POLLING,
    SERVICE_READ_REGISTER,
    SERVICE_RESET_CIRCUIT_BREAKER,
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
        vol.Required(CONF_ADDRESS): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=65535)
        ),
        vol.Required(CONF_REGISTER_TYPE, default=REGISTER_TYPE_HOLDING): vol.In(
            _ALL_REGISTER_TYPES
        ),
        vol.Optional(CONF_DATA_TYPE, default=DATA_TYPE_UINT16): vol.In(DATA_TYPES),
        vol.Optional(CONF_SLAVE_ID): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=247)
        ),
    }
)

WRITE_REGISTER_SCHEMA = vol.Schema(
    {
        vol.Required("entry_id"): cv.string,
        vol.Required(CONF_ADDRESS): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=65535)
        ),
        vol.Required(CONF_REGISTER_TYPE, default=REGISTER_TYPE_HOLDING): vol.In(
            [REGISTER_TYPE_HOLDING, REGISTER_TYPE_COIL]
        ),
        vol.Required("value"): vol.All(vol.Coerce(int), vol.Range(min=0, max=65535)),
        vol.Optional(CONF_SLAVE_ID): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=247)
        ),
    }
)


_BATCH_WRITE_ITEM_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ADDRESS): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=65535)
        ),
        vol.Required(CONF_REGISTER_TYPE, default=REGISTER_TYPE_HOLDING): vol.In(
            [REGISTER_TYPE_HOLDING, REGISTER_TYPE_COIL]
        ),
        vol.Required("value"): vol.Coerce(float),
        vol.Optional(CONF_DATA_TYPE, default=DATA_TYPE_UINT16): vol.In(
            [
                DATA_TYPE_UINT16,
                DATA_TYPE_INT16,
                DATA_TYPE_UINT32,
                DATA_TYPE_INT32,
                DATA_TYPE_FLOAT32,
            ]
        ),
    }
)

BATCH_WRITE_SCHEMA = vol.Schema(
    {
        vol.Required("entry_id"): cv.string,
        vol.Required("writes"): vol.All(
            [_BATCH_WRITE_ITEM_SCHEMA], vol.Length(min=1, max=BATCH_WRITE_MAX_ITEMS)
        ),
        vol.Optional(CONF_SLAVE_ID): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=247)
        ),
    }
)

BOOST_POLLING_SCHEMA = vol.Schema(
    {
        vol.Required("entry_id"): cv.string,
        vol.Required("duration"): vol.All(
            vol.Coerce(float), vol.Range(min=BOOST_MIN_DURATION, max=BOOST_MAX_DURATION)
        ),
        vol.Optional("scan_interval", default=BOOST_MIN_INTERVAL): vol.All(
            vol.Coerce(int),
            vol.Range(min=BOOST_MIN_INTERVAL, max=BOOST_MAX_INTERVAL),
        ),
    }
)

RESET_CIRCUIT_BREAKER_SCHEMA = vol.Schema(
    {
        vol.Required("entry_id"): cv.string,
        vol.Optional(CONF_SLAVE_ID): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=247)
        ),
    }
)


def _get_coordinator(hass: HomeAssistant, entry_id: str) -> ModbusUsbCoordinator:
    domain_data = hass.data.get(DOMAIN, {})
    coordinator = domain_data.get(entry_id)
    if coordinator is None:
        raise ValueError(f"No Modbus USB entry found with id '{entry_id}'")
    return coordinator


def _require_coordinator(hass: HomeAssistant, entry_id: str) -> ModbusUsbCoordinator:
    """Resolve a coordinator for automation services with a visible error."""
    coordinator = hass.data.get(DOMAIN, {}).get(entry_id)
    if coordinator is None:
        raise HomeAssistantError(
            f"No Modbus USB hub found for config entry '{entry_id}'"
        )
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
        except Exception as err:
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
        except Exception as err:
            _LOGGER.error("Service write_register failed: %s", err)
            # A service failure must be visible to scripts and automations;
            # logging alone makes an unsuccessful hardware command look valid.
            raise HomeAssistantError(f"Modbus write failed: {err}") from err

    async def handle_batch_write(call: ServiceCall) -> None:
        entry_id: str = call.data["entry_id"]
        writes: list[dict] = call.data["writes"]
        slave: int | None = call.data.get(CONF_SLAVE_ID)

        coordinator = _require_coordinator(hass, entry_id)
        try:
            result = await hass.async_add_executor_job(
                coordinator.batch_write, writes, slave
            )
        except Exception as err:
            _LOGGER.error("Service batch_write failed: %s", err)
            raise HomeAssistantError(f"Modbus batch write failed: {err}") from err
        _LOGGER.debug(
            "Batch write: %s/%s writes accepted on slave %s",
            result["written"],
            len(writes),
            result["slave_id"],
        )
        # One refresh after the whole batch keeps states current without
        # polling between individual write frames.
        await coordinator.async_request_refresh()

    async def handle_boost_polling(call: ServiceCall) -> None:
        from homeassistant.helpers.event import async_call_later

        entry_id: str = call.data["entry_id"]
        duration: float = call.data["duration"]
        scan_interval: int = call.data.get("scan_interval", BOOST_MIN_INTERVAL)

        coordinator = _require_coordinator(hass, entry_id)
        boost = coordinator.apply_polling_boost(scan_interval, duration)

        def _maybe_restore_boost(_now) -> None:
            # A later boost call extends the same window; the earlier timer
            # must not restore while the extended boost is still running.
            if not coordinator.boost_state()["active"]:
                coordinator.restore_polling_boost()

        async_call_later(hass, duration, _maybe_restore_boost)
        _LOGGER.info(
            "Polling boost: %ss interval for %ss on entry %s",
            scan_interval,
            duration,
            entry_id,
        )
        await coordinator.async_request_refresh()
        _LOGGER.debug("Boost state after apply: %s", boost)

    async def handle_reset_circuit_breaker(call: ServiceCall) -> None:
        entry_id: str = call.data["entry_id"]
        slave: int | None = call.data.get(CONF_SLAVE_ID)

        coordinator = _require_coordinator(hass, entry_id)
        breaker = getattr(coordinator, "circuit_breaker", None)
        if breaker is None:
            raise HomeAssistantError("This hub does not have a slave circuit breaker")
        reset_ids = breaker.reset(slave)
        _LOGGER.info(
            "Circuit breaker reset for %s on entry %s",
            f"slave {slave}" if slave is not None else "all slaves",
            entry_id,
        )
        _LOGGER.debug("Reset slave IDs: %s", reset_ids)

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
    hass.services.async_register(
        DOMAIN,
        SERVICE_BATCH_WRITE,
        handle_batch_write,
        schema=BATCH_WRITE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_BOOST_POLLING,
        handle_boost_polling,
        schema=BOOST_POLLING_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_RESET_CIRCUIT_BREAKER,
        handle_reset_circuit_breaker,
        schema=RESET_CIRCUIT_BREAKER_SCHEMA,
    )


async def async_unregister_services(hass: HomeAssistant) -> None:
    """Unregister services when the last entry is removed."""
    for service in (
        SERVICE_READ_REGISTER,
        SERVICE_WRITE_REGISTER,
        SERVICE_BATCH_WRITE,
        SERVICE_BOOST_POLLING,
        SERVICE_RESET_CIRCUIT_BREAKER,
    ):
        if hass.services.has_service(DOMAIN, service):
            hass.services.async_remove(DOMAIN, service)
