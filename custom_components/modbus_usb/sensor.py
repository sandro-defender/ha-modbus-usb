"""Sensor platform for Modbus USB Controller."""
from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_DEVICE_CLASS,
    CONF_DEVICE_ID,
    CONF_DEVICES,
    CONF_ENTITIES,
    CONF_ENTITY_ID,
    CONF_ENTITY_TYPE,
    CONF_NAME,
    CONF_STATE_CLASS,
    CONF_UNIT_OF_MEASUREMENT,
    DOMAIN,
)
from .coordinator import (
    ModbusUsbCoordinator,
    get_device_info,
    get_entity_picture,
    normalize_enum,
)


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
        self._attr_device_class = normalize_enum(
            ent.get(CONF_DEVICE_CLASS), SensorDeviceClass, ent.get(CONF_NAME, "")
        )
        self._attr_state_class = normalize_enum(
            ent.get(CONF_STATE_CLASS), SensorStateClass, ent.get(CONF_NAME, "")
        )
        self._attr_device_info = get_device_info(entry, ent)
        picture = get_entity_picture(entry, ent)
        if picture:
            self._attr_entity_picture = picture

    @property
    def available(self) -> bool:
        device_id = self._ent.get(CONF_DEVICE_ID)
        device = next(
            (item for item in self._entry.options.get(CONF_DEVICES, [])
             if str(item.get("id")) == str(device_id)),
            None,
        )
        return (device is None or device.get("enabled", True)) and super().available

    @property
    def native_value(self):
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(self._ent[CONF_ENTITY_ID])


# Changelog:
# 2026-09-06 — Entity picture from device/template image URL.
# Date modified: 2026-09-06

