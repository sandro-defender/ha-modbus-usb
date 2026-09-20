"""WebSocket commands for diagnostics, probes, scans, and log tools."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv

from ..const import (
    CONF_ADDRESS,
    CONF_DEVICE_ID,
    CONF_DEVICES,
    CONF_ENTITIES,
    CONF_ENTITY_ID,
    CONF_ENTITY_TYPE,
    CONF_NAME,
    CONF_REGISTER_TYPE,
    DOMAIN,
    REGISTER_TYPE_COIL,
)
from ..templates import (
    async_load_templates,
)
from .helpers import (
    _async_write_configured_switch,
    _entity_slave_id,
    _get_entry,
    _probe_request_frame,
)

_LOGGER = logging.getLogger(__name__)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "modbus_usb/diagnostic_read",
        vol.Required("entry_id"): cv.string,
        vol.Required("address"): vol.Coerce(int),
        vol.Required("register_type"): vol.In(["holding", "input", "coil", "discrete"]),
        vol.Required("data_type"): vol.In(
            ["uint16", "int16", "uint32", "int32", "float32"]
        ),
        vol.Required("slave_id"): vol.All(vol.Coerce(int), vol.Range(min=1, max=247)),
    }
)
@websocket_api.async_response
async def ws_diagnostic_read(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Read one register and return its decoded result directly to the panel."""
    try:
        coordinator = hass.data[DOMAIN][msg["entry_id"]]
        value = await hass.async_add_executor_job(
            coordinator.read_register_raw,
            msg["address"],
            msg["register_type"],
            msg["data_type"],
            msg["slave_id"],
        )
        connection.send_result(msg["id"], {"value": value})
    except Exception as err:
        _LOGGER.warning("Diagnostic read failed: %s", err)
        connection.send_error(msg["id"], "read_failed", str(err))


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "modbus_usb/diagnostic_write",
        vol.Required("entry_id"): cv.string,
        vol.Required("address"): vol.Coerce(int),
        vol.Required("register_type"): vol.In(["holding", "coil"]),
        vol.Required("value"): vol.Coerce(int),
        vol.Required("slave_id"): vol.All(vol.Coerce(int), vol.Range(min=1, max=247)),
    }
)
@websocket_api.async_response
async def ws_diagnostic_write(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Write one coil or holding register directly from Diagnostics."""
    try:
        coordinator = hass.data[DOMAIN][msg["entry_id"]]
        if msg["register_type"] == REGISTER_TYPE_COIL:
            await hass.async_add_executor_job(
                coordinator.write_coil,
                msg["address"],
                bool(msg["value"]),
                msg["slave_id"],
            )
        else:
            await hass.async_add_executor_job(
                coordinator.write_register,
                msg["address"],
                msg["value"],
                msg["slave_id"],
            )
        connection.send_result(msg["id"], {"success": True})
    except Exception as err:
        _LOGGER.warning("Diagnostic write failed: %s", err)
        connection.send_error(msg["id"], "write_failed", str(err))


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "modbus_usb/manual_hex_write",
        vol.Required("entry_id"): cv.string,
        vol.Required("frame_hex"): cv.string,
        vol.Optional("generate_crc", default=False): bool,
        vol.Required("confirmed"): True,
    }
)
@websocket_api.async_response
async def ws_manual_hex_write(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Send one explicitly confirmed, CRC-checked standard Modbus write frame."""
    try:
        coordinator = hass.data[DOMAIN][msg["entry_id"]]
        result = await hass.async_add_executor_job(
            coordinator.execute_manual_hex_write,
            msg["frame_hex"],
            msg.get("generate_crc", False),
        )
        connection.send_result(msg["id"], result)
    except Exception as err:
        _LOGGER.warning("Manual hexadecimal Modbus write failed: %s", err)
        connection.send_error(msg["id"], "manual_hex_write_failed", str(err))


_PROBE_FUNCTIONS = {
    "coil": (0x01, "Read Coils"),
    "discrete": (0x02, "Read Discrete Inputs"),
    "holding": (0x03, "Read Holding Registers"),
    "input": (0x04, "Read Input Registers"),
}

_PROBE_STOP_EVENTS: dict[str, asyncio.Event] = {}


@websocket_api.websocket_command(
    {
        vol.Required("type"): "modbus_usb/probe_registers",
        vol.Required("entry_id"): cv.string,
        vol.Required("slave_id"): vol.All(vol.Coerce(int), vol.Range(min=1, max=247)),
        vol.Required("start_address"): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=65535)
        ),
        vol.Required("end_address"): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=65535)
        ),
        vol.Required("register_types"): vol.All(
            [vol.In(list(_PROBE_FUNCTIONS))], vol.Length(min=1, max=4)
        ),
    }
)
@websocket_api.async_response
async def ws_probe_registers(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Safely probe standard *read-only* Modbus functions at a few addresses.

    Unknown devices often use vendor-specific write values.  This endpoint never
    writes anything and intentionally limits the address span, avoiding a long
    request flood when the selected slave is absent or wired incorrectly.
    """
    start_address = msg["start_address"]
    end_address = msg["end_address"]
    if end_address < start_address:
        connection.send_error(
            msg["id"], "invalid_range", "End address must be at or above start address"
        )
        return
    if end_address - start_address > 3:
        connection.send_error(
            msg["id"],
            "range_too_large",
            "The safe probe can check at most four consecutive addresses at once",
        )
        return

    try:
        coordinator = hass.data[DOMAIN][msg["entry_id"]]
        results: list[dict[str, Any]] = []
        stop_event = asyncio.Event()
        _PROBE_STOP_EVENTS[msg["entry_id"]] = stop_event
        stopped = False
        try:
            for register_type in msg["register_types"]:
                function_code, function_name = _PROBE_FUNCTIONS[register_type]
                for address in range(start_address, end_address + 1):
                    if stop_event.is_set():
                        stopped = True
                        break
                    request = _probe_request_frame(
                        msg["slave_id"], function_code, address
                    )
                    try:
                        value = await hass.async_add_executor_job(
                            coordinator.read_register_raw,
                            address,
                            register_type,
                            "uint16",
                            msg["slave_id"],
                        )
                        value_hint = (
                            "A binary value; this may be a state bit."
                            if isinstance(value, bool)
                            else "A raw 16-bit register value; meaning is device-specific."
                        )
                        results.append(
                            {
                                "register_type": register_type,
                                "function_code": f"0x{function_code:02X}",
                                "function_name": function_name,
                                "address": address,
                                "request": request,
                                "status": "response",
                                "value": value,
                                "meaning": value_hint,
                            }
                        )
                    except Exception as err:
                        results.append(
                            {
                                "register_type": register_type,
                                "function_code": f"0x{function_code:02X}",
                                "function_name": function_name,
                                "address": address,
                                "request": request,
                                "status": "no_response",
                                "error": str(err),
                                "meaning": "No valid response. Check slave ID, baud rate, wiring, and register map.",
                            }
                        )
                    # Keep the Home Assistant event loop responsive between probes.
                    await asyncio.sleep(0)
                if stopped:
                    break
        finally:
            if _PROBE_STOP_EVENTS.get(msg["entry_id"]) is stop_event:
                _PROBE_STOP_EVENTS.pop(msg["entry_id"], None)
        connection.send_result(msg["id"], {"results": results, "stopped": stopped})
    except Exception as err:
        _LOGGER.warning("Read-only Modbus probe failed: %s", err)
        connection.send_error(msg["id"], "probe_failed", str(err))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "modbus_usb/stop_probe_registers",
        vol.Required("entry_id"): cv.string,
    }
)
@websocket_api.async_response
async def ws_stop_probe_registers(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Request that a running read-only board probe stop after its current read."""
    stop_event = _PROBE_STOP_EVENTS.get(msg["entry_id"])
    if stop_event is not None:
        stop_event.set()
    connection.send_result(msg["id"], {"stopping": stop_event is not None})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "modbus_usb/test_device_entities",
        vol.Required("entry_id"): cv.string,
        vol.Required("device_id"): cv.string,
    }
)
@websocket_api.async_response
async def ws_test_device_entities(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Test all configured entities; safely cycle switches and read the rest."""
    try:
        entry = _get_entry(hass, msg["entry_id"])
        device = next(
            (
                item
                for item in entry.options.get(CONF_DEVICES, [])
                if item.get(CONF_ENTITY_ID) == msg["device_id"]
            ),
            None,
        )
        if device is None:
            raise ValueError("Configured device was not found")

        entities = [
            item
            for item in entry.options.get(CONF_ENTITIES, [])
            if item.get(CONF_DEVICE_ID) == msg["device_id"]
        ]
        if not entities:
            raise ValueError("This device has no configured entities to test")

        started = time.monotonic()
        results: list[dict[str, Any]] = []
        for entity in entities:
            result: dict[str, Any] = {
                "name": entity.get(CONF_NAME, entity.get(CONF_ENTITY_ID, "Switch")),
                "address": entity.get(CONF_ADDRESS),
                "register_type": entity.get(CONF_REGISTER_TYPE),
                "entity_type": entity.get(CONF_ENTITY_TYPE),
            }
            if entity.get(CONF_ENTITY_TYPE) != "switch":
                try:
                    slave_id = _entity_slave_id(entry, entity)
                    coordinator = hass.data[DOMAIN][entry.entry_id]
                    value = await hass.async_add_executor_job(
                        coordinator.read_entity_value, entity, slave_id
                    )
                    result["slave_id"] = slave_id
                    result["read"] = {"success": True, "value": value}
                except Exception as err:
                    result["read"] = {"success": False, "error": str(err)}
                results.append(result)
                await asyncio.sleep(0.1)
                continue

            result["on"] = {"success": False}
            result["off"] = {"success": False}
            try:
                result["slave_id"] = await _async_write_configured_switch(
                    hass, entry, entity, True
                )
                result["on"] = {"success": True, "message": "acknowledged"}
            except Exception as err:
                result["on"] = {"success": False, "error": str(err)}

            # Keep each output on briefly, then always attempt the safe OFF
            # command even if the ON request failed.
            await asyncio.sleep(0.35)
            try:
                result["slave_id"] = await _async_write_configured_switch(
                    hass, entry, entity, False
                )
                result["off"] = {"success": True, "message": "acknowledged"}
            except Exception as err:
                result["off"] = {"success": False, "error": str(err)}
            results.append(result)
            await asyncio.sleep(0.15)

        successful_steps = sum(
            int(step["read"].get("success", False))
            if "read" in step
            else sum(int(step[phase].get("success", False)) for phase in ("on", "off"))
            for step in results
        )
        total_steps = sum(1 if "read" in step else 2 for step in results)
        connection.send_result(
            msg["id"],
            {
                "device_name": device.get(CONF_NAME, msg["device_id"]),
                "entity_count": len(results),
                "switch_count": sum(
                    item.get("entity_type") == "switch" for item in results
                ),
                "read_count": sum(
                    item.get("entity_type") != "switch" for item in results
                ),
                "successful_steps": successful_steps,
                "failed_steps": total_steps - successful_steps,
                "duration_ms": round((time.monotonic() - started) * 1000, 1),
                "results": results,
            },
        )
    except Exception as err:
        _LOGGER.warning("Device entity test failed: %s", err)
        connection.send_error(msg["id"], "device_test_failed", str(err))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "modbus_usb/verify_device_reads",
        vol.Required("entry_id"): cv.string,
        vol.Required("device_id"): cv.string,
    }
)
@websocket_api.async_response
async def ws_verify_device_reads(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Verify configured device reads without operating any outputs.

    This is deliberately separate from the legacy full device test: a template
    verification run reads every non-switch entity and reports switches as
    skipped, so it can be used on connected equipment without cycling relays.
    """
    try:
        entry = _get_entry(hass, msg["entry_id"])
        device = next(
            (
                item
                for item in entry.options.get(CONF_DEVICES, [])
                if item.get("id") == msg["device_id"]
            ),
            None,
        )
        if device is None:
            raise ValueError("Configured device was not found")
        entities = [
            item
            for item in entry.options.get(CONF_ENTITIES, [])
            if item.get(CONF_DEVICE_ID) == msg["device_id"]
        ]
        if not entities:
            raise ValueError(
                "This device has no configured template entities to verify"
            )

        coordinator = hass.data[DOMAIN][entry.entry_id]
        started = time.monotonic()
        results: list[dict[str, Any]] = []
        for entity in entities:
            result: dict[str, Any] = {
                "name": entity.get(CONF_NAME, entity.get(CONF_ENTITY_ID, "Entity")),
                "address": entity.get(CONF_ADDRESS),
                "register_type": entity.get(CONF_REGISTER_TYPE),
                "entity_type": entity.get(CONF_ENTITY_TYPE),
            }
            if entity.get(CONF_ENTITY_TYPE) == "switch":
                result["status"] = "skipped"
                result["reason"] = (
                    "Output control is excluded from read-only verification"
                )
            else:
                try:
                    slave_id = _entity_slave_id(entry, entity)
                    value = await hass.async_add_executor_job(
                        coordinator.read_entity_value, entity, slave_id
                    )
                    result.update(
                        {"status": "pass", "slave_id": slave_id, "value": value}
                    )
                except Exception as err:
                    result.update({"status": "fail", "error": str(err)})
                await asyncio.sleep(0)
            results.append(result)

        checked = [item for item in results if item["status"] != "skipped"]
        passed = sum(item["status"] == "pass" for item in checked)
        connection.send_result(
            msg["id"],
            {
                "device_name": device.get(CONF_NAME, msg["device_id"]),
                "checked": len(checked),
                "passed": passed,
                "failed": len(checked) - passed,
                "skipped": len(results) - len(checked),
                "duration_ms": round((time.monotonic() - started) * 1000, 1),
                "results": results,
            },
        )
    except Exception as err:
        _LOGGER.warning("Read-only template verification failed: %s", err)
        connection.send_error(msg["id"], "template_verify_failed", str(err))


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "modbus_usb/clear_diagnostic_log",
        vol.Required("entry_id"): cv.string,
    }
)
@websocket_api.async_response
async def ws_clear_diagnostic_log(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Clear the sidebar's in-memory RS-485 activity log."""
    try:
        hass.data[DOMAIN][msg["entry_id"]].clear_transaction_log()
        connection.send_result(msg["id"], {"success": True})
    except (KeyError, AttributeError) as err:
        connection.send_error(msg["id"], "not_found", str(err))


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "modbus_usb/scan_bus",
        vol.Required("entry_id"): cv.string,
        vol.Required("baudrates"): [
            vol.All(vol.Coerce(int), vol.Range(min=1200, max=115200))
        ],
        vol.Optional("parities", default=["N"]): [vol.In(["N", "E", "O"])],
        vol.Optional("start_slave", default=1): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=247)
        ),
        vol.Optional("end_slave", default=20): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=247)
        ),
    }
)
@websocket_api.async_response
async def ws_scan_bus(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Probe a bounded range of Modbus slave IDs and serial speeds."""
    try:
        coordinator = hass.data[DOMAIN][msg["entry_id"]]
        templates = await async_load_templates(hass)
        result = await hass.async_add_executor_job(
            coordinator.scan_bus,
            msg["baudrates"],
            msg["parities"],
            msg["start_slave"],
            msg["end_slave"],
            templates,
        )
        connection.send_result(msg["id"], result)
    except Exception as err:
        _LOGGER.warning("RS-485 bus scan failed: %s", err)
        connection.send_error(msg["id"], "scan_failed", str(err))
