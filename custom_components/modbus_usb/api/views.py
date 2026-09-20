"""REST API fallback views for the Modbus USB integration."""

from __future__ import annotations

import logging

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from ..const import (
    DOMAIN,
)
from ..templates import (
    async_delete_template,
    async_load_templates,
    async_save_template,
)
from .helpers import _format_entry_data

_LOGGER = logging.getLogger(__name__)


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
            return self.json(
                {"error": "filename and content are required"}, status_code=400
            )
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
            return self.json(
                {"error": "filename query parameter required"}, status_code=400
            )
        deleted = await async_delete_template(hass, filename)
        return self.json({"success": deleted})
