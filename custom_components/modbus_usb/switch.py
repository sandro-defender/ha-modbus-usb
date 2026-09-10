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
    CONF_ADDRESSES,
    CONF_ASSUMED_STATE,
    CONF_DEVICES,
    CONF_DEVICE_ID,
    CONF_ENTITIES,
    CONF_ENTITY_ID,
    CONF_ENTITY_TYPE,
    CONF_NAME,
    CONF_OFF_VALUE,
    CONF_ON_VALUE,
    CONF_REGISTER_TYPE,
    CONF_STATE_ON_VALUE,
    CONF_SLAVE_ID,
    DOMAIN,
    REGISTER_TYPE_COIL,
)
from .coordinator import ModbusUsbCoordinator, get_device_info, get_entity_picture


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
        self._attr_assumed_state = bool(ent.get(CONF_ASSUMED_STATE, False))
        self._last_command: bool | None = None
        picture = get_entity_picture(entry, ent)
        if picture:
            self._attr_entity_picture = picture

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
        if self._attr_assumed_state:
            return False if self._last_command is None else self._last_command
        command_state = self.coordinator.get_command_state(self._ent[CONF_ENTITY_ID])
        if command_state is not None:
            return command_state
        if self.coordinator.data is None:
            return None
        value = self.coordinator.data.get(self._ent[CONF_ENTITY_ID])
        if value is None:
            return None
        if self._ent[CONF_REGISTER_TYPE] == REGISTER_TYPE_COIL:
            return bool(value)
        state_on_value = self._ent.get(CONF_STATE_ON_VALUE, self._ent.get(CONF_ON_VALUE, 1))
        # R413E16 firmware variants report a channel as either 1 (state-bit
        # form) or 0x0100 / 256 (last-command form). Both are the documented
        # ON representation for a channel written with 0x0100; accepting both
        # prevents a physically ON output appearing OFF in Home Assistant.
        if (
            state_on_value == 1
            and self._ent.get(CONF_ON_VALUE) == 0x0100
            and self._ent.get(CONF_OFF_VALUE) == 0x0200
        ):
            return value in (1, 0x0100)
        return value == state_on_value

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._write(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._write(False)

    async def _write(self, on: bool) -> None:
        addresses = self._ent.get(CONF_ADDRESSES) or [self._ent[CONF_ADDRESS]]
        slave = self._resolve_slave_id()
        if self._ent[CONF_REGISTER_TYPE] == REGISTER_TYPE_COIL:
            for address in addresses:
                await self.hass.async_add_executor_job(
                    self.coordinator.write_coil, int(address), on, slave
                )
        else:
            value = self._ent.get(CONF_ON_VALUE, 1) if on else self._ent.get(CONF_OFF_VALUE, 0)
            for address in addresses:
                await self.hass.async_add_executor_job(
                    self.coordinator.write_register, int(address), value, slave
                )
        if (
            self._ent.get(CONF_REGISTER_TYPE) != REGISTER_TYPE_COIL
            and self._ent.get(CONF_ON_VALUE) == 0x0100
            and self._ent.get(CONF_OFF_VALUE) == 0x0200
            and self._ent.get(CONF_DEVICE_ID)
        ):
            self.coordinator.set_r413e16_channel_states(
                self._ent[CONF_DEVICE_ID], {int(address): on for address in addresses}
            )
        if self._attr_assumed_state:
            self._last_command = on
            self.async_write_ha_state()
            # A combined command has no single register to read back for its
            # own state. Matching R413E16 channels were updated above from the
            # accepted commands, because some board revisions report OFF when
            # read immediately after a successful write.
            return
        await self.coordinator.async_request_refresh()


# Changelog:
# 2026-09-06 — Assumed-state switches (no poll); ON/OFF register values; device image as entity_picture.
# Date modified: 2026-09-06

