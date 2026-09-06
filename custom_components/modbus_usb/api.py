"""WebSocket and REST API endpoints for Modbus USB Controller.

Provides complete sidebar UI management for:
  - Hub serial and polling configuration
  - Separated, editable devices
  - Entity management (sensors, switches, binary sensors, numbers)
  - YAML template management (CRUD)
  - Template application to devices
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

import voluptuous as vol
from aiohttp import web
from homeassistant.components import websocket_api
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv

from .const import (
    CONF_ADDRESS,
    CONF_BAUDRATE,
    CONF_BYTESIZE,
    CONF_DATA_TYPE,
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
    CONF_PARITY,
    CONF_PORT,
    CONF_REGISTER_TYPE,
    CONF_SCALE,
    CONF_SCAN_INTERVAL,
    CONF_SLAVE_ID,
    CONF_STATE_CLASS,
    CONF_STEP,
    CONF_STOPBITS,
    CONF_UNIT_OF_MEASUREMENT,
    DOMAIN,
)
from .templates import (
    async_delete_template,
    async_load_templates,
    async_save_template,
)

_LOGGER = logging.getLogger(__name__)


def _get_entry(hass: HomeAssistant, entry_id: str):
    """Retrieve config entry by ID or raise an error."""
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None:
        raise ValueError(f"Config entry '{entry_id}' not found")
    return entry


def _format_entry_data(entry) -> dict[str, Any]:
    """Serialize config entry for UI consumption."""
    options = dict(entry.options or {})
    devices = list(options.get(CONF_DEVICES, []))
    entities = list(options.get(CONF_ENTITIES, []))

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
        entries_data = [_format_entry_data(e) for e in entries]
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

        existing_idx = next((i for i, d in enumerate(devices) if d.get("id") == device_id), None)
        if existing_idx is not None:
            devices[existing_idx] = device
        else:
            devices.append(device)

        new_options[CONF_DEVICES] = devices
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

        new_options = dict(entry.options or {})
        entities = list(new_options.get(CONF_ENTITIES, []))

        existing_idx = next((i for i, e in enumerate(entities) if e.get(CONF_ENTITY_ID) == ent_id), None)
        if existing_idx is not None:
            entities[existing_idx] = entity
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
        entities = [e for e in new_options.get(CONF_ENTITIES, []) if e.get(CONF_ENTITY_ID) != ent_id]
        new_options[CONF_ENTITIES] = entities

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
            }
            devices.append(new_device)
        else:
            # Look up existing device to inherit slave_id if not given
            existing_dev = next((d for d in devices if d.get("id") == device_id), None)
            if existing_dev and "slave_id" in existing_dev:
                slave_id = existing_dev["slave_id"]

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
        entries_data = [_format_entry_data(e) for e in entries]
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
