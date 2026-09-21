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
from .coordinator import ModbusUsbCoordinator
from .decoding import normalize_enum
from .device_info import get_device_info, get_entity_picture


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
    # v2.9.0: hub health sensors (error rate, reconnects) on the hub device.
    sensors.append(HubErrorRateSensor(coordinator, entry))
    sensors.append(HubReconnectsSensor(coordinator, entry))
    async_add_entities(sensors)


class ModbusUsbSensor(CoordinatorEntity[ModbusUsbCoordinator], SensorEntity):
    """A sensor backed by a Modbus holding/input register."""

    def __init__(
        self, coordinator: ModbusUsbCoordinator, entry: ConfigEntry, ent: dict
    ) -> None:
        super().__init__(coordinator)
        self._ent = ent
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{ent[CONF_ENTITY_ID]}"
        self._attr_name = ent[CONF_NAME]
        self._attr_native_unit_of_measurement = (
            ent.get(CONF_UNIT_OF_MEASUREMENT) or None
        )
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
            (
                item
                for item in self._entry.options.get(CONF_DEVICES, [])
                if str(item.get("id")) == str(device_id)
            ),
            None,
        )
        return (device is None or device.get("enabled", True)) and super().available

    @property
    def native_value(self):
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(self._ent[CONF_ENTITY_ID])


class HubSensorBase(CoordinatorEntity[ModbusUsbCoordinator], SensorEntity):
    """Base for the v2.9.0 hub health sensors on the hub device.

    States come straight from coordinator book-keeping (the rolling
    transaction window and the reconnect counter) — no extra bus traffic.
    """

    def __init__(
        self, coordinator: ModbusUsbCoordinator, entry: ConfigEntry, key: str, name: str
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._key = key
        self._attr_unique_id = f"{entry.entry_id}_hub_{key}"
        self._attr_name = name
        self._attr_device_info = get_device_info(entry, {})


class HubErrorRateSensor(HubSensorBase):
    """% of failed transactions over the last 5 minutes."""

    def __init__(self, coordinator: ModbusUsbCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "error_rate", "Error rate")
        self._attr_native_unit_of_measurement = "%"
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.error_rate_percent()


class HubReconnectsSensor(HubSensorBase):
    """Total successful (re)connects of the hub client (total_increasing)."""

    def __init__(self, coordinator: ModbusUsbCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "reconnects", "Reconnects")
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING

    @property
    def native_value(self) -> int | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.reconnect_count


# Changelog:
# 2026-09-21 — v2.9.0: hub health sensors (error rate, reconnects).
# 2026-09-06 — Entity picture from device/template image URL.
# Date modified: 2026-09-21
