"""WebSocket commands for hub serial/polling configuration."""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv

from ..const import (
    CONF_BAUDRATE,
    CONF_BYTESIZE,
    CONF_PARITY,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_SLAVE_ID,
    CONF_STOPBITS,
    DOMAIN,
)
from ..templates import (
    async_load_templates,
)
from .helpers import _format_entry_data, _get_entry, _list_serial_ports

_LOGGER = logging.getLogger(__name__)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "modbus_usb/get_data",
        vol.Optional("entry_id"): cv.string,
    }
)
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


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "modbus_usb/scan_usb_ports",
    }
)
@websocket_api.async_response
async def ws_scan_usb_ports(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """List USB and other serial adapters available to Home Assistant."""
    try:
        ports = await hass.async_add_executor_job(_list_serial_ports)
        connection.send_result(msg["id"], {"ports": ports})
    except Exception as err:
        _LOGGER.warning("Serial-port scan failed: %s", err)
        connection.send_error(msg["id"], "port_scan_failed", str(err))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "modbus_usb/get_serial_status",
        vol.Required("entry_id"): cv.string,
    }
)
@websocket_api.async_response
async def ws_get_serial_status(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Return the configured port profile and matching USB-adapter metadata.

    Listing ports is non-invasive: it never opens the configured adapter, so
    this can safely run while the integration owns an active RS-485 bus.
    """
    try:
        coordinator = hass.data[DOMAIN][msg["entry_id"]]
        serial = coordinator.get_diagnostics().get("serial", {})
        configured_port = str(serial.get("port") or "")
        ports = await hass.async_add_executor_job(_list_serial_ports)
        # Match either exact port or persistent symlink path
        adapter = next(
            (
                item
                for item in ports
                if item.get("port") == configured_port
                or item.get("persistent_path") == configured_port
                or (
                    configured_port
                    and item.get("persistent_path")
                    and configured_port in str(item.get("persistent_path"))
                )
            ),
            None,
        )
        connection.send_result(msg["id"], {"serial": serial, "adapter": adapter})
    except Exception as err:
        _LOGGER.warning("Serial status lookup failed: %s", err)
        connection.send_error(msg["id"], "serial_status_failed", str(err))


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "modbus_usb/save_hub",
        vol.Required("entry_id"): cv.string,
        vol.Required("hub"): dict,
    }
)
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

        hass.config_entries.async_update_entry(
            entry, data=new_data, options=new_options
        )
        connection.send_result(msg["id"], {"success": True})
    except Exception as err:
        _LOGGER.error("ws_save_hub failed: %s", err, exc_info=True)
        connection.send_error(msg["id"], "error", str(err))
