"""Number entity platform for Modbus USB Controller.

Exposes a holding register as a numeric input (slider or text box) that
can be read and written directly from the HA frontend.
"""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_ADDRESS,
    CONF_DATA_TYPE,
    CONF_DEVICE_ID,
    CONF_DEVICES,
    CONF_ENTITIES,
    CONF_ENTITY_ID,
    CONF_ENTITY_TYPE,
    CONF_MAX_VALUE,
    CONF_MIN_VALUE,
    CONF_MODE,
    CONF_NAME,
    CONF_SCALE,
    CONF_SLAVE_ID,
    CONF_STEP,
    CONF_UNIT_OF_MEASUREMENT,
    DATA_TYPE_UINT16,
    DATA_TYPE_WORD_COUNT,
    DOMAIN,
)
from .coordinator import ModbusUsbCoordinator
from .decoding import as_float
from .device_info import get_device_info, get_entity_picture


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up number entities from config entry."""
    coordinator: ModbusUsbCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = entry.options.get(CONF_ENTITIES, [])
    numbers = [
        ModbusUsbNumber(coordinator, entry, ent)
        for ent in entities
        if ent[CONF_ENTITY_TYPE] == "number"
    ]
    async_add_entities(numbers)


class ModbusUsbNumber(CoordinatorEntity[ModbusUsbCoordinator], NumberEntity):
    """A writable number entity backed by a Modbus holding register."""

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
        # Sidebar edits can persist null for optional numeric settings; a
        # plain float() of None removed the whole number platform on setup.
        min_value = as_float(ent.get(CONF_MIN_VALUE), 0)
        max_value = as_float(ent.get(CONF_MAX_VALUE), 65535)
        if max_value < min_value:
            min_value, max_value = max_value, min_value
        step = as_float(ent.get(CONF_STEP), 1)
        self._attr_native_min_value = min_value
        self._attr_native_max_value = max_value
        # Home Assistant requires a positive step; a 0/negative saved step
        # made the slider unusable.
        self._attr_native_step = step if step > 0 else 1
        mode_str = ent.get(CONF_MODE, "slider")
        self._attr_mode = NumberMode.SLIDER if mode_str == "slider" else NumberMode.BOX
        self._scale: float = as_float(ent.get(CONF_SCALE), 1) or 1
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

    def _resolve_slave_id(self) -> int | None:
        slave = self._ent.get(CONF_SLAVE_ID)
        if slave is None and self._ent.get(CONF_DEVICE_ID):
            devices = self._entry.options.get(CONF_DEVICES, [])
            dev = next(
                (
                    d
                    for d in devices
                    if str(d.get("id")) == str(self._ent.get(CONF_DEVICE_ID))
                ),
                None,
            )
            if dev and dev.get(CONF_SLAVE_ID) is not None:
                slave = dev.get(CONF_SLAVE_ID)
        return int(slave) if slave is not None else None

    @property
    def native_value(self) -> float | None:
        """Return the current register value, scaled."""
        if self.coordinator.data is None:
            return None
        raw = self.coordinator.data.get(self._ent[CONF_ENTITY_ID])
        if raw is None:
            return None
        return float(raw)

    async def async_set_native_value(self, value: float) -> None:
        """Write a new value to the holding register."""
        address = self._ent[CONF_ADDRESS]
        data_type = self._ent.get(CONF_DATA_TYPE, DATA_TYPE_UINT16)
        count = DATA_TYPE_WORD_COUNT.get(data_type, 1)
        scale = self._scale
        slave = self._resolve_slave_id()

        # Convert display value back to raw integer
        raw_int = (
            int(round(value / scale)) if scale not in (1.0, 0.0) else int(round(value))
        )

        if count == 1:
            await self.hass.async_add_executor_job(
                self.coordinator.write_register, address, raw_int, slave
            )
        else:
            # 32-bit: write two registers using pymodbus write_registers
            await self.hass.async_add_executor_job(
                self.coordinator.write_registers_32bit,
                address,
                raw_int,
                data_type,
                slave,
            )
        await self.coordinator.async_request_refresh()


# Changelog:
# 2026-09-06 — Entity picture from device/template image URL.
# Date modified: 2026-09-06
