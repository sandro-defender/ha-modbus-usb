"""WebSocket commands for entity CRUD."""

from __future__ import annotations

import logging
import uuid

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er

from ..const import (
    CONF_ADDRESS,
    CONF_ADDRESSES,
    CONF_ENTITIES,
    CONF_ENTITY_ID,
    CONF_ENTITY_TYPE,
    CONF_MAX_VALUE,
    CONF_MIN_VALUE,
    CONF_OFF_VALUE,
    CONF_ON_VALUE,
    CONF_REGISTER_TYPE,
    CONF_SCALE,
    CONF_SLAVE_ID,
    CONF_STATE_ON_VALUE,
    CONF_STEP,
    DATA_PRESERVE_SERIAL_RELOAD,
    DOMAIN,
    ENTITY_TYPES,
    REGISTER_TYPE_COIL,
    REGISTER_TYPE_DISCRETE,
    REGISTER_TYPE_HOLDING,
    REGISTER_TYPE_INPUT,
)
from .helpers import _get_entry

_LOGGER = logging.getLogger(__name__)


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "modbus_usb/save_entity",
        vol.Required("entry_id"): cv.string,
        vol.Required("entity"): dict,
    }
)
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

        entity_type = entity.get(CONF_ENTITY_TYPE)
        if entity_type not in ENTITY_TYPES:
            raise ValueError(f"Unsupported entity type: {entity_type!r}")

        # Coerce numeric fields
        if CONF_ADDRESS in entity:
            entity[CONF_ADDRESS] = int(entity[CONF_ADDRESS])
            if not 0 <= entity[CONF_ADDRESS] <= 65535:
                raise ValueError("Register addresses must be between 0 and 65535")
        register_type = entity.get(CONF_REGISTER_TYPE)
        allowed_registers = {
            "sensor": [REGISTER_TYPE_HOLDING, REGISTER_TYPE_INPUT],
            "switch": [REGISTER_TYPE_COIL, REGISTER_TYPE_HOLDING],
            "binary_sensor": [REGISTER_TYPE_COIL, REGISTER_TYPE_DISCRETE],
            "number": [REGISTER_TYPE_HOLDING],
        }[entity_type]
        if register_type not in allowed_registers:
            raise ValueError(
                f"A {entity_type} cannot use register type {register_type!r}"
            )
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

        existing_idx = next(
            (i for i, e in enumerate(entities) if e.get(CONF_ENTITY_ID) == ent_id), None
        )
        if existing_idx is not None:
            # The editor exposes only the fields it can safely change. Keep
            # template-only settings such as assumed_state and a device image
            # when an existing entity is edited from the sidebar.
            entities[existing_idx] = {**entities[existing_idx], **entity}
        else:
            entities.append(entity)

        new_options[CONF_ENTITIES] = entities
        hass.data.setdefault(DOMAIN, {}).setdefault(
            DATA_PRESERVE_SERIAL_RELOAD, set()
        ).add(entry.entry_id)
        hass.config_entries.async_update_entry(entry, options=new_options)

        connection.send_result(msg["id"], {"success": True, "entity": entity})
    except Exception as err:
        _LOGGER.error("ws_save_entity failed: %s", err, exc_info=True)
        connection.send_error(msg["id"], "error", str(err))


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "modbus_usb/delete_entity",
        vol.Required("entry_id"): cv.string,
        vol.Required("entity_id"): cv.string,
    }
)
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
                entity
                for entity in new_options.get(CONF_ENTITIES, [])
                if entity.get(CONF_ENTITY_ID) == ent_id
            ),
            None,
        )
        entities = [
            e
            for e in new_options.get(CONF_ENTITIES, [])
            if e.get(CONF_ENTITY_ID) != ent_id
        ]
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

        hass.data.setdefault(DOMAIN, {}).setdefault(
            DATA_PRESERVE_SERIAL_RELOAD, set()
        ).add(entry.entry_id)
        hass.config_entries.async_update_entry(entry, options=new_options)
        connection.send_result(msg["id"], {"success": True})
    except Exception as err:
        _LOGGER.error("ws_delete_entity failed: %s", err, exc_info=True)
        connection.send_error(msg["id"], "error", str(err))
