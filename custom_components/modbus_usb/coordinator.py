"""Data update coordinator for the Modbus USB Controller integration."""
from __future__ import annotations

import logging
import struct
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_ADDRESS,
    CONF_ASSUMED_STATE,
    CONF_DATA_TYPE,
    CONF_DEVICES,
    CONF_DEVICE_ID,
    CONF_ENTITIES,
    CONF_ENTITY_TYPE,
    CONF_IMAGE,
    CONF_MANUFACTURER,
    CONF_MODEL,
    CONF_NAME,
    CONF_REGISTER_TYPE,
    CONF_SCALE,
    CONF_SLAVE_ID,
    DATA_TYPE_FLOAT32,
    DATA_TYPE_INT16,
    DATA_TYPE_INT32,
    DATA_TYPE_UINT16,
    DATA_TYPE_UINT32,
    DATA_TYPE_WORD_COUNT,
    DIAG_CONSECUTIVE_FAILURES,
    DIAG_FAILED_READS,
    DIAG_LAST_ERROR,
    DIAG_LAST_SUCCESS,
    DIAG_TOTAL_READS,
    DOMAIN,
    REGISTER_TYPE_COIL,
    REGISTER_TYPE_DISCRETE,
    REGISTER_TYPE_HOLDING,
    REGISTER_TYPE_INPUT,
)

_LOGGER = logging.getLogger(__name__)


def _decode_words(words: list[int], data_type: str) -> float | int:
    """Decode a list of 16-bit register words into a number."""
    if data_type == DATA_TYPE_UINT16:
        return words[0]
    if data_type == DATA_TYPE_INT16:
        val = words[0]
        return val - 0x10000 if val >= 0x8000 else val
    # 32-bit types: big-endian word order (high word first)
    raw = struct.pack(">HH", words[0], words[1])
    if data_type == DATA_TYPE_UINT32:
        return struct.unpack(">I", raw)[0]
    if data_type == DATA_TYPE_INT32:
        return struct.unpack(">i", raw)[0]
    if data_type == DATA_TYPE_FLOAT32:
        return struct.unpack(">f", raw)[0]
    return words[0]


def get_device_info(entry: ConfigEntry, ent: dict) -> DeviceInfo:
    """Return DeviceInfo for an entity, linking it to a separated device or the hub."""
    device_id = ent.get(CONF_DEVICE_ID)
    if device_id:
        devices = entry.options.get(CONF_DEVICES, [])
        device = next((d for d in devices if str(d.get("id")) == str(device_id)), None)
        if device:
            return DeviceInfo(
                identifiers={(DOMAIN, f"{entry.entry_id}_{device_id}")},
                name=device.get(CONF_NAME, f"Device {device_id}"),
                manufacturer=device.get(CONF_MANUFACTURER, "Modbus USB"),
                model=device.get(CONF_MODEL, "Modbus Device"),
                via_device=(DOMAIN, entry.entry_id),
            )
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer="Modbus USB",
        model="Modbus Serial Hub",
    )


def get_entity_picture(entry: ConfigEntry, ent: dict) -> str | None:
    """Return a device/template image URL for HA entity_picture, if set."""
    picture = ent.get(CONF_IMAGE)
    device_id = ent.get(CONF_DEVICE_ID)
    if device_id:
        devices = entry.options.get(CONF_DEVICES, [])
        device = next((d for d in devices if str(d.get("id")) == str(device_id)), None)
        if device and device.get(CONF_IMAGE):
            picture = device.get(CONF_IMAGE)
    return picture or None


class ModbusUsbCoordinator(DataUpdateCoordinator):
    """Polls the USB-connected Modbus controller and shares results with entities."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: Any,
        slave_id: int,
        scan_interval: int,
        entry_id: str,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="Modbus USB Controller",
            update_interval=timedelta(seconds=scan_interval),
        )
        self.client = client
        self.slave_id = slave_id
        self.entry_id = entry_id
        # entities list is read fresh from config_entry.options each refresh so
        # the options flow (add/edit/remove) takes effect without reload in most cases
        self.hass = hass
        # Health / diagnostics tracking
        self.diag: dict[str, Any] = {
            DIAG_TOTAL_READS: 0,
            DIAG_FAILED_READS: 0,
            DIAG_CONSECUTIVE_FAILURES: 0,
            DIAG_LAST_ERROR: None,
            DIAG_LAST_SUCCESS: None,
        }

    def _get_entities(self) -> list[dict]:
        entry = self.hass.config_entries.async_get_entry(self.entry_id)
        if entry is None:
            return []
        return entry.options.get(CONF_ENTITIES, [])

    def _get_device_slave_map(self) -> dict[str, int]:
        entry = self.hass.config_entries.async_get_entry(self.entry_id)
        if entry is None:
            return {}
        devices = entry.options.get(CONF_DEVICES, [])
        return {
            str(d.get("id")): int(d.get(CONF_SLAVE_ID, self.slave_id))
            for d in devices
            if "id" in d and d.get(CONF_SLAVE_ID) is not None
        }

    async def _async_update_data(self) -> dict[str, Any]:
        entities = self._get_entities()
        if not entities:
            return {}
        try:
            device_slave_map = self._get_device_slave_map()
            return await self.hass.async_add_executor_job(
                self._read_all, entities, device_slave_map
            )
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(f"Error communicating with Modbus device: {err}") from err

    def _read_all(
        self, entities: list[dict], device_slave_map: dict[str, int] | None = None
    ) -> dict[str, Any]:
        if not self.client.connected:
            self.client.connect()

        device_slave_map = device_slave_map or {}
        data: dict[str, Any] = {}
        for ent in entities:
            ent_id = ent["id"]
            if ent.get(CONF_ASSUMED_STATE):
                continue
            self.diag[DIAG_TOTAL_READS] += 1
            try:
                data[ent_id] = self._read_one(ent, device_slave_map)
                self.diag[DIAG_CONSECUTIVE_FAILURES] = 0
                self.diag[DIAG_LAST_SUCCESS] = datetime.now().isoformat()
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Failed reading %s: %s", ent.get("name", ent_id), err)
                data[ent_id] = None
                self.diag[DIAG_FAILED_READS] += 1
                self.diag[DIAG_CONSECUTIVE_FAILURES] += 1
                self.diag[DIAG_LAST_ERROR] = str(err)
        return data

    def _read_one(self, ent: dict, device_slave_map: dict[str, int] | None = None) -> Any:
        register_type = ent[CONF_REGISTER_TYPE]
        address = ent[CONF_ADDRESS]

        # Resolve slave ID: entity -> device -> hub default
        slave = ent.get(CONF_SLAVE_ID)
        if slave is None and device_slave_map and ent.get(CONF_DEVICE_ID):
            slave = device_slave_map.get(str(ent.get(CONF_DEVICE_ID)))
        if slave is None:
            slave = self.slave_id
        target_slave = int(slave)

        if register_type == REGISTER_TYPE_COIL:
            result = self.client.read_coils(address, 1, slave=target_slave)
            if result.isError():
                raise UpdateFailed(str(result))
            return bool(result.bits[0])

        if register_type == REGISTER_TYPE_DISCRETE:
            result = self.client.read_discrete_inputs(address, 1, slave=target_slave)
            if result.isError():
                raise UpdateFailed(str(result))
            return bool(result.bits[0])

        data_type = ent.get(CONF_DATA_TYPE, DATA_TYPE_UINT16)
        count = DATA_TYPE_WORD_COUNT.get(data_type, 1)
        scale = ent.get(CONF_SCALE, 1)

        if register_type == REGISTER_TYPE_HOLDING:
            result = self.client.read_holding_registers(address, count, slave=target_slave)
        elif register_type == REGISTER_TYPE_INPUT:
            result = self.client.read_input_registers(address, count, slave=target_slave)
        else:
            raise UpdateFailed(f"Unknown register type: {register_type}")

        if result.isError():
            raise UpdateFailed(str(result))

        value = _decode_words(result.registers, data_type)
        if scale not in (1, None):
            value = value * scale
        return value

    def write_coil(self, address: int, value: bool, slave: int | None = None) -> None:
        """Write a coil value (used by switches). Runs synchronously - call via executor."""
        if not self.client.connected:
            self.client.connect()
        target_slave = int(slave if slave is not None else self.slave_id)
        result = self.client.write_coil(address, value, slave=target_slave)
        if result.isError():
            raise UpdateFailed(str(result))

    def write_register(self, address: int, value: int, slave: int | None = None) -> None:
        """Write a single holding register (used by switches modeled as registers)."""
        if not self.client.connected:
            self.client.connect()
        target_slave = int(slave if slave is not None else self.slave_id)
        result = self.client.write_register(address, value, slave=target_slave)
        if result.isError():
            raise UpdateFailed(str(result))

    def write_registers_32bit(
        self, address: int, value: int, data_type: str, slave: int | None = None
    ) -> None:
        """Write two consecutive holding registers for 32-bit data types."""
        if not self.client.connected:
            self.client.connect()
        target_slave = int(slave if slave is not None else self.slave_id)
        if data_type in ("int32",):
            raw = struct.pack(">i", value)
        elif data_type in ("float32",):
            raw = struct.pack(">f", value)
        else:  # uint32
            raw = struct.pack(">I", value)
        high, low = struct.unpack(">HH", raw)
        result = self.client.write_registers(address, [high, low], slave=target_slave)
        if result.isError():
            raise UpdateFailed(str(result))

    def read_register_raw(
        self, address: int, register_type: str, data_type: str, slave: int | None = None
    ) -> Any:
        """Perform a one-shot read of a register for the diagnostics/service call."""
        if not self.client.connected:
            self.client.connect()
        target_slave = int(slave if slave is not None else self.slave_id)
        count = DATA_TYPE_WORD_COUNT.get(data_type, 1)
        if register_type == REGISTER_TYPE_COIL:
            result = self.client.read_coils(address, 1, slave=target_slave)
            if result.isError():
                raise UpdateFailed(str(result))
            return bool(result.bits[0])
        if register_type == REGISTER_TYPE_DISCRETE:
            result = self.client.read_discrete_inputs(address, 1, slave=target_slave)
            if result.isError():
                raise UpdateFailed(str(result))
            return bool(result.bits[0])
        if register_type == REGISTER_TYPE_HOLDING:
            result = self.client.read_holding_registers(address, count, slave=target_slave)
        elif register_type == REGISTER_TYPE_INPUT:
            result = self.client.read_input_registers(address, count, slave=target_slave)
        else:
            raise UpdateFailed(f"Unknown register type: {register_type}")
        if result.isError():
            raise UpdateFailed(str(result))
        return _decode_words(result.registers, data_type)


# Changelog:
# 2026-09-06 — Skip polling assumed_state entities; expose get_entity_picture from device image.
# Date modified: 2026-09-06

