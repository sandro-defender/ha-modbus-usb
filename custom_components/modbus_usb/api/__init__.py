"""WebSocket and REST API for the Modbus USB Controller integration."""

from __future__ import annotations

import logging

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant

from .boards import ws_r4d6f20_command, ws_r4d6f20_set_mode, ws_r413e16_command
from .devices import (
    ws_delete_device,
    ws_restore_device_template_controls,
    ws_save_device,
    ws_set_device_enabled,
    ws_write_entity,
)
from .diagnostics import (
    ws_clear_diagnostic_log,
    ws_diagnostic_read,
    ws_diagnostic_write,
    ws_export_activity_log,
    ws_manual_hex_write,
    ws_probe_registers,
    ws_scan_bus,
    ws_stop_probe_registers,
    ws_subscribe_traffic,
    ws_test_device_entities,
    ws_traffic_inspector,
    ws_verify_device_reads,
)
from .entities import ws_delete_entity, ws_save_entity
from .hub import (
    ws_get_data,
    ws_get_serial_status,
    ws_save_hub,
    ws_scan_usb_ports,
    ws_test_hub_connection,
)
from .templates import (
    ws_apply_template,
    ws_delete_template,
    ws_designer_validate,
    ws_get_templates,
    ws_save_and_apply_template,
    ws_save_template,
)
from .updates import ws_check_update, ws_install_update, ws_restart_home_assistant
from .views import ModbusUsbConfigView, ModbusUsbTemplatesView

_LOGGER = logging.getLogger(__name__)
_API_REGISTERED = False


async def async_register_api(hass: HomeAssistant) -> None:
    """Register WebSocket commands and REST views."""
    global _API_REGISTERED
    if _API_REGISTERED:
        return
    _API_REGISTERED = True

    # Register WebSocket handlers
    websocket_api.async_register_command(hass, ws_get_data)
    websocket_api.async_register_command(hass, ws_test_hub_connection)
    websocket_api.async_register_command(hass, ws_diagnostic_read)
    websocket_api.async_register_command(hass, ws_diagnostic_write)
    websocket_api.async_register_command(hass, ws_manual_hex_write)
    websocket_api.async_register_command(hass, ws_probe_registers)
    websocket_api.async_register_command(hass, ws_stop_probe_registers)
    websocket_api.async_register_command(hass, ws_write_entity)
    websocket_api.async_register_command(hass, ws_check_update)
    websocket_api.async_register_command(hass, ws_install_update)
    websocket_api.async_register_command(hass, ws_restart_home_assistant)
    websocket_api.async_register_command(hass, ws_r413e16_command)
    websocket_api.async_register_command(hass, ws_r4d6f20_command)
    websocket_api.async_register_command(hass, ws_r4d6f20_set_mode)
    websocket_api.async_register_command(hass, ws_test_device_entities)
    websocket_api.async_register_command(hass, ws_verify_device_reads)
    websocket_api.async_register_command(hass, ws_clear_diagnostic_log)
    websocket_api.async_register_command(hass, ws_export_activity_log)
    websocket_api.async_register_command(hass, ws_scan_bus)
    websocket_api.async_register_command(hass, ws_traffic_inspector)
    websocket_api.async_register_command(hass, ws_subscribe_traffic)
    websocket_api.async_register_command(hass, ws_designer_validate)
    websocket_api.async_register_command(hass, ws_scan_usb_ports)
    websocket_api.async_register_command(hass, ws_get_serial_status)
    websocket_api.async_register_command(hass, ws_restore_device_template_controls)
    websocket_api.async_register_command(hass, ws_save_device)
    websocket_api.async_register_command(hass, ws_set_device_enabled)
    websocket_api.async_register_command(hass, ws_delete_device)
    websocket_api.async_register_command(hass, ws_save_entity)
    websocket_api.async_register_command(hass, ws_delete_entity)
    websocket_api.async_register_command(hass, ws_save_hub)
    websocket_api.async_register_command(hass, ws_get_templates)
    websocket_api.async_register_command(hass, ws_save_template)
    websocket_api.async_register_command(hass, ws_delete_template)
    websocket_api.async_register_command(hass, ws_apply_template)
    websocket_api.async_register_command(hass, ws_save_and_apply_template)

    # Register HTTP views
    try:
        hass.http.register_view(ModbusUsbConfigView)
        hass.http.register_view(ModbusUsbTemplatesView)
    except Exception as err:
        _LOGGER.warning(
            "Could not register HTTP views (may already be registered): %s", err
        )

    _LOGGER.debug("Modbus USB WebSocket & REST API registered")


# Changelog:
# 2026-09-20 — v2.6.0: live traffic subscription (modbus_usb/subscribe_traffic) and
#              designer one-step save & apply (modbus_usb/save_and_apply_template).
# 2026-09-06 — Copy template image onto devices when applying eletechsup and other photo templates.
# Date modified: 2026-09-20
