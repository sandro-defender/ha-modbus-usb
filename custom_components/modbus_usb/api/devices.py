"""WebSocket commands for device CRUD and runtime control."""

from __future__ import annotations

import logging
import uuid

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv

from ..const import (
    CONF_DEVICE_CONTROLS,
    CONF_DEVICE_ID,
    CONF_DEVICES,
    CONF_ENABLED,
    CONF_ENTITIES,
    CONF_ENTITY_ID,
    CONF_ENTITY_TYPE,
    CONF_IMAGE,
    CONF_INFO_URL,
    CONF_MANUFACTURER,
    CONF_MODEL,
    CONF_SLAVE_ID,
    DATA_PRESERVE_SERIAL_RELOAD,
    DATA_SKIP_DEVICE_RELOAD,
    DOMAIN,
)
from ..templates import (
    async_load_templates,
)
from .helpers import _async_write_configured_switch, _get_entry

_LOGGER = logging.getLogger(__name__)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "modbus_usb/write_entity",
        vol.Required("entry_id"): cv.string,
        vol.Required("entity_id"): cv.string,
        vol.Required("state"): bool,
    }
)
@websocket_api.async_response
async def ws_write_entity(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Write a configured switch without guessing its Home Assistant entity ID."""
    try:
        entry = _get_entry(hass, msg["entry_id"])
        entity = next(
            (
                item
                for item in entry.options.get(CONF_ENTITIES, [])
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
    except Exception as err:
        _LOGGER.warning("Configured switch write failed: %s", err)
        connection.send_error(msg["id"], "write_failed", str(err))


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "modbus_usb/restore_device_template_controls",
        vol.Required("entry_id"): cv.string,
        vol.Required("device_id"): cv.string,
    }
)
@websocket_api.async_response
async def ws_restore_device_template_controls(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Restore command metadata from the matching built-in template.

    Older devices created before command metadata existed keep their working
    entities, but lack the controls object used by the board-tools page.
    """
    try:
        entry = _get_entry(hass, msg["entry_id"])
        devices = [dict(device) for device in entry.options.get(CONF_DEVICES, [])]
        device = next(
            (item for item in devices if item.get("id") == msg["device_id"]), None
        )
        if device is None:
            raise ValueError("Configured device was not found")
        model = str(device.get(CONF_MODEL, "")).strip().lower()
        manufacturer = str(device.get(CONF_MANUFACTURER, "")).strip().lower()
        templates = await async_load_templates(hass)
        template = next(
            (
                item
                for item in templates
                if item.get(CONF_DEVICE_CONTROLS)
                and str(item.get(CONF_MODEL, "")).strip().lower() == model
                and (
                    not manufacturer
                    or str(item.get(CONF_MANUFACTURER, "")).strip().lower()
                    == manufacturer
                )
            ),
            None,
        )
        if template is None:
            raise ValueError("No standard command template matches this device model")
        restored = {
            **device,
            CONF_DEVICE_CONTROLS: template[CONF_DEVICE_CONTROLS],
            CONF_IMAGE: template.get(CONF_IMAGE) or device.get(CONF_IMAGE, ""),
            CONF_INFO_URL: template.get(CONF_INFO_URL) or device.get(CONF_INFO_URL, ""),
        }
        new_options = dict(entry.options or {})
        new_options[CONF_DEVICES] = [
            restored if item.get("id") == device["id"] else item for item in devices
        ]
        hass.data.setdefault(DOMAIN, {}).setdefault(
            DATA_PRESERVE_SERIAL_RELOAD, set()
        ).add(entry.entry_id)
        hass.config_entries.async_update_entry(entry, options=new_options)
        connection.send_result(
            msg["id"], {"success": True, "template": template.get("filename")}
        )
    except Exception as err:
        _LOGGER.warning("restore_device_template_controls failed: %s", err)
        connection.send_error(msg["id"], "restore_template_controls_failed", str(err))


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "modbus_usb/save_device",
        vol.Required("entry_id"): cv.string,
        vol.Required("device"): dict,
    }
)
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

        existing_idx = next(
            (i for i, d in enumerate(devices) if d.get("id") == device_id), None
        )
        if existing_idx is not None:
            # The editor only sends editable fields. Keep device metadata and
            # the enabled state so an edit cannot accidentally re-enable it.
            device = {**devices[existing_idx], **device}
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
        # A device edit reloads entity platforms so HA can add/remove entities.
        # Preserve the shared coordinator during that reload to avoid dropping
        # the serial bus just because the sidebar device list changed.
        hass.data.setdefault(DOMAIN, {}).setdefault(
            DATA_PRESERVE_SERIAL_RELOAD, set()
        ).add(entry.entry_id)
        hass.config_entries.async_update_entry(entry, options=new_options)

        connection.send_result(msg["id"], {"success": True, "device": device})
    except Exception as err:
        _LOGGER.error("ws_save_device failed: %s", err, exc_info=True)
        connection.send_error(msg["id"], "error", str(err))


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "modbus_usb/set_device_enabled",
        vol.Required("entry_id"): cv.string,
        vol.Required("device_id"): cv.string,
        vol.Required("enabled"): cv.boolean,
    }
)
@websocket_api.async_response
async def ws_set_device_enabled(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Persist one device's enabled state; the entry listener reloads platforms."""
    try:
        entry = _get_entry(hass, msg["entry_id"])
        device_id = msg["device_id"]
        devices = [dict(device) for device in entry.options.get(CONF_DEVICES, [])]
        if not any(device.get("id") == device_id for device in devices):
            raise ValueError("Configured device was not found")
        enabled = bool(msg["enabled"])
        new_options = dict(entry.options or {})
        new_options[CONF_DEVICES] = [
            {**device, CONF_ENABLED: enabled}
            if device.get("id") == device_id
            else device
            for device in devices
        ]
        hass.data.setdefault(DOMAIN, {}).setdefault(DATA_SKIP_DEVICE_RELOAD, set()).add(
            entry.entry_id
        )
        hass.config_entries.async_update_entry(entry, options=new_options)
        connection.send_result(msg["id"], {"success": True, "enabled": enabled})
    except Exception as err:
        _LOGGER.warning("set_device_enabled failed: %s", err)
        connection.send_error(msg["id"], "set_device_enabled_failed", str(err))


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "modbus_usb/delete_device",
        vol.Required("entry_id"): cv.string,
        vol.Required("device_id"): cv.string,
        vol.Optional("delete_entities", default=True): cv.boolean,
    }
)
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
        devices = [
            d for d in new_options.get(CONF_DEVICES, []) if d.get("id") != device_id
        ]
        new_options[CONF_DEVICES] = devices

        if delete_entities:
            entities = [
                e
                for e in new_options.get(CONF_ENTITIES, [])
                if e.get(CONF_DEVICE_ID) != device_id
            ]
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

        # Entity removal needs a platform reload, but the bus itself has not
        # changed. Keep its current serial client open across that reload.
        hass.data.setdefault(DOMAIN, {}).setdefault(
            DATA_PRESERVE_SERIAL_RELOAD, set()
        ).add(entry.entry_id)
        hass.config_entries.async_update_entry(entry, options=new_options)
        connection.send_result(msg["id"], {"success": True})
    except Exception as err:
        _LOGGER.error("ws_delete_device failed: %s", err, exc_info=True)
        connection.send_error(msg["id"], "error", str(err))
