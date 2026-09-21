"""Shared private helpers for the Modbus USB WebSocket API."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from ..boards.r413e16 import is_r413e16_switch_config
from ..const import (
    CONF_ADDRESS,
    CONF_ADDRESSES,
    CONF_BAUDRATE,
    CONF_BYTESIZE,
    CONF_DEVICE_CONTROLS,
    CONF_DEVICE_ID,
    CONF_DEVICES,
    CONF_ENTITIES,
    CONF_ENTITY_ID,
    CONF_ENTITY_TYPE,
    CONF_MODEL,
    CONF_OFF_VALUE,
    CONF_ON_VALUE,
    CONF_PARITY,
    CONF_PORT,
    CONF_REGISTER_TYPE,
    CONF_SCAN_INTERVAL,
    CONF_SLAVE_ID,
    CONF_STOPBITS,
    DOMAIN,
    REGISTER_TYPE_COIL,
)
from ..transport import connection_config, describe_transport

_LOGGER = logging.getLogger(__name__)


def _resolve_persistent_port_path(port: str) -> str:
    """Detect when /dev/ttyUSB* dynamically switches index or lookup persistent by-id path."""
    import os

    if not port or not isinstance(port, str):
        return port
    if port.startswith("/dev/serial/by-id/") and os.path.exists(port):
        return port
    by_id_dir = "/dev/serial/by-id"
    if os.path.isdir(by_id_dir):
        if os.path.exists(port):
            real_target = os.path.realpath(port)
            for name in sorted(os.listdir(by_id_dir)):
                link_path = os.path.join(by_id_dir, name)
                try:
                    if os.path.realpath(link_path) == real_target:
                        return link_path
                except OSError:
                    continue
            return port
        for name in sorted(os.listdir(by_id_dir)):
            link_path = os.path.join(by_id_dir, name)
            try:
                real_target = os.path.realpath(link_path)
                if os.path.exists(real_target) and (
                    "ttyUSB" in real_target or "ttyACM" in real_target
                ):
                    return link_path
            except OSError:
                continue
    return port


def _list_serial_ports() -> list[dict[str, Any]]:
    """Return serial ports and detailed adapter metadata visible to Home Assistant."""

    try:
        from serial.tools import list_ports
    except ImportError:
        _LOGGER.warning("USB port scanning is unavailable because pyserial is missing")
        return []

    ports: list[dict[str, Any]] = []
    for port in list_ports.comports():
        details_parts = [
            value
            for value in (port.manufacturer, port.product, port.hwid)
            if value and value != "n/a"
        ]
        persistent_path = _resolve_persistent_port_path(port.device)
        vid_hex = f"0x{port.vid:04X}" if port.vid is not None else None
        pid_hex = f"0x{port.pid:04X}" if port.pid is not None else None
        chipset = port.product or port.description or "Standard Serial Adapter"
        if port.manufacturer and port.manufacturer != "n/a":
            chipset = f"{port.manufacturer} ({chipset})"

        ports.append(
            {
                "port": port.device,
                "persistent_path": persistent_path,
                "description": port.description or "Serial device",
                "details": " · ".join(details_parts),
                "manufacturer": port.manufacturer
                if port.manufacturer != "n/a"
                else None,
                "product": port.product if port.product != "n/a" else None,
                "vid": vid_hex,
                "pid": pid_hex,
                "hwid": port.hwid if port.hwid != "n/a" else None,
                "serial_number": port.serial_number
                if port.serial_number != "n/a"
                else None,
                "chipset": chipset,
            }
        )
    return sorted(ports, key=lambda item: item["port"])


def _get_entry(hass: HomeAssistant, entry_id: str):
    """Retrieve config entry by ID or raise an error."""
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None:
        raise ValueError(f"Config entry '{entry_id}' not found")
    return entry


def _hub_transport_fields(data: dict[str, Any]) -> dict[str, Any]:
    """Transport-related hub fields for the panel, never including secrets."""
    summary = describe_transport(connection_config(data))
    fields = {
        "transport": summary["transport"],
        "transport_label": summary["label"],
        "endpoint": summary["endpoint"],
        "baudrate_fixed": summary["baudrate_fixed"],
        "response_timeout": summary["response_timeout"],
    }
    for key in (
        "host",
        "tcp_port",
        "api_port",
        "esphome_service",
        "esphome_event",
        "encrypted",
        "password_set",
    ):
        if key in summary:
            fields[key] = summary[key]
    return fields


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
    diagnostics = (
        coordinator.get_diagnostics()
        if coordinator
        else {
            "connected": False,
            "entry_id": entry.entry_id,
            "health": {},
            "transactions": [],
        }
    )
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
        diagnostics["devices"].append(
            {
                "device_id": device.get("id"),
                "slave_id": slave_id,
                "status": latest.get("status") if latest else "unknown",
                "last_operation": latest.get("operation") if latest else None,
                "last_seen": latest.get("timestamp") if latest else None,
                "last_error": latest.get("error")
                if latest and latest.get("status") == "error"
                else None,
            }
        )
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
            # v2.8.0: transport fields; credentials are reduced to presence
            # flags (``encrypted`` / ``password_set``) by describe_transport.
            **_hub_transport_fields(entry.data),
        },
        "devices": devices,
        "entities": entities,
        "diagnostics": diagnostics,
    }


def _probe_request_frame(slave_id: int, function_code: int, address: int) -> str:
    """Return the visible portion of a one-item Modbus RTU read request.

    The CRC is deliberately represented rather than calculated here: pymodbus
    owns frame generation and may use a different transport implementation.
    """
    return (
        f"{slave_id:02X} {function_code:02X} {address >> 8:02X} "
        f"{address & 0xFF:02X} 00 01 [CRC]"
    )


def _entity_slave_id(entry, entity: dict[str, Any]) -> int:
    """Resolve an entity's explicit or owning device's Modbus slave ID."""
    slave_id = entity.get(CONF_SLAVE_ID)
    if slave_id is None and entity.get(CONF_DEVICE_ID):
        device = next(
            (
                device
                for device in entry.options.get(CONF_DEVICES, [])
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
        try:
            value = int(
                entity.get(CONF_ON_VALUE, 1) if state else entity.get(CONF_OFF_VALUE, 0)
            )
        except (TypeError, ValueError):
            value = 1 if state else 0
        for address in addresses:
            await hass.async_add_executor_job(
                coordinator.write_register, int(address), value, slave_id
            )
    if is_r413e16_switch_config(entity) and entity.get(CONF_DEVICE_ID):
        coordinator.set_r413e16_channel_states(
            entity[CONF_DEVICE_ID], {int(address): state for address in addresses}
        )
    return slave_id


def _get_r413e16_device(entry, device_id: str) -> dict[str, Any]:
    """Return a R413E16 device or reject a device-specific command."""
    device = next(
        (
            item
            for item in entry.options.get(CONF_DEVICES, [])
            if item.get("id") == device_id
        ),
        None,
    )
    if device is None:
        raise ValueError("Configured device was not found")
    controls = device.get(CONF_DEVICE_CONTROLS, {})
    model = str(device.get(CONF_MODEL, "")).lower()
    if controls.get("protocol") != "eletechsup_r413e16" and "r413e16" not in model:
        raise ValueError(
            "These controls are available only for an eletechsup R413E16 device"
        )
    return device


def _get_r4d6f20_device(entry, device_id: str) -> dict[str, Any]:
    """Return an R4D6F20 device or reject a board-specific operation."""
    device = next(
        (
            item
            for item in entry.options.get(CONF_DEVICES, [])
            if item.get("id") == device_id
        ),
        None,
    )
    if device is None:
        raise ValueError("Configured device was not found")
    controls = device.get(CONF_DEVICE_CONTROLS, {})
    if (
        controls.get("protocol") != "eletechsup_r4d6f20"
        and "r4d6f20" not in str(device.get(CONF_MODEL, "")).lower()
    ):
        raise ValueError(
            "These controls are available only for an eletechsup R4D6F20 device"
        )
    return device
