"""Switch platform for Modbus USB Controller."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_ADDRESS,
    CONF_DEVICES,
    CONF_DEVICE_ID,
    CONF_ENTITIES,
    CONF_ENTITY_ID,
    CONF_ENTITY_TYPE,
    CONF_NAME,
    CONF_OFF_VALUE,
    CONF_ON_VALUE,
    CONF_REGISTER_TYPE,
    CONF_SLAVE_ID,
    DOMAIN,
    REGISTER_TYPE_COIL,
)
from .coordinator import ModbusUsbCoordinator, get_device_info


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: ModbusUsbCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = entry.options.get(CONF_ENTITIES, [])
    switches = [
        ModbusUsbSwitch(coordinator, entry, ent)
        for ent in entities
        if ent[CONF_ENTITY_TYPE] == "switch"
    ]
    async_add_entities(switches)


class ModbusUsbSwitch(CoordinatorEntity[ModbusUsbCoordinator], SwitchEntity):
    """A switch backed by a Modbus coil or holding register."""

    def __init__(self, coordinator: ModbusUsbCoordinator, entry: ConfigEntry, ent: dict) -> None:
        super().__init__(coordinator)
        self._ent = ent
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{ent[CONF_ENTITY_ID]}"
        self._attr_name = ent[CONF_NAME]
        self._attr_device_info = get_device_info(entry, ent)

    def _resolve_slave_id(self) -> int | None:
        slave = self._ent.get(CONF_SLAVE_ID)
        if slave is None and self._ent.get(CONF_DEVICE_ID):
            devices = self._entry.options.get(CONF_DEVICES, [])
            dev = next(
                (d for d in devices if str(d.get("id")) == str(self._ent.get(CONF_DEVICE_ID))),
                None,
            )
            if dev and dev.get(CONF_SLAVE_ID) is not None:
                slave = dev.get(CONF_SLAVE_ID)
        return int(slave) if slave is not None else None

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        value = self.coordinator.data.get(self._ent[CONF_ENTITY_ID])
        if value is None:
            return None
        if self._ent[CONF_REGISTER_TYPE] == REGISTER_TYPE_COIL:
            return bool(value)
        return value == self._ent.get(CONF_ON_VALUE, 1)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._write(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._write(False)

    async def _write(self, on: bool) -> None:
        address = self._ent[CONF_ADDRESS]
        slave = self._resolve_slave_id()
        if self._ent[CONF_REGISTER_TYPE] == REGISTER_TYPE_COIL:
            await self.hass.async_add_executor_job(
                self.coordinator.write_coil, address, on, slave
            )
        else:
            value = self._ent.get(CONF_ON_VALUE, 1) if on else self._ent.get(CONF_OFF_VALUE, 0)
            await self.hass.async_add_executor_job(
                self.coordinator.write_register, address, value, slave
            )
        await self.coordinator.async_request_refresh()
