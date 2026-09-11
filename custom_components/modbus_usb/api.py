"""WebSocket and REST API endpoints for Modbus USB Controller.

Provides complete sidebar UI management for:
  - Hub serial and polling configuration
  - Separated, editable devices
  - Entity management (sensors, switches, binary sensors, numbers)
  - YAML template management (CRUD)
  - Template application to devices
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

import voluptuous as vol
from aiohttp import web
from homeassistant.components import websocket_api
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_ADDRESS,
    CONF_ADDRESSES,
    CONF_BAUDRATE,
    CONF_BYTESIZE,
    CONF_DATA_TYPE,
    CONF_DEVICE_CONTROLS,
    CONF_DESCRIPTION,
    CONF_DEVICES,
    CONF_DEVICE_CLASS,
    CONF_DEVICE_ID,
    CONF_ENTITIES,
    CONF_ENTITY_ID,
    CONF_ENTITY_TYPE,
    CONF_MANUFACTURER,
    CONF_MAX_VALUE,
    CONF_MIN_VALUE,
    CONF_MODE,
    CONF_MODEL,
    CONF_NAME,
    CONF_OFF_VALUE,
    CONF_ON_VALUE,
    CONF_IMAGE,
    CONF_INFO_URL,
    CONF_PARITY,
    CONF_PORT,
    CONF_REGISTER_TYPE,
    CONF_SCALE,
    CONF_SCAN_INTERVAL,
    CONF_SLAVE_ID,
    CONF_STATE_ON_VALUE,
    CONF_STATE_CLASS,
    CONF_STEP,
    CONF_STOPBITS,
    CONF_UNIT_OF_MEASUREMENT,
    DOMAIN,
    REGISTER_TYPE_COIL,
)
from .templates import (
    async_delete_template,
    async_load_templates,
    async_save_template,
)
from .coordinator import is_r413e16_switch_config

_LOGGER = logging.getLogger(__name__)

_GITHUB_REPOSITORY = "sandro-defender/ha-modbus-usb"


def _local_version() -> str:
    """Return the version packaged with this installed custom component."""
    manifest_path = Path(__file__).with_name("manifest.json")
    with manifest_path.open(encoding="utf-8") as manifest_file:
        return str(json.load(manifest_file)["version"])


def _version_key(version: str) -> tuple[int, ...]:
    """Convert a simple release version to comparable integer components."""
    clean = version.strip().lstrip("vV").split("-", maxsplit=1)[0]
    parts = [int(part) for part in clean.split(".")]
    # Treat equivalent short semantic versions consistently: 2.1 is 2.1.0.
    return tuple((parts + [0, 0, 0])[:3])


def _find_hacs_update_entity(hass: HomeAssistant) -> str | None:
    """Return this integration's HACS update entity when Home Assistant has one."""
    for state in hass.states.async_all("update"):
        attributes = state.attributes
        searchable = " ".join(
            str(value)
            for value in (
                state.entity_id,
                attributes.get("friendly_name", ""),
                attributes.get("repository", ""),
                attributes.get("release_url", ""),
                attributes.get("url", ""),
            )
        ).lower()
        # An update entity reports "on" only after HACS has discovered a
        # package update. GitHub can publish a release a few minutes earlier,
        # so do not ask HACS to install while it still reports no update.
        if (
            state.state == "on"
            and ("ha-modbus-usb" in searchable or "modbus usb controller" in searchable)
        ):
            return state.entity_id
    return None


async def _async_update_status(hass: HomeAssistant) -> dict[str, Any]:
    """Read the latest GitHub release and compare it to the installed version."""
    current_version = _local_version()
    session = async_get_clientsession(hass)
    release_url = f"https://github.com/{_GITHUB_REPOSITORY}/releases/latest"
    async with session.get(
        f"https://api.github.com/repos/{_GITHUB_REPOSITORY}/releases/latest",
        headers={"Accept": "application/vnd.github+json"},
        timeout=10,
    ) as response:
        if response.status != 200:
            raise ValueError(f"GitHub release check failed (HTTP {response.status})")
        release = await response.json()
    latest_version = str(release.get("tag_name", "")).lstrip("vV")
    if not latest_version:
        raise ValueError("GitHub's latest release has no version tag")
    try:
        update_available = _version_key(latest_version) > _version_key(current_version)
    except ValueError as err:
        raise ValueError("GitHub release version is not a supported numeric version") from err
    return {
        "current_version": current_version,
        "latest_version": latest_version,
        "update_available": update_available,
        "release_url": release.get("html_url") or release_url,
        "update_entity_id": _find_hacs_update_entity(hass),
    }


def _list_serial_ports() -> list[dict[str, str]]:
    """Return serial ports visible to the Home Assistant host."""
    try:
        from serial.tools import list_ports
    except ImportError:
        _LOGGER.warning("USB port scanning is unavailable because pyserial is missing")
        return []

    ports: list[dict[str, str]] = []
    for port in list_ports.comports():
        details = " · ".join(
            value for value in (port.manufacturer, port.product, port.hwid)
            if value and value != "n/a"
        )
        ports.append({
            "port": port.device,
            "description": port.description or "Serial device",
            "details": details,
        })
    return sorted(ports, key=lambda item: item["port"])


def _get_entry(hass: HomeAssistant, entry_id: str):
    """Retrieve config entry by ID or raise an error."""
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None:
        raise ValueError(f"Config entry '{entry_id}' not found")
    return entry


def _format_entry_data(hass: HomeAssistant, entry) -> dict[str, Any]:
    """Serialize config entry for UI consumption."""
    options = dict(entry.options or {})
    devices = [dict(device) for device in options.get(CONF_DEVICES, [])]
    entities = [dict(entity) for entity in options.get(CONF_ENTITIES, [])]

    # Auto-synthesize a default device if entities exist without device_id
    has_unassigned = any(not e.get(CONF_DEVICE_ID) for e in entities)
    if (not devices or has_unassigned) and entities:
        default_dev_id = f"default_{entry.entry_id[:6]}"
        if not any(d.get("id") == default_dev_id for d in devices):
            devices.insert(
                0,
                {
                    "id": default_dev_id,
                    "name": entry.title or "Modbus Device",
                    "slave_id": entry.data.get(CONF_SLAVE_ID, 1),
                    "manufacturer": "Modbus USB",
                    "model": "Generic Device",
                    "description": "Auto-created device for unassigned entities",
                },
            )
        # Link unassigned entities to default device
        for ent in entities:
            if not ent.get(CONF_DEVICE_ID):
                ent[CONF_DEVICE_ID] = default_dev_id

    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    diagnostics = coordinator.get_diagnostics() if coordinator else {
        "connected": False,
        "entry_id": entry.entry_id,
        "health": {},
        "transactions": [],
    }
    transactions = diagnostics["transactions"]
    entity_registry = er.async_get(hass)
    entity_domains = {
        "sensor": "sensor",
        "switch": "switch",
        "binary_sensor": "binary_sensor",
        "number": "number",
    }
    for entity in entities:
        domain = entity_domains.get(entity.get(CONF_ENTITY_TYPE))
        entity_id = entity.get(CONF_ENTITY_ID)
        if domain and entity_id:
            ha_entity_id = entity_registry.async_get_entity_id(
                domain, DOMAIN, f"{entry.entry_id}_{entity_id}"
            )
            if ha_entity_id:
                entity["ha_entity_id"] = ha_entity_id
        # A tested R413E16 channel has verified function-03 feedback. Include
        # that coordinator state so the sidebar cannot show a stale browser
        # snapshot while HA is processing its entity-state event.
        if coordinator and entity_id:
            reported_state = coordinator.get_command_state(str(entity_id))
            if reported_state is not None:
                entity["reported_state"] = "on" if reported_state else "off"
    diagnostics["devices"] = []
    for device in devices:
        slave_id = int(device.get(CONF_SLAVE_ID, entry.data.get(CONF_SLAVE_ID, 1)))
        latest = next(
            (item for item in transactions if item.get("slave") == slave_id), None
        )
        diagnostics["devices"].append({
            "device_id": device.get("id"),
            "slave_id": slave_id,
            "status": latest.get("status") if latest else "unknown",
            "last_operation": latest.get("operation") if latest else None,
            "last_seen": latest.get("timestamp") if latest else None,
            "last_error": latest.get("error") if latest and latest.get("status") == "error" else None,
        })
    return {
        "entry_id": entry.entry_id,
        "title": entry.title,
        "hub": {
            CONF_PORT: entry.data.get(CONF_PORT),
            CONF_BAUDRATE: entry.data.get(CONF_BAUDRATE),
            CONF_BYTESIZE: entry.data.get(CONF_BYTESIZE),
            CONF_PARITY: entry.data.get(CONF_PARITY),
            CONF_STOPBITS: entry.data.get(CONF_STOPBITS),
            CONF_SLAVE_ID: entry.data.get(CONF_SLAVE_ID, 1),
            CONF_SCAN_INTERVAL: options.get(CONF_SCAN_INTERVAL, 10),
        },
        "devices": devices,
        "entities": entities,
        "diagnostics": diagnostics,
    }


# ─────────────────────────────────────────────────────────────────────────────
# WebSocket Commands
# ─────────────────────────────────────────────────────────────────────────────

@websocket_api.websocket_command({
    vol.Required("type"): "modbus_usb/get_data",
    vol.Optional("entry_id"): cv.string,
})
@websocket_api.async_response
async def ws_get_data(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Return all hubs, separated devices, entities, and templates."""
    try:
        entries = hass.config_entries.async_entries(DOMAIN)
        entries_data = [_format_entry_data(hass, e) for e in entries]
        templates = await async_load_templates(hass)
        connection.send_result(
            msg["id"],
            {
                "entries": entries_data,
                "templates": templates,
            },
        )
    except Exception as err:
        _LOGGER.error("ws_get_data failed: %s", err, exc_info=True)
        connection.send_error(msg["id"], "error", str(err))


@websocket_api.websocket_command({
    vol.Required("type"): "modbus_usb/diagnostic_read",
    vol.Required("entry_id"): cv.string,
    vol.Required("address"): vol.Coerce(int),
    vol.Required("register_type"): vol.In(["holding", "input", "coil", "discrete"]),
    vol.Required("data_type"): vol.In(["uint16", "int16", "uint32", "int32", "float32"]),
    vol.Required("slave_id"): vol.All(vol.Coerce(int), vol.Range(min=1, max=247)),
})
@websocket_api.async_response
async def ws_diagnostic_read(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Read one register and return its decoded result directly to the panel."""
    try:
        coordinator = hass.data[DOMAIN][msg["entry_id"]]
        value = await hass.async_add_executor_job(
            coordinator.read_register_raw,
            msg["address"], msg["register_type"], msg["data_type"], msg["slave_id"],
        )
        connection.send_result(msg["id"], {"value": value})
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("Diagnostic read failed: %s", err)
        connection.send_error(msg["id"], "read_failed", str(err))


@websocket_api.websocket_command({
    vol.Required("type"): "modbus_usb/diagnostic_write",
    vol.Required("entry_id"): cv.string,
    vol.Required("address"): vol.Coerce(int),
    vol.Required("register_type"): vol.In(["holding", "coil"]),
    vol.Required("value"): vol.Coerce(int),
    vol.Required("slave_id"): vol.All(vol.Coerce(int), vol.Range(min=1, max=247)),
})
@websocket_api.async_response
async def ws_diagnostic_write(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Write one coil or holding register directly from Diagnostics."""
    try:
        coordinator = hass.data[DOMAIN][msg["entry_id"]]
        if msg["register_type"] == REGISTER_TYPE_COIL:
            await hass.async_add_executor_job(
                coordinator.write_coil, msg["address"], bool(msg["value"]), msg["slave_id"]
            )
        else:
            await hass.async_add_executor_job(
                coordinator.write_register, msg["address"], msg["value"], msg["slave_id"]
            )
        connection.send_result(msg["id"], {"success": True})
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("Diagnostic write failed: %s", err)
        connection.send_error(msg["id"], "write_failed", str(err))


_PROBE_FUNCTIONS = {
    "coil": (0x01, "Read Coils"),
    "discrete": (0x02, "Read Discrete Inputs"),
    "holding": (0x03, "Read Holding Registers"),
    "input": (0x04, "Read Input Registers"),
}
_PROBE_STOP_EVENTS: dict[str, asyncio.Event] = {}


def _probe_request_frame(slave_id: int, function_code: int, address: int) -> str:
    """Return the visible portion of a one-item Modbus RTU read request.

    The CRC is deliberately represented rather than calculated here: pymodbus
    owns frame generation and may use a different transport implementation.
    """
    return (
        f"{slave_id:02X} {function_code:02X} {address >> 8:02X} "
        f"{address & 0xFF:02X} 00 01 [CRC]"
    )


@websocket_api.websocket_command({
    vol.Required("type"): "modbus_usb/probe_registers",
    vol.Required("entry_id"): cv.string,
    vol.Required("slave_id"): vol.All(vol.Coerce(int), vol.Range(min=1, max=247)),
    vol.Required("start_address"): vol.All(vol.Coerce(int), vol.Range(min=0, max=65535)),
    vol.Required("end_address"): vol.All(vol.Coerce(int), vol.Range(min=0, max=65535)),
    vol.Required("register_types"): vol.All(
        [vol.In(list(_PROBE_FUNCTIONS))], vol.Length(min=1, max=4)
    ),
})
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
        connection.send_error(msg["id"], "invalid_range", "End address must be at or above start address")
        return
    if end_address - start_address > 3:
        connection.send_error(
            msg["id"], "range_too_large",
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
                    request = _probe_request_frame(msg["slave_id"], function_code, address)
                    try:
                        value = await hass.async_add_executor_job(
                            coordinator.read_register_raw,
                            address, register_type, "uint16", msg["slave_id"],
                        )
                        value_hint = (
                            "A binary value; this may be a state bit."
                            if isinstance(value, bool)
                            else "A raw 16-bit register value; meaning is device-specific."
                        )
                        results.append({
                            "register_type": register_type,
                            "function_code": f"0x{function_code:02X}",
                            "function_name": function_name,
                            "address": address,
                            "request": request,
                            "status": "response",
                            "value": value,
                            "meaning": value_hint,
                        })
                    except Exception as err:  # noqa: BLE001
                        results.append({
                            "register_type": register_type,
                            "function_code": f"0x{function_code:02X}",
                            "function_name": function_name,
                            "address": address,
                            "request": request,
                            "status": "no_response",
                            "error": str(err),
                            "meaning": "No valid response. Check slave ID, baud rate, wiring, and register map.",
                        })
                    # Keep the Home Assistant event loop responsive between probes.
                    await asyncio.sleep(0)
                if stopped:
                    break
        finally:
            if _PROBE_STOP_EVENTS.get(msg["entry_id"]) is stop_event:
                _PROBE_STOP_EVENTS.pop(msg["entry_id"], None)
        connection.send_result(msg["id"], {"results": results, "stopped": stopped})
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("Read-only Modbus probe failed: %s", err)
        connection.send_error(msg["id"], "probe_failed", str(err))


@websocket_api.websocket_command({
    vol.Required("type"): "modbus_usb/stop_probe_registers",
    vol.Required("entry_id"): cv.string,
})
@websocket_api.async_response
async def ws_stop_probe_registers(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:  # noqa: ARG001
    """Request that a running read-only board probe stop after its current read."""
    stop_event = _PROBE_STOP_EVENTS.get(msg["entry_id"])
    if stop_event is not None:
        stop_event.set()
    connection.send_result(msg["id"], {"stopping": stop_event is not None})


def _entity_slave_id(entry, entity: dict[str, Any]) -> int:
    """Resolve an entity's explicit or owning device's Modbus slave ID."""
    slave_id = entity.get(CONF_SLAVE_ID)
    if slave_id is None and entity.get(CONF_DEVICE_ID):
        device = next(
            (
                device for device in entry.options.get(CONF_DEVICES, [])
                if str(device.get("id")) == str(entity.get(CONF_DEVICE_ID))
            ),
            None,
        )
        if device:
            slave_id = device.get(CONF_SLAVE_ID)
    return int(slave_id if slave_id is not None else entry.data.get(CONF_SLAVE_ID, 1))


async def _async_write_configured_switch(
    hass: HomeAssistant, entry, entity: dict[str, Any], state: bool
) -> int:
    """Write one configured switch and return its resolved slave ID."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    slave_id = _entity_slave_id(entry, entity)
    addresses = entity.get(CONF_ADDRESSES) or [entity[CONF_ADDRESS]]
    if entity.get(CONF_REGISTER_TYPE) == REGISTER_TYPE_COIL:
        for address in addresses:
            await hass.async_add_executor_job(
                coordinator.write_coil, int(address), state, slave_id
            )
    else:
        value = entity.get(CONF_ON_VALUE, 1) if state else entity.get(CONF_OFF_VALUE, 0)
        for address in addresses:
            await hass.async_add_executor_job(
                coordinator.write_register, int(address), int(value), slave_id
            )
    if (
        is_r413e16_switch_config(entity)
        and entity.get(CONF_DEVICE_ID)
    ):
        coordinator.set_r413e16_channel_states(
            entity[CONF_DEVICE_ID], {int(address): state for address in addresses}
        )
    return slave_id


@websocket_api.websocket_command({
    vol.Required("type"): "modbus_usb/write_entity",
    vol.Required("entry_id"): cv.string,
    vol.Required("entity_id"): cv.string,
    vol.Required("state"): bool,
})
@websocket_api.async_response
async def ws_write_entity(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Write a configured switch without guessing its Home Assistant entity ID."""
    try:
        entry = _get_entry(hass, msg["entry_id"])
        entity = next(
            (
                item for item in entry.options.get(CONF_ENTITIES, [])
                if item.get(CONF_ENTITY_ID) == msg["entity_id"]
            ),
            None,
        )
        if entity is None:
            raise ValueError("Configured entity was not found")
        if entity.get(CONF_ENTITY_TYPE) != "switch":
            raise ValueError("Only switch entities can be written")

        slave_id = await _async_write_configured_switch(
            hass, entry, entity, msg["state"]
        )
        # The sidebar must not leave HA entities at an accepted-command state.
        # Read every configured entity after any panel switch command.
        await hass.data[DOMAIN][entry.entry_id].async_request_refresh()
        connection.send_result(msg["id"], {"success": True, "slave_id": slave_id})
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("Configured switch write failed: %s", err)
        connection.send_error(msg["id"], "write_failed", str(err))


@websocket_api.websocket_command({
    vol.Required("type"): "modbus_usb/check_update",
})
@websocket_api.async_response
async def ws_check_update(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Check GitHub for a newer release without changing the installation."""
    try:
        connection.send_result(msg["id"], await _async_update_status(hass))
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("GitHub update check failed: %s", err)
        connection.send_error(msg["id"], "update_check_failed", str(err))


@websocket_api.websocket_command({
    vol.Required("type"): "modbus_usb/install_update",
})
@websocket_api.async_response
async def ws_install_update(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Start the HACS update when available, otherwise return the release page."""
    try:
        if not connection.user.is_admin:
            connection.send_error(msg["id"], "unauthorized", "Administrator access is required")
            return
        status = await _async_update_status(hass)
        if not status["update_available"]:
            connection.send_result(msg["id"], {"started": False, **status})
            return
        update_entity_id = status.get("update_entity_id")
        if update_entity_id:
            try:
                await hass.services.async_call(
                    "update", "install", {"entity_id": update_entity_id}, blocking=True
                )
                connection.send_result(msg["id"], {"started": True, "method": "hacs", **status})
                return
            except Exception as err:  # noqa: BLE001
                # HACS can finish its refresh between the check and install.
                # Offer the verified release page rather than showing a failed
                # update action to the user.
                _LOGGER.debug("HACS update could not start: %s", err)
        connection.send_result(msg["id"], {"started": False, "method": "release_page", **status})
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("Integration update failed: %s", err)
        connection.send_error(msg["id"], "update_failed", str(err))


def _get_r413e16_device(entry, device_id: str) -> dict[str, Any]:
    """Return a R413E16 device or reject a device-specific command."""
    device = next(
        (item for item in entry.options.get(CONF_DEVICES, []) if item.get("id") == device_id),
        None,
    )
    if device is None:
        raise ValueError("Configured device was not found")
    controls = device.get(CONF_DEVICE_CONTROLS, {})
    model = str(device.get(CONF_MODEL, "")).lower()
    if controls.get("protocol") != "eletechsup_r413e16" and "r413e16" not in model:
        raise ValueError("These controls are available only for an eletechsup R413E16 device")
    return device


@websocket_api.websocket_command({
    vol.Required("type"): "modbus_usb/r413e16_command",
    vol.Required("entry_id"): cv.string,
    vol.Required("device_id"): cv.string,
    vol.Required("command"): vol.In(["all_on", "all_off", "read_states", "configure", "channel_action", "factory_reset"]),
    # The R413E16 command guide assigns codes 0–4 to 1200–19200. Code 5 is
    # factory reset, so higher rates must never be offered for this board.
    vol.Optional("baudrate"): vol.In([1200, 2400, 4800, 9600, 19200]),
    vol.Optional("slave_id"): vol.All(vol.Coerce(int), vol.Range(min=1, max=247)),
    vol.Optional("channel"): vol.All(vol.Coerce(int), vol.Range(min=1, max=16)),
    vol.Optional("action"): vol.In(["toggle", "interlock", "momentary", "delay"]),
    vol.Optional("delay_seconds"): vol.All(vol.Coerce(int), vol.Range(min=0, max=255)),
})
@websocket_api.async_response
async def ws_r413e16_command(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Run verified R413E16 group commands and serial-setup writes."""
    try:
        entry = _get_entry(hass, msg["entry_id"])
        device = _get_r413e16_device(entry, msg["device_id"])
        coordinator = hass.data[DOMAIN][entry.entry_id]
        current_slave = int(device.get(CONF_SLAVE_ID, entry.data.get(CONF_SLAVE_ID, 1)))
        command = msg["command"]

        if command == "read_states":
            # The confirmed R413E16 channel map returns 1 for ON and 0 for OFF.
            # Refresh the configured channel switches from this real board
            # feedback, rather than leaving an older accepted command cached.
            states: list[dict[str, Any]] = []
            for channel in range(1, 17):
                try:
                    value = await hass.async_add_executor_job(
                        coordinator.read_register_raw,
                        channel, "holding", "uint16", current_slave,
                    )
                    states.append({"channel": channel, "value": value, "ok": True})
                except Exception as err:  # noqa: BLE001
                    states.append({"channel": channel, "ok": False, "error": str(err)})
            coordinator.set_r413e16_channel_states(
                device["id"],
                {
                    item["channel"]: int(item["value"]) in (1, 0x0100)
                    for item in states
                    if item["ok"]
                },
            )
            connection.send_result(msg["id"], {
                "success": True, "command": command, "slave_id": current_slave,
                "states": states,
            })
            return

        if command == "factory_reset":
            # The vendor guide assigns baud register value 5 to factory reset.
            # It takes effect after the board is powered up again. The published
            # defaults are 9600 baud and slave ID 1, so keep HA in sync.
            await hass.async_add_executor_job(
                coordinator.write_register, 0x00FE, 5, current_slave
            )
            new_data = {**entry.data, CONF_BAUDRATE: 9600}
            new_options = dict(entry.options or {})
            new_options[CONF_DEVICES] = [
                {**item, CONF_SLAVE_ID: 1} if item.get("id") == device["id"] else item
                for item in new_options.get(CONF_DEVICES, [])
            ]
            new_options[CONF_ENTITIES] = [
                {**item, CONF_SLAVE_ID: 1}
                if item.get(CONF_DEVICE_ID) == device["id"] else item
                for item in new_options.get(CONF_ENTITIES, [])
            ]
            hass.config_entries.async_update_entry(entry, data=new_data, options=new_options)
            connection.send_result(msg["id"], {
                "success": True, "command": command, "baudrate": 9600,
                "slave_id": 1, "power_cycle_required": True,
            })
            return

        if command == "channel_action":
            channel = msg.get("channel")
            action = msg.get("action")
            if channel is None or action is None:
                raise ValueError("Choose a channel and an action")
            controls = device.get(CONF_DEVICE_CONTROLS, {})
            channel_actions = controls.get("channel_actions", {})
            values = {
                "toggle": 0x0300,
                "interlock": 0x0400,
                "momentary": 0x0500,
            }
            if action == "delay":
                delay_seconds = msg.get("delay_seconds")
                if delay_seconds is None:
                    raise ValueError("Enter a delay from 0 to 255 seconds")
                value = int(channel_actions.get("delay_base", 0x0600)) + delay_seconds
            else:
                value = int(channel_actions.get(action, values[action]))
            await hass.async_add_executor_job(
                coordinator.write_register, channel, value, current_slave
            )
            if action == "interlock":
                coordinator.set_r413e16_channel_states(
                    device["id"], {item: item == channel for item in range(1, 17)}
                )
            connection.send_result(msg["id"], {
                "success": True, "command": command, "channel": channel,
                "action": action, "value": value, "slave_id": current_slave,
            })
            return

        if command in ("all_on", "all_off"):
            # Some R413E16 board revisions do not implement the optional
            # register-0 broadcast command consistently. Interlock and normal
            # channels prove the individual FC06 commands, so use those known
            # good writes for a reliable all-channel operation.
            value = 0x0100 if command == "all_on" else 0x0200
            for channel in range(1, 17):
                await hass.async_add_executor_job(
                    coordinator.write_register, channel, value, current_slave
                )
            coordinator.set_r413e16_channel_states(
                device["id"], {channel: command == "all_on" for channel in range(1, 17)}
            )
            connection.send_result(msg["id"], {
                "success": True, "command": command, "slave_id": current_slave,
                "channels_written": 16, "value": value,
            })
            return

        baudrate = msg.get("baudrate")
        new_slave = msg.get("slave_id")
        if baudrate is None and new_slave is None:
            raise ValueError("Choose a baud rate, a slave ID, or both")

        # Each write applies immediately. Change the address first, then send
        # the baud-rate command to the new address while the adapter still
        # uses the old rate.
        target_slave = current_slave
        if new_slave is not None:
            await hass.async_add_executor_job(
                coordinator.write_register, 0x00FF, new_slave, current_slave
            )
            target_slave = new_slave
        if baudrate is not None:
            baud_codes = {
                1200: 0, 2400: 1, 4800: 2, 9600: 3, 19200: 4,
            }
            await hass.async_add_executor_job(
                coordinator.write_register, 0x00FE, baud_codes[baudrate], target_slave
            )

        new_options = dict(entry.options or {})
        devices = [dict(item) for item in new_options.get(CONF_DEVICES, [])]
        if new_slave is not None:
            devices = [
                {**item, CONF_SLAVE_ID: new_slave} if item.get("id") == device["id"] else item
                for item in devices
            ]
            new_options[CONF_ENTITIES] = [
                {**item, CONF_SLAVE_ID: new_slave}
                if item.get(CONF_DEVICE_ID) == device["id"] else item
                for item in new_options.get(CONF_ENTITIES, [])
            ]
        new_options[CONF_DEVICES] = devices
        new_data = dict(entry.data)
        if baudrate is not None:
            new_data[CONF_BAUDRATE] = baudrate
        hass.config_entries.async_update_entry(entry, data=new_data, options=new_options)
        connection.send_result(msg["id"], {
            "success": True, "baudrate": baudrate, "slave_id": new_slave,
            "reload_required": baudrate is not None,
        })
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("R413E16 command failed: %s", err)
        connection.send_error(msg["id"], "r413e16_command_failed", str(err))


@websocket_api.websocket_command({
    vol.Required("type"): "modbus_usb/test_device_entities",
    vol.Required("entry_id"): cv.string,
    vol.Required("device_id"): cv.string,
})
@websocket_api.async_response
async def ws_test_device_entities(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Test all configured entities; safely cycle switches and read the rest."""
    try:
        entry = _get_entry(hass, msg["entry_id"])
        device = next(
            (item for item in entry.options.get(CONF_DEVICES, [])
             if item.get(CONF_ENTITY_ID) == msg["device_id"]),
            None,
        )
        if device is None:
            raise ValueError("Configured device was not found")

        entities = [
            item for item in entry.options.get(CONF_ENTITIES, [])
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
                except Exception as err:  # noqa: BLE001
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
            except Exception as err:  # noqa: BLE001
                result["on"] = {"success": False, "error": str(err)}

            # Keep each output on briefly, then always attempt the safe OFF
            # command even if the ON request failed.
            await asyncio.sleep(0.35)
            try:
                result["slave_id"] = await _async_write_configured_switch(
                    hass, entry, entity, False
                )
                result["off"] = {"success": True, "message": "acknowledged"}
            except Exception as err:  # noqa: BLE001
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
        connection.send_result(msg["id"], {
            "device_name": device.get(CONF_NAME, msg["device_id"]),
            "entity_count": len(results),
            "switch_count": sum(item.get("entity_type") == "switch" for item in results),
            "read_count": sum(item.get("entity_type") != "switch" for item in results),
            "successful_steps": successful_steps,
            "failed_steps": total_steps - successful_steps,
            "duration_ms": round((time.monotonic() - started) * 1000, 1),
            "results": results,
        })
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("Device entity test failed: %s", err)
        connection.send_error(msg["id"], "device_test_failed", str(err))


@websocket_api.websocket_command({
    vol.Required("type"): "modbus_usb/clear_diagnostic_log",
    vol.Required("entry_id"): cv.string,
})
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


@websocket_api.websocket_command({
    vol.Required("type"): "modbus_usb/scan_bus",
    vol.Required("entry_id"): cv.string,
    vol.Required("baudrates"): [vol.All(vol.Coerce(int), vol.Range(min=1200, max=115200))],
    vol.Optional("start_slave", default=1): vol.All(vol.Coerce(int), vol.Range(min=1, max=247)),
    vol.Optional("end_slave", default=20): vol.All(vol.Coerce(int), vol.Range(min=1, max=247)),
})
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
            msg["baudrates"], msg["start_slave"], msg["end_slave"], templates,
        )
        connection.send_result(msg["id"], result)
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("RS-485 bus scan failed: %s", err)
        connection.send_error(msg["id"], "scan_failed", str(err))


@websocket_api.websocket_command({
    vol.Required("type"): "modbus_usb/scan_usb_ports",
})
@websocket_api.async_response
async def ws_scan_usb_ports(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """List USB and other serial adapters available to Home Assistant."""
    try:
        ports = await hass.async_add_executor_job(_list_serial_ports)
        connection.send_result(msg["id"], {"ports": ports})
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("Serial-port scan failed: %s", err)
        connection.send_error(msg["id"], "port_scan_failed", str(err))


@websocket_api.websocket_command({
    vol.Required("type"): "modbus_usb/save_device",
    vol.Required("entry_id"): cv.string,
    vol.Required("device"): dict,
})
@websocket_api.async_response
async def ws_save_device(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Create or update a separated device."""
    try:
        entry = _get_entry(hass, msg["entry_id"])
        device = dict(msg["device"])

        device_id = device.get("id")
        if not device_id:
            device_id = f"dev_{uuid.uuid4().hex[:8]}"
            device["id"] = device_id

        # Validate slave ID
        slave_id = device.get(CONF_SLAVE_ID)
        if slave_id is not None:
            device[CONF_SLAVE_ID] = int(slave_id)

        new_options = dict(entry.options or {})
        devices = list(new_options.get(CONF_DEVICES, []))
        entities = list(new_options.get(CONF_ENTITIES, []))

        existing_idx = next((i for i, d in enumerate(devices) if d.get("id") == device_id), None)
        if existing_idx is not None:
            devices[existing_idx] = device
            # A sidebar device owns the address of its attached entities. The
            # template copies this value into each entity on creation, so sync
            # every save too: it repairs old installations with mixed IDs.
            if device.get(CONF_SLAVE_ID) is not None:
                entities = [
                    {
                        **entity,
                        CONF_SLAVE_ID: device[CONF_SLAVE_ID],
                    }
                    if entity.get(CONF_DEVICE_ID) == device_id
                    else entity
                    for entity in entities
                ]
        else:
            devices.append(device)

        new_options[CONF_DEVICES] = devices
        new_options[CONF_ENTITIES] = entities
        hass.config_entries.async_update_entry(entry, options=new_options)

        connection.send_result(msg["id"], {"success": True, "device": device})
    except Exception as err:
        _LOGGER.error("ws_save_device failed: %s", err, exc_info=True)
        connection.send_error(msg["id"], "error", str(err))


@websocket_api.websocket_command({
    vol.Required("type"): "modbus_usb/delete_device",
    vol.Required("entry_id"): cv.string,
    vol.Required("device_id"): cv.string,
    vol.Optional("delete_entities", default=True): cv.boolean,
})
@websocket_api.async_response
async def ws_delete_device(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Delete a separated device and optionally its assigned entities."""
    try:
        entry = _get_entry(hass, msg["entry_id"])
        device_id = msg["device_id"]
        delete_entities = msg.get("delete_entities", True)

        new_options = dict(entry.options or {})
        devices = [d for d in new_options.get(CONF_DEVICES, []) if d.get("id") != device_id]
        new_options[CONF_DEVICES] = devices

        if delete_entities:
            entities = [e for e in new_options.get(CONF_ENTITIES, []) if e.get(CONF_DEVICE_ID) != device_id]
            new_options[CONF_ENTITIES] = entities

        # Removing configuration alone leaves an orphaned child in Home
        # Assistant's device registry. Remove its entities first, then remove
        # the child device identified by this hub and sidebar device ID.
        if delete_entities:
            from homeassistant.helpers import device_registry as dr
            from homeassistant.helpers import entity_registry as er

            device_registry = dr.async_get(hass)
            registry_device = device_registry.async_get_device(
                identifiers={(DOMAIN, f"{entry.entry_id}_{device_id}")}
            )
            if registry_device:
                entity_registry = er.async_get(hass)
                for registry_entity in er.async_entries_for_config_entry(
                    entity_registry, entry.entry_id
                ):
                    if registry_entity.device_id == registry_device.id:
                        entity_registry.async_remove(registry_entity.entity_id)
                device_registry.async_remove_device(registry_device.id)

        hass.config_entries.async_update_entry(entry, options=new_options)
        connection.send_result(msg["id"], {"success": True})
    except Exception as err:
        _LOGGER.error("ws_delete_device failed: %s", err, exc_info=True)
        connection.send_error(msg["id"], "error", str(err))


@websocket_api.websocket_command({
    vol.Required("type"): "modbus_usb/save_entity",
    vol.Required("entry_id"): cv.string,
    vol.Required("entity"): dict,
})
@websocket_api.async_response
async def ws_save_entity(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Create or update an entity (sensor, switch, binary_sensor, number)."""
    try:
        entry = _get_entry(hass, msg["entry_id"])
        entity = dict(msg["entity"])

        ent_id = entity.get(CONF_ENTITY_ID)
        if not ent_id:
            ent_id = uuid.uuid4().hex[:8]
            entity[CONF_ENTITY_ID] = ent_id

        # Coerce numeric fields
        if CONF_ADDRESS in entity:
            entity[CONF_ADDRESS] = int(entity[CONF_ADDRESS])
        if CONF_ADDRESSES in entity and entity[CONF_ADDRESSES] not in (None, ""):
            if not isinstance(entity[CONF_ADDRESSES], list):
                raise ValueError("Group switch addresses must be a list")
            addresses = [int(address) for address in entity[CONF_ADDRESSES]]
            if any(address < 0 or address > 65535 for address in addresses):
                raise ValueError("Group switch addresses must be between 0 and 65535")
            entity[CONF_ADDRESSES] = list(dict.fromkeys(addresses))
        if CONF_SCALE in entity and entity[CONF_SCALE] not in (None, ""):
            entity[CONF_SCALE] = float(entity[CONF_SCALE])
        if CONF_SLAVE_ID in entity and entity[CONF_SLAVE_ID] not in (None, ""):
            entity[CONF_SLAVE_ID] = int(entity[CONF_SLAVE_ID])
        if CONF_MIN_VALUE in entity and entity[CONF_MIN_VALUE] not in (None, ""):
            entity[CONF_MIN_VALUE] = float(entity[CONF_MIN_VALUE])
        if CONF_MAX_VALUE in entity and entity[CONF_MAX_VALUE] not in (None, ""):
            entity[CONF_MAX_VALUE] = float(entity[CONF_MAX_VALUE])
        if CONF_STEP in entity and entity[CONF_STEP] not in (None, ""):
            entity[CONF_STEP] = float(entity[CONF_STEP])
        # JSON form values are strings.  Keep Modbus command and state values
        # numeric so a switch edited in the sidebar retains its template
        # behaviour (including the R413E16 verified-state mapping).
        for value_key in (CONF_ON_VALUE, CONF_OFF_VALUE, CONF_STATE_ON_VALUE):
            if value_key in entity and entity[value_key] not in (None, ""):
                entity[value_key] = int(entity[value_key])

        new_options = dict(entry.options or {})
        entities = list(new_options.get(CONF_ENTITIES, []))

        existing_idx = next((i for i, e in enumerate(entities) if e.get(CONF_ENTITY_ID) == ent_id), None)
        if existing_idx is not None:
            # The editor exposes only the fields it can safely change. Keep
            # template-only settings such as assumed_state and a device image
            # when an existing entity is edited from the sidebar.
            entities[existing_idx] = {**entities[existing_idx], **entity}
        else:
            entities.append(entity)

        new_options[CONF_ENTITIES] = entities
        hass.config_entries.async_update_entry(entry, options=new_options)

        connection.send_result(msg["id"], {"success": True, "entity": entity})
    except Exception as err:
        _LOGGER.error("ws_save_entity failed: %s", err, exc_info=True)
        connection.send_error(msg["id"], "error", str(err))


@websocket_api.websocket_command({
    vol.Required("type"): "modbus_usb/delete_entity",
    vol.Required("entry_id"): cv.string,
    vol.Required("entity_id"): cv.string,
})
@websocket_api.async_response
async def ws_delete_entity(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Delete an entity."""
    try:
        entry = _get_entry(hass, msg["entry_id"])
        ent_id = msg["entity_id"]

        new_options = dict(entry.options or {})
        existing_entity = next(
            (
                entity for entity in new_options.get(CONF_ENTITIES, [])
                if entity.get(CONF_ENTITY_ID) == ent_id
            ),
            None,
        )
        entities = [e for e in new_options.get(CONF_ENTITIES, []) if e.get(CONF_ENTITY_ID) != ent_id]
        new_options[CONF_ENTITIES] = entities

        # Remove the registry entry too. Otherwise deleting and re-adding a
        # template switch leaves an old, similarly named HA entity behind,
        # which can make a dashboard appear not to synchronize.
        entity_registry = er.async_get(hass)
        entity_domain = (existing_entity or {}).get(CONF_ENTITY_TYPE)
        ha_entity_id = (
            entity_registry.async_get_entity_id(
                entity_domain, DOMAIN, f"{entry.entry_id}_{ent_id}"
            )
            if entity_domain in {"sensor", "switch", "binary_sensor", "number"}
            else None
        )
        if ha_entity_id:
            entity_registry.async_remove(ha_entity_id)

        hass.config_entries.async_update_entry(entry, options=new_options)
        connection.send_result(msg["id"], {"success": True})
    except Exception as err:
        _LOGGER.error("ws_delete_entity failed: %s", err, exc_info=True)
        connection.send_error(msg["id"], "error", str(err))


@websocket_api.websocket_command({
    vol.Required("type"): "modbus_usb/save_hub",
    vol.Required("entry_id"): cv.string,
    vol.Required("hub"): dict,
})
@websocket_api.async_response
async def ws_save_hub(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Update hub connection parameters directly from the sidebar UI."""
    try:
        entry = _get_entry(hass, msg["entry_id"])
        hub_data = msg["hub"]

        new_data = dict(entry.data)
        if CONF_PORT in hub_data:
            new_data[CONF_PORT] = hub_data[CONF_PORT]
        if CONF_BAUDRATE in hub_data:
            new_data[CONF_BAUDRATE] = int(hub_data[CONF_BAUDRATE])
        if CONF_BYTESIZE in hub_data:
            new_data[CONF_BYTESIZE] = int(hub_data[CONF_BYTESIZE])
        if CONF_PARITY in hub_data:
            new_data[CONF_PARITY] = hub_data[CONF_PARITY]
        if CONF_STOPBITS in hub_data:
            new_data[CONF_STOPBITS] = int(hub_data[CONF_STOPBITS])
        if CONF_SLAVE_ID in hub_data:
            new_data[CONF_SLAVE_ID] = int(hub_data[CONF_SLAVE_ID])

        new_options = dict(entry.options or {})
        if CONF_SCAN_INTERVAL in hub_data:
            new_options[CONF_SCAN_INTERVAL] = int(hub_data[CONF_SCAN_INTERVAL])

        hass.config_entries.async_update_entry(entry, data=new_data, options=new_options)
        connection.send_result(msg["id"], {"success": True})
    except Exception as err:
        _LOGGER.error("ws_save_hub failed: %s", err, exc_info=True)
        connection.send_error(msg["id"], "error", str(err))


@websocket_api.websocket_command({
    vol.Required("type"): "modbus_usb/get_templates",
})
@websocket_api.async_response
async def ws_get_templates(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Return all device templates loaded from individual YAML files."""
    try:
        templates = await async_load_templates(hass)
        connection.send_result(msg["id"], {"templates": templates})
    except Exception as err:
        _LOGGER.error("ws_get_templates failed: %s", err, exc_info=True)
        connection.send_error(msg["id"], "error", str(err))


@websocket_api.websocket_command({
    vol.Required("type"): "modbus_usb/save_template",
    vol.Required("filename"): cv.string,
    vol.Required("content"): cv.string,
})
@websocket_api.async_response
async def ws_save_template(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Save or create a device template YAML file."""
    try:
        saved_tpl = await async_save_template(hass, msg["filename"], msg["content"])
        connection.send_result(msg["id"], {"success": True, "template": saved_tpl})
    except Exception as err:
        _LOGGER.error("ws_save_template failed: %s", err, exc_info=True)
        connection.send_error(msg["id"], "error", str(err))


@websocket_api.websocket_command({
    vol.Required("type"): "modbus_usb/delete_template",
    vol.Required("filename"): cv.string,
})
@websocket_api.async_response
async def ws_delete_template(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Delete a device template YAML file."""
    try:
        deleted = await async_delete_template(hass, msg["filename"])
        connection.send_result(msg["id"], {"success": deleted})
    except Exception as err:
        _LOGGER.error("ws_delete_template failed: %s", err, exc_info=True)
        connection.send_error(msg["id"], "error", str(err))


@websocket_api.websocket_command({
    vol.Required("type"): "modbus_usb/apply_template",
    vol.Required("entry_id"): cv.string,
    vol.Optional("device_id"): cv.string,
    vol.Optional("device_name"): cv.string,
    vol.Optional("slave_id"): vol.All(vol.Coerce(int), vol.Range(min=1, max=247)),
    vol.Optional("template_filename"): cv.string,
    vol.Optional("template_id"): cv.string,
    vol.Optional("selected_entities"): list,
    vol.Optional("address_offset", default=0): vol.Coerce(int),
})
@websocket_api.async_response
async def ws_apply_template(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Apply template to an existing or new device, adding selected sensors/switches."""
    try:
        entry = _get_entry(hass, msg["entry_id"])
        templates = await async_load_templates(hass)

        # Match template by filename or id
        target_tpl = None
        tfname = msg.get("template_filename")
        tid = msg.get("template_id")
        for t in templates:
            if (tfname and t.get("filename") == tfname) or (tid and t.get("id") == tid):
                target_tpl = t
                break

        if not target_tpl:
            raise ValueError(f"Template '{tfname or tid}' not found")

        new_options = dict(entry.options or {})
        devices = list(new_options.get(CONF_DEVICES, []))
        entities = list(new_options.get(CONF_ENTITIES, []))

        device_id = msg.get("device_id")
        slave_id = msg.get("slave_id") or target_tpl.get("default_slave_id", 1)

        # If no device_id provided, create a new device
        if not device_id:
            device_id = f"dev_{uuid.uuid4().hex[:8]}"
            dev_name = msg.get("device_name") or target_tpl.get("name") or "New Device"
            new_device = {
                "id": device_id,
                "name": dev_name,
                "slave_id": slave_id,
                "model": target_tpl.get("model", "Modbus Device"),
                "manufacturer": target_tpl.get("manufacturer", "Generic"),
                "description": target_tpl.get("description", ""),
                "image": target_tpl.get(CONF_IMAGE) or "",
                "info_url": target_tpl.get(CONF_INFO_URL) or "",
                CONF_DEVICE_CONTROLS: target_tpl.get(CONF_DEVICE_CONTROLS, {}),
            }
            devices.append(new_device)
        else:
            # Look up existing device to inherit slave_id if not given
            existing_dev = next((d for d in devices if d.get("id") == device_id), None)
            if existing_dev and "slave_id" in existing_dev:
                slave_id = existing_dev["slave_id"]
            if existing_dev and target_tpl.get(CONF_IMAGE) and not existing_dev.get(CONF_IMAGE):
                existing_dev = dict(existing_dev)
                existing_dev[CONF_IMAGE] = target_tpl[CONF_IMAGE]
                devices = [
                    existing_dev if d.get("id") == device_id else d for d in devices
                ]
            if existing_dev and target_tpl.get(CONF_INFO_URL) and not existing_dev.get(CONF_INFO_URL):
                existing_dev = dict(existing_dev)
                existing_dev[CONF_INFO_URL] = target_tpl[CONF_INFO_URL]
                devices = [
                    existing_dev if d.get("id") == device_id else d for d in devices
                ]
            if existing_dev and target_tpl.get(CONF_DEVICE_CONTROLS) and not existing_dev.get(CONF_DEVICE_CONTROLS):
                existing_dev = dict(existing_dev)
                existing_dev[CONF_DEVICE_CONTROLS] = target_tpl[CONF_DEVICE_CONTROLS]
                devices = [
                    existing_dev if d.get("id") == device_id else d for d in devices
                ]

        # Filter entities from template
        tpl_entities = target_tpl.get("entities", [])
        selected_names = msg.get("selected_entities")
        if selected_names is not None:
            # Filter by matching name or index
            selected_set = set(selected_names)
            chosen_entities = [
                e for i, e in enumerate(tpl_entities)
                if e.get("name") in selected_set or str(i) in selected_set
            ]
        else:
            chosen_entities = tpl_entities

        address_offset = int(msg.get("address_offset", 0))
        added_entities = []

        for tent in chosen_entities:
            ent = dict(tent)
            ent[CONF_ENTITY_ID] = uuid.uuid4().hex[:8]
            ent[CONF_DEVICE_ID] = device_id
            ent[CONF_SLAVE_ID] = slave_id
            if CONF_ADDRESS in ent:
                ent[CONF_ADDRESS] = int(ent[CONF_ADDRESS]) + address_offset
            if CONF_ADDRESSES in ent:
                ent[CONF_ADDRESSES] = [
                    int(address) + address_offset
                    for address in ent[CONF_ADDRESSES]
                ]
            entities.append(ent)
            added_entities.append(ent)

        new_options[CONF_DEVICES] = devices
        new_options[CONF_ENTITIES] = entities

        hass.config_entries.async_update_entry(entry, options=new_options)

        connection.send_result(
            msg["id"],
            {
                "success": True,
                "device_id": device_id,
                "added_count": len(added_entities),
            },
        )
    except Exception as err:
        _LOGGER.error("ws_apply_template failed: %s", err, exc_info=True)
        connection.send_error(msg["id"], "error", str(err))


# ─────────────────────────────────────────────────────────────────────────────
# REST API Fallback Views
# ─────────────────────────────────────────────────────────────────────────────

class ModbusUsbConfigView(HomeAssistantView):
    """REST API view for Modbus USB configuration."""

    url = "/api/modbus_usb/config"
    name = "api:modbus_usb:config"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        """GET /api/modbus_usb/config -> list all entries, devices, entities."""
        hass: HomeAssistant = request.app["hass"]
        entries = hass.config_entries.async_entries(DOMAIN)
        entries_data = [_format_entry_data(hass, e) for e in entries]
        templates = await async_load_templates(hass)
        return self.json({"entries": entries_data, "templates": templates})


class ModbusUsbTemplatesView(HomeAssistantView):
    """REST API view for template management."""

    url = "/api/modbus_usb/templates"
    name = "api:modbus_usb:templates"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        """GET /api/modbus_usb/templates -> list all templates."""
        hass: HomeAssistant = request.app["hass"]
        templates = await async_load_templates(hass)
        return self.json({"templates": templates})

    async def post(self, request: web.Request) -> web.Response:
        """POST /api/modbus_usb/templates -> save a template."""
        hass: HomeAssistant = request.app["hass"]
        data = await request.json()
        filename = data.get("filename")
        content = data.get("content")
        if not filename or not content:
            return self.json({"error": "filename and content are required"}, status_code=400)
        try:
            tpl = await async_save_template(hass, filename, content)
            return self.json({"success": True, "template": tpl})
        except Exception as err:
            return self.json({"error": str(err)}, status_code=400)

    async def delete(self, request: web.Request) -> web.Response:
        """DELETE /api/modbus_usb/templates?filename=xxx -> delete a template."""
        hass: HomeAssistant = request.app["hass"]
        filename = request.query.get("filename")
        if not filename:
            return self.json({"error": "filename query parameter required"}, status_code=400)
        deleted = await async_delete_template(hass, filename)
        return self.json({"success": deleted})


# ─────────────────────────────────────────────────────────────────────────────
# Registration
# ─────────────────────────────────────────────────────────────────────────────

_API_REGISTERED = False


async def async_register_api(hass: HomeAssistant) -> None:
    """Register WebSocket commands and REST views."""
    global _API_REGISTERED  # noqa: PLW0603
    if _API_REGISTERED:
        return
    _API_REGISTERED = True

    # Register WebSocket handlers
    websocket_api.async_register_command(hass, ws_get_data)
    websocket_api.async_register_command(hass, ws_diagnostic_read)
    websocket_api.async_register_command(hass, ws_diagnostic_write)
    websocket_api.async_register_command(hass, ws_probe_registers)
    websocket_api.async_register_command(hass, ws_stop_probe_registers)
    websocket_api.async_register_command(hass, ws_write_entity)
    websocket_api.async_register_command(hass, ws_check_update)
    websocket_api.async_register_command(hass, ws_install_update)
    websocket_api.async_register_command(hass, ws_r413e16_command)
    websocket_api.async_register_command(hass, ws_test_device_entities)
    websocket_api.async_register_command(hass, ws_clear_diagnostic_log)
    websocket_api.async_register_command(hass, ws_scan_bus)
    websocket_api.async_register_command(hass, ws_scan_usb_ports)
    websocket_api.async_register_command(hass, ws_save_device)
    websocket_api.async_register_command(hass, ws_delete_device)
    websocket_api.async_register_command(hass, ws_save_entity)
    websocket_api.async_register_command(hass, ws_delete_entity)
    websocket_api.async_register_command(hass, ws_save_hub)
    websocket_api.async_register_command(hass, ws_get_templates)
    websocket_api.async_register_command(hass, ws_save_template)
    websocket_api.async_register_command(hass, ws_delete_template)
    websocket_api.async_register_command(hass, ws_apply_template)

    # Register HTTP views
    try:
        hass.http.register_view(ModbusUsbConfigView)
        hass.http.register_view(ModbusUsbTemplatesView)
    except Exception as err:
        _LOGGER.warning("Could not register HTTP views (may already be registered): %s", err)

    _LOGGER.debug("Modbus USB WebSocket & REST API registered")


# Changelog:
# 2026-09-06 — Copy template image onto devices when applying eletechsup and other photo templates.
# Date modified: 2026-09-06
