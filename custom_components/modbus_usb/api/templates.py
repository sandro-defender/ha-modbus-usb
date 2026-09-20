"""WebSocket commands for YAML template management."""

from __future__ import annotations

import logging
import uuid

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv

from ..const import (
    CONF_ADDRESS,
    CONF_ADDRESSES,
    CONF_DEVICE_CONTROLS,
    CONF_DEVICE_ID,
    CONF_DEVICES,
    CONF_ENABLED,
    CONF_ENTITIES,
    CONF_ENTITY_ID,
    CONF_IMAGE,
    CONF_INFO_URL,
    CONF_M0_SHORT,
    CONF_SLAVE_ID,
    DATA_PRESERVE_SERIAL_RELOAD,
    DOMAIN,
)
from ..designer import async_validate_template_design
from ..templates import (
    async_delete_template,
    async_load_templates,
    async_save_template,
)
from .helpers import _get_entry

_LOGGER = logging.getLogger(__name__)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "modbus_usb/get_templates",
    }
)
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


@websocket_api.websocket_command(
    {
        vol.Required("type"): "modbus_usb/designer_validate",
        vol.Required("entry_id"): cv.string,
        vol.Required("content"): cv.string,
        vol.Optional("slave_id"): vol.All(vol.Coerce(int), vol.Range(min=1, max=247)),
        vol.Optional("test_reads", default=True): bool,
    }
)
@websocket_api.async_response
async def ws_designer_validate(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Validate a draft template and test-read it against the live bus."""
    try:
        coordinator = hass.data[DOMAIN][msg["entry_id"]]
        result = await async_validate_template_design(
            hass,
            coordinator,
            msg["content"],
            slave_id=msg.get("slave_id"),
            test_reads=msg.get("test_reads", True),
        )
        connection.send_result(msg["id"], result)
    except KeyError:
        connection.send_error(
            msg["id"], "not_found", f"Unknown config entry '{msg['entry_id']}'"
        )
    except ValueError as err:
        # Structural validation failures are expected user input errors; the
        # designer renders them inline rather than treating them as crashes.
        connection.send_result(
            msg["id"], {"valid": False, "error": str(err), "entities": []}
        )
    except Exception as err:
        _LOGGER.error("ws_designer_validate failed: %s", err, exc_info=True)
        connection.send_error(msg["id"], "designer_failed", str(err))


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "modbus_usb/save_template",
        vol.Required("filename"): cv.string,
        vol.Required("content"): cv.string,
    }
)
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


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "modbus_usb/delete_template",
        vol.Required("filename"): cv.string,
    }
)
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


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "modbus_usb/apply_template",
        vol.Required("entry_id"): cv.string,
        vol.Optional("device_id"): cv.string,
        vol.Optional("device_name"): cv.string,
        vol.Optional("slave_id"): vol.All(vol.Coerce(int), vol.Range(min=1, max=247)),
        vol.Optional("template_filename"): cv.string,
        vol.Optional("template_id"): cv.string,
        vol.Optional("selected_entities"): list,
        vol.Optional("address_offset", default=0): vol.Coerce(int),
        vol.Optional("m0_short", default=False): bool,
    }
)
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
                CONF_M0_SHORT: bool(
                    msg.get("m0_short", target_tpl.get(CONF_M0_SHORT, False))
                ),
                CONF_ENABLED: True,
            }
            devices.append(new_device)
        else:
            # Look up existing device to inherit slave_id if not given
            existing_dev = next((d for d in devices if d.get("id") == device_id), None)
            if existing_dev and "slave_id" in existing_dev:
                slave_id = existing_dev["slave_id"]
            if (
                existing_dev
                and target_tpl.get(CONF_IMAGE)
                and not existing_dev.get(CONF_IMAGE)
            ):
                existing_dev = dict(existing_dev)
                existing_dev[CONF_IMAGE] = target_tpl[CONF_IMAGE]
                devices = [
                    existing_dev if d.get("id") == device_id else d for d in devices
                ]
            if (
                existing_dev
                and target_tpl.get(CONF_INFO_URL)
                and not existing_dev.get(CONF_INFO_URL)
            ):
                existing_dev = dict(existing_dev)
                existing_dev[CONF_INFO_URL] = target_tpl[CONF_INFO_URL]
                devices = [
                    existing_dev if d.get("id") == device_id else d for d in devices
                ]
            if (
                existing_dev
                and target_tpl.get(CONF_DEVICE_CONTROLS)
                and not existing_dev.get(CONF_DEVICE_CONTROLS)
            ):
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
                e
                for i, e in enumerate(tpl_entities)
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
                    int(address) + address_offset for address in ent[CONF_ADDRESSES]
                ]
            entities.append(ent)
            added_entities.append(ent)

        new_options[CONF_DEVICES] = devices
        new_options[CONF_ENTITIES] = entities

        # Applying a board template changes only entity configuration. Keep the
        # already-open USB serial client while HA rebuilds those entities.
        hass.data.setdefault(DOMAIN, {}).setdefault(
            DATA_PRESERVE_SERIAL_RELOAD, set()
        ).add(entry.entry_id)
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
