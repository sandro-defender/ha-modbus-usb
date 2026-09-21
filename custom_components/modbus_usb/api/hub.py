"""WebSocket commands for hub serial/polling configuration."""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv

from ..const import (
    CONF_API_ENCRYPTION_KEY,
    CONF_API_PASSWORD,
    CONF_API_PORT,
    CONF_BAUDRATE,
    CONF_BYTESIZE,
    CONF_ESPHOME_EVENT,
    CONF_ESPHOME_SERVICE,
    CONF_HOST,
    CONF_PARITY,
    CONF_PORT,
    CONF_RESPONSE_TIMEOUT,
    CONF_SCAN_INTERVAL,
    CONF_SLAVE_ID,
    CONF_STOPBITS,
    CONF_TCP_PORT,
    CONF_TRANSPORT,
    DOMAIN,
    TRANSPORTS,
)
from ..serial_watch import (
    probe_port_ownership,
    stable_by_id_path,
)
from ..templates import (
    async_load_templates,
)
from ..transport import (
    connection_config,
    describe_transport,
    is_serial,
    probe_connection,
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
        if not is_serial(getattr(coordinator, "serial_config", None)):
            # ESPHome bridges: no local adapter to match; the transport
            # summary (device info, bridge stats) is already in ``serial``.
            connection.send_result(
                msg["id"], {"serial": serial, "adapter": None, "esphome": True}
            )
            return
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
        # v2.9.0: port-ownership diagnostics. When the integration already
        # owns the port we do not probe it (opening it would fail with
        # EBUSY while Home Assistant is mid-transaction).
        connected = bool(getattr(coordinator.client, "connected", False))
        if connected:
            ownership = {
                "reason": "ok",
                "hint": "Port is open — Home Assistant is currently using it.",
                "holders": [],
                "probed": False,
            }
        else:
            ownership = await hass.async_add_executor_job(
                probe_port_ownership, configured_port
            )
            ownership["probed"] = True
        by_id_candidates = sorted(
            {
                str(item["persistent_path"])
                for item in ports
                if item.get("persistent_path")
                and str(item["persistent_path"]).startswith("/dev/serial/by-id/")
            }
        )
        stable_path = await hass.async_add_executor_job(
            stable_by_id_path, configured_port
        )
        connection.send_result(
            msg["id"],
            {
                "serial": serial,
                "adapter": adapter,
                "ownership": ownership,
                "by_id_candidates": by_id_candidates,
                "stable_path": stable_path,
            },
        )
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
        if CONF_TRANSPORT in hub_data:
            transport = str(hub_data[CONF_TRANSPORT])
            if transport not in TRANSPORTS:
                raise ValueError(f"Unknown hub transport '{transport}'")
            new_data[CONF_TRANSPORT] = transport
        if CONF_HOST in hub_data:
            host = str(hub_data[CONF_HOST]).strip()
            if not host and new_data.get(CONF_TRANSPORT, "serial") != "serial":
                raise ValueError("Host is required for ESPHome transports")
            new_data[CONF_HOST] = host
        if CONF_TCP_PORT in hub_data:
            new_data[CONF_TCP_PORT] = _port_number(hub_data[CONF_TCP_PORT])
        if CONF_API_PORT in hub_data:
            new_data[CONF_API_PORT] = _port_number(hub_data[CONF_API_PORT])
        if CONF_RESPONSE_TIMEOUT in hub_data:
            new_data[CONF_RESPONSE_TIMEOUT] = max(
                0.1, min(float(hub_data[CONF_RESPONSE_TIMEOUT]), 30.0)
            )
        for text_key in (CONF_ESPHOME_SERVICE, CONF_ESPHOME_EVENT):
            if text_key in hub_data and str(hub_data[text_key]).strip():
                new_data[text_key] = str(hub_data[text_key]).strip()
        # Secrets: a blank value means "keep the stored one"; the panel never
        # receives the current value, so it can only replace or clear it.
        for secret in (CONF_API_ENCRYPTION_KEY, CONF_API_PASSWORD):
            if secret in hub_data:
                value = str(hub_data[secret] or "").strip()
                if value:
                    new_data[secret] = value
                elif hub_data.get(f"clear_{secret}"):
                    new_data.pop(secret, None)
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


def _port_number(value) -> int:
    port = int(value)
    if not 1 <= port <= 65535:
        raise ValueError(f"Port must be 1–65535, got {port}")
    return port


@websocket_api.websocket_command(
    {
        vol.Required("type"): "modbus_usb/test_hub_connection",
        vol.Required("entry_id"): cv.string,
        vol.Optional("hub"): dict,
    }
)
@websocket_api.async_response
async def ws_test_hub_connection(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Read-only reachability probe of the hub endpoint (v2.8.0).

    Probes the stored connection, or — when ``hub`` overrides are given —
    the settings the user is about to save, without touching the entry.
    For serial and ESPHome-TCP hubs the probe never sends Modbus traffic;
    for the ESPHome API it performs the handshake and returns device info.
    Secrets in ``hub`` are used for the probe only and never echoed back.
    """
    try:
        entry = _get_entry(hass, msg["entry_id"])
        overrides = dict(msg.get("hub") or {})
        data = {**entry.data}
        for key, value in overrides.items():
            if key in (CONF_API_ENCRYPTION_KEY, CONF_API_PASSWORD) and not value:
                continue  # blank secret → keep the stored one
            data[key] = value
        config = connection_config(data)
        coordinator = hass.data.get(DOMAIN, {}).get(msg["entry_id"])
        # The live client owns a serial adapter / the single TCP stream slot:
        # probing the same endpoint while it is open would fail spuriously.
        same_endpoint = (
            coordinator is not None
            and connection_config(getattr(coordinator, "serial_config", {})) == config
        )
        if same_endpoint and getattr(coordinator.client, "connected", False):
            result = {
                "reachable": True,
                "latency_ms": None,
                "error": None,
                "error_key": None,
                "esphome": dict(getattr(coordinator.client, "device_info", {}) or {})
                or None,
                "live": True,
            }
        else:
            result = await hass.async_add_executor_job(probe_connection, config, hass)
            result["live"] = False
        result["summary"] = describe_transport(config)
        connection.send_result(msg["id"], result)
    except Exception as err:
        _LOGGER.warning("Hub connection test failed: %s", err)
        connection.send_error(msg["id"], "test_failed", str(err))
