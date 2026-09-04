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
    CONF_ENTITIES,
    CONF_ENTITY_ID,
    CONF_ENTITY_TYPE,
    CONF_NAME,
    CONF_OFF_VALUE,
    CONF_ON_VALUE,
    CONF_REGISTER_TYPE,
    DOMAIN,
    REGISTER_TYPE_COIL,
)
from .coordinator import ModbusUsbCoordinator


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
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Modbus USB",
        )

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
        if self._ent[CONF_REGISTER_TYPE] == REGISTER_TYPE_COIL:
            await self.hass.async_add_executor_job(self.coordinator.write_coil, address, on)
        else:
            value = self._ent.get(CONF_ON_VALUE, 1) if on else self._ent.get(CONF_OFF_VALUE, 0)
            await self.hass.async_add_executor_job(self.coordinator.write_register, address, value)
        await self.coordinator.async_request_refresh()
