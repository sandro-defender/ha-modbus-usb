"""Binary sensor platform for Modbus USB Controller.

Supports read-only coil and discrete-input registers as binary sensors.
"""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
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
    DOMAIN,
)
from .coordinator import ModbusUsbCoordinator
from .decoding import normalize_enum
from .device_info import get_device_info, get_entity_picture


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up binary sensor entities from config entry."""
    coordinator: ModbusUsbCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = entry.options.get(CONF_ENTITIES, [])
    binary_sensors = [
        ModbusUsbBinarySensor(coordinator, entry, ent)
        for ent in entities
        if ent[CONF_ENTITY_TYPE] == "binary_sensor"
    ]
    # v2.9.0: hub connectivity on the hub device.
    binary_sensors.append(HubConnectedSensor(coordinator, entry))
    async_add_entities(binary_sensors)


class ModbusUsbBinarySensor(
    CoordinatorEntity[ModbusUsbCoordinator], BinarySensorEntity
):
    """A read-only binary sensor backed by a Modbus coil or discrete-input register."""

    def __init__(
        self, coordinator: ModbusUsbCoordinator, entry: ConfigEntry, ent: dict
    ) -> None:
        super().__init__(coordinator)
        self._ent = ent
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{ent[CONF_ENTITY_ID]}"
        self._attr_name = ent[CONF_NAME]
        self._attr_device_class = normalize_enum(
            ent.get(CONF_DEVICE_CLASS), BinarySensorDeviceClass, ent.get(CONF_NAME, "")
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
    def is_on(self) -> bool | None:
        """Return True if the coil/discrete input is active."""
        if self.coordinator.data is None:
            return None
        value = self.coordinator.data.get(self._ent[CONF_ENTITY_ID])
        if value is None:
            return None
        return bool(value)


class HubConnectedSensor(CoordinatorEntity[ModbusUsbCoordinator], BinarySensorEntity):
    """v2.9.0: whether the hub client (serial adapter / bridge) is open.

    Mirrors coordinator.client.connected without any extra bus traffic;
    like every coordinator entity it turns unavailable while a poll
    fails (e.g. adapter_lost), and comes back on the first good refresh.
    """

    def __init__(self, coordinator: ModbusUsbCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_hub_connected"
        self._attr_name = "Connected"
        self._attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
        self._attr_device_info = get_device_info(entry, {})

    @property
    def is_on(self) -> bool:
        return bool(getattr(self.coordinator.client, "connected", False))


# Changelog:
# 2026-09-21 — v2.9.0: hub connectivity sensor on the hub device.
# 2026-09-06 — Entity picture from device/template image URL.
# Date modified: 2026-09-21
