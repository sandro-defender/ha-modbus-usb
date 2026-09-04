"""Data update coordinator for the Modbus USB Controller integration."""
from __future__ import annotations

import logging
import struct
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_ADDRESS,
    CONF_DATA_TYPE,
    CONF_ENTITIES,
    CONF_ENTITY_TYPE,
    CONF_REGISTER_TYPE,
    CONF_SCALE,
    DATA_TYPE_FLOAT32,
    DATA_TYPE_INT16,
    DATA_TYPE_INT32,
    DATA_TYPE_UINT16,
    DATA_TYPE_UINT32,
    DATA_TYPE_WORD_COUNT,
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

    def _get_entities(self) -> list[dict]:
        entry = self.hass.config_entries.async_get_entry(self.entry_id)
        if entry is None:
            return []
        return entry.options.get(CONF_ENTITIES, [])

    async def _async_update_data(self) -> dict[str, Any]:
        entities = self._get_entities()
        if not entities:
            return {}
        try:
            return await self.hass.async_add_executor_job(self._read_all, entities)
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(f"Error communicating with Modbus device: {err}") from err

    def _read_all(self, entities: list[dict]) -> dict[str, Any]:
        if not self.client.connected:
            self.client.connect()

        data: dict[str, Any] = {}
        for ent in entities:
            ent_id = ent["id"]
            try:
                data[ent_id] = self._read_one(ent)
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Failed reading %s: %s", ent.get("name", ent_id), err)
                data[ent_id] = None
        return data

    def _read_one(self, ent: dict) -> Any:
        register_type = ent[CONF_REGISTER_TYPE]
        address = ent[CONF_ADDRESS]

        if register_type == REGISTER_TYPE_COIL:
            result = self.client.read_coils(address, 1, slave=self.slave_id)
            if result.isError():
                raise UpdateFailed(str(result))
            return bool(result.bits[0])

        if register_type == REGISTER_TYPE_DISCRETE:
            result = self.client.read_discrete_inputs(address, 1, slave=self.slave_id)
            if result.isError():
                raise UpdateFailed(str(result))
            return bool(result.bits[0])

        data_type = ent.get(CONF_DATA_TYPE, DATA_TYPE_UINT16)
        count = DATA_TYPE_WORD_COUNT.get(data_type, 1)
        scale = ent.get(CONF_SCALE, 1)

        if register_type == REGISTER_TYPE_HOLDING:
            result = self.client.read_holding_registers(address, count, slave=self.slave_id)
        elif register_type == REGISTER_TYPE_INPUT:
            result = self.client.read_input_registers(address, count, slave=self.slave_id)
        else:
            raise UpdateFailed(f"Unknown register type: {register_type}")

        if result.isError():
            raise UpdateFailed(str(result))

        value = _decode_words(result.registers, data_type)
        if scale not in (1, None):
            value = value * scale
        return value

    def write_coil(self, address: int, value: bool) -> None:
        """Write a coil value (used by switches). Runs synchronously - call via executor."""
        if not self.client.connected:
            self.client.connect()
        result = self.client.write_coil(address, value, slave=self.slave_id)
        if result.isError():
            raise UpdateFailed(str(result))

    def write_register(self, address: int, value: int) -> None:
        """Write a single holding register (used by switches modeled as registers)."""
        if not self.client.connected:
            self.client.connect()
        result = self.client.write_register(address, value, slave=self.slave_id)
        if result.isError():
            raise UpdateFailed(str(result))
