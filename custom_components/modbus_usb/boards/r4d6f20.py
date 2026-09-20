"""Eletechsup R4D6F20 grouped block reads (Command 1 and Command 2 maps)."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from homeassistant.helpers.update_coordinator import UpdateFailed

from ..const import (
    CONF_ADDRESS,
    CONF_DATA_TYPE,
    CONF_REGISTER_TYPE,
    CONF_SCALE,
    DATA_TYPE_UINT16,
    REGISTER_TYPE_COIL,
    REGISTER_TYPE_DISCRETE,
    REGISTER_TYPE_HOLDING,
    REGISTER_TYPE_INPUT,
)
from ..decoding import as_float
from ..models import EntityConfig

if TYPE_CHECKING:
    from ..coordinator import ModbusUsbCoordinator


# (start address, register count) grouped into one RTU request each.
COMMAND1_RANGES = ((0, 20), (128, 2), (160, 2))
# (register type, pymodbus method, start, count) for Command 2 (M0 shorted).
COMMAND2_RANGES = (
    (REGISTER_TYPE_COIL, "read_coils", 0, 20),
    (REGISTER_TYPE_DISCRETE, "read_discrete_inputs", 0, 2),
    (REGISTER_TYPE_INPUT, "read_input_registers", 0, 2),
)


def read_command1_blocks(
    coord: ModbusUsbCoordinator, entities: list[EntityConfig], slave: int
) -> tuple[dict[str, Any], set[str]]:
    """Read the standard R4D6F20 Command 1 ranges in three RTU requests.

    Returns the decoded values plus the entity IDs covered by a block, so
    the caller only skips per-entity polling for entities that were read.
    """
    data: dict[str, Any] = {}
    handled: set[str] = set()
    for start, count in COMMAND1_RANGES:
        members = [
            item
            for item in entities
            if item.get(CONF_REGISTER_TYPE) == REGISTER_TYPE_HOLDING
            and item.get(CONF_DATA_TYPE, DATA_TYPE_UINT16) == DATA_TYPE_UINT16
            and start <= int(item.get(CONF_ADDRESS, -1)) < start + count
        ]
        if not members:
            continue
        handled.update(item["id"] for item in members)
        started = datetime.now()
        try:
            with coord._serial_lock:
                coord._ensure_connected()
                result = coord._call_modbus(
                    "read_holding_registers", start, count=count, slave=slave
                )
                if result.isError():
                    raise UpdateFailed(str(result))
            for item in members:
                value = result.registers[int(item[CONF_ADDRESS]) - start]
                scale = as_float(item.get(CONF_SCALE), 1)
                data[item["id"]] = value if scale == 1 else value * scale
            coord._record_transaction(
                "read_holding",
                slave=slave,
                address=start,
                count=count,
                result="block",
                duration_ms=(datetime.now() - started).total_seconds() * 1000,
            )
        except Exception as err:
            for item in members:
                data[item["id"]] = None
            coord._record_transaction(
                "read_holding",
                slave=slave,
                address=start,
                count=count,
                error=err,
                duration_ms=(datetime.now() - started).total_seconds() * 1000,
            )
    return data, handled


def read_command2_blocks(
    coord: ModbusUsbCoordinator, entities: list[EntityConfig], slave: int
) -> tuple[dict[str, Any], set[str]]:
    """Read documented R4D6F20 Command 2 ranges in three RTU requests.

    Returns the decoded values plus the entity IDs covered by a block so
    entities outside the Command 2 maps keep their individual polling.
    """
    data: dict[str, Any] = {}
    handled: set[str] = set()
    for register_type, method, start, count in COMMAND2_RANGES:
        members = [
            item
            for item in entities
            if item.get(CONF_REGISTER_TYPE) == register_type
            and start <= int(item.get(CONF_ADDRESS, -1)) < start + count
        ]
        if not members:
            continue
        handled.update(item["id"] for item in members)
        started = datetime.now()
        try:
            with coord._serial_lock:
                coord._ensure_connected()
                result = coord._call_modbus(method, start, count=count, slave=slave)
                if result.isError():
                    raise UpdateFailed(str(result))
            for item in members:
                index = int(item[CONF_ADDRESS]) - start
                value = (
                    bool(result.bits[index])
                    if register_type in (REGISTER_TYPE_COIL, REGISTER_TYPE_DISCRETE)
                    else result.registers[index]
                )
                scale = as_float(item.get(CONF_SCALE), 1)
                data[item["id"]] = value if scale == 1 else value * scale
            coord._record_transaction(
                f"read_{register_type}",
                slave=slave,
                address=start,
                count=count,
                result="block",
                duration_ms=(datetime.now() - started).total_seconds() * 1000,
            )
        except Exception as err:
            for item in members:
                data[item["id"]] = None
            coord._record_transaction(
                f"read_{register_type}",
                slave=slave,
                address=start,
                count=count,
                error=err,
                duration_ms=(datetime.now() - started).total_seconds() * 1000,
            )
    return data, handled
