"""Sensor platform for Modbus USB Controller."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_DEVICE_CLASS,
    CONF_ENTITIES,
    CONF_ENTITY_ID,
    CONF_ENTITY_TYPE,
    CONF_NAME,
    CONF_STATE_CLASS,
    CONF_UNIT_OF_MEASUREMENT,
    DOMAIN,
)
from .coordinator import ModbusUsbCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: ModbusUsbCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = entry.options.get(CONF_ENTITIES, [])
    sensors = [
        ModbusUsbSensor(coordinator, entry, ent)
        for ent in entities
        if ent[CONF_ENTITY_TYPE] == "sensor"
    ]
    async_add_entities(sensors)


class ModbusUsbSensor(CoordinatorEntity[ModbusUsbCoordinator], SensorEntity):
    """A sensor backed by a Modbus holding/input register."""

    def __init__(self, coordinator: ModbusUsbCoordinator, entry: ConfigEntry, ent: dict) -> None:
        super().__init__(coordinator)
        self._ent = ent
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{ent[CONF_ENTITY_ID]}"
        self._attr_name = ent[CONF_NAME]
        self._attr_native_unit_of_measurement = ent.get(CONF_UNIT_OF_MEASUREMENT) or None
        self._attr_device_class = ent.get(CONF_DEVICE_CLASS)
        self._attr_state_class = ent.get(CONF_STATE_CLASS)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Modbus USB",
        )

    @property
    def native_value(self):
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(self._ent[CONF_ENTITY_ID])
