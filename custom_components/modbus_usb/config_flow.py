"""Config and options flow for Modbus USB Controller."""
from __future__ import annotations

import uuid
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
try:
    from homeassistant.config_entries import ConfigFlowResult
except ImportError:
    from homeassistant.data_entry_flow import FlowResult as ConfigFlowResult

from .const import (
    BAUDRATE_OPTIONS,
    BYTESIZE_OPTIONS,
    CONF_ADDRESS,
    CONF_BAUDRATE,
    CONF_BYTESIZE,
    CONF_DATA_TYPE,
    CONF_DEVICE_CLASS,
    CONF_ENTITIES,
    CONF_ENTITY_ID,
    CONF_ENTITY_TYPE,
    CONF_MAX_VALUE,
    CONF_MIN_VALUE,
    CONF_MODE,
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
    DATA_TYPES,
    DEFAULT_BAUDRATE,
    DEFAULT_BYTESIZE,
    DEFAULT_PARITY,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SLAVE_ID,
    DEFAULT_STOPBITS,
    DEVICE_CLASS_OPTIONS,
    DOMAIN,
    PARITY_OPTIONS,
    REGISTER_TYPE_COIL,
    REGISTER_TYPE_DISCRETE,
    REGISTER_TYPE_HOLDING,
    REGISTER_TYPES_SENSOR,
    REGISTER_TYPES_SWITCH,
    STATE_CLASS_OPTIONS,
    STOPBITS_OPTIONS,
)


def _hub_schema(defaults: dict | None = None) -> vol.Schema:
    d = defaults or {}
    return vol.Schema(
        {
            vol.Required(CONF_NAME, default=d.get(CONF_NAME, "Modbus USB Controller")): str,
            vol.Required(CONF_PORT, default=d.get(CONF_PORT, DEFAULT_PORT)): str,
            vol.Required(CONF_BAUDRATE, default=d.get(CONF_BAUDRATE, DEFAULT_BAUDRATE)): vol.In(
                BAUDRATE_OPTIONS
            ),
            vol.Required(CONF_BYTESIZE, default=d.get(CONF_BYTESIZE, DEFAULT_BYTESIZE)): vol.In(
                BYTESIZE_OPTIONS
            ),
            vol.Required(CONF_PARITY, default=d.get(CONF_PARITY, DEFAULT_PARITY)): vol.In(
                list(PARITY_OPTIONS.keys())
            ),
            vol.Required(CONF_STOPBITS, default=d.get(CONF_STOPBITS, DEFAULT_STOPBITS)): vol.In(
                STOPBITS_OPTIONS
            ),
            vol.Required(CONF_SLAVE_ID, default=d.get(CONF_SLAVE_ID, DEFAULT_SLAVE_ID)): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=247)
            ),
            vol.Required(
                CONF_SCAN_INTERVAL, default=d.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=3600)),
        }
    )


class ModbusUsbConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the initial setup of the Modbus USB hub."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(f"{user_input[CONF_PORT]}_{user_input[CONF_SLAVE_ID]}")
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=user_input[CONF_NAME],
                data={
                    CONF_PORT: user_input[CONF_PORT],
                    CONF_BAUDRATE: user_input[CONF_BAUDRATE],
                    CONF_BYTESIZE: user_input[CONF_BYTESIZE],
                    CONF_PARITY: user_input[CONF_PARITY],
                    CONF_STOPBITS: user_input[CONF_STOPBITS],
                    CONF_SLAVE_ID: user_input[CONF_SLAVE_ID],
                },
                options={
                    CONF_SCAN_INTERVAL: user_input[CONF_SCAN_INTERVAL],
                    CONF_ENTITIES: [],
                },
            )

        return self.async_show_form(step_id="user", data_schema=_hub_schema(), errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> "ModbusUsbOptionsFlow":
        # Do NOT pass/store config_entry ourselves. Since Home Assistant
        # 2025.12, OptionsFlow.config_entry is a read-only property that
        # the frontend/flow manager sets automatically; manually assigning
        # it (as this used to do in __init__) now raises
        # AttributeError: property 'config_entry' has no setter,
        # which is what was causing the 500 error when opening settings.
        return ModbusUsbOptionsFlow()


class ModbusUsbOptionsFlow(config_entries.OptionsFlow):
    """Options flow: manage scan interval and the list of sensor/switch entities."""

    def __init__(self) -> None:
        self._editing_id: str | None = None
        self._pending_entity_type: str | None = None

    def _entities(self) -> list[dict]:
        return list(self.config_entry.options.get(CONF_ENTITIES, []))

    async def _save_entities(self, entities: list[dict]) -> ConfigFlowResult:
        new_options = dict(self.config_entry.options)
        new_options[CONF_ENTITIES] = entities
        return self.async_create_entry(title="", data=new_options)

    # ---------- Main menu ----------
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options=["settings", "add_entity", "manage_entities"],
        )

    # ---------- Global settings (scan interval) ----------
    async def async_step_settings(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            new_options = dict(self.config_entry.options)
            new_options[CONF_SCAN_INTERVAL] = user_input[CONF_SCAN_INTERVAL]
            return self.async_create_entry(title="", data=new_options)

        current = self.config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        schema = vol.Schema(
            {
                vol.Required(CONF_SCAN_INTERVAL, default=current): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=3600)
                )
            }
        )
        return self.async_show_form(step_id="settings", data_schema=schema)

    # ---------- Add entity: choose type ----------
    async def async_step_add_entity(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._pending_entity_type = user_input[CONF_ENTITY_TYPE]
            self._editing_id = None
            if self._pending_entity_type == "sensor":
                return await self.async_step_edit_sensor()
            if self._pending_entity_type == "binary_sensor":
                return await self.async_step_edit_binary_sensor()
            if self._pending_entity_type == "number":
                return await self.async_step_edit_number()
            return await self.async_step_edit_switch()

        schema = vol.Schema(
            {
                vol.Required(CONF_ENTITY_TYPE, default="sensor"): vol.In(
                    ["sensor", "switch", "binary_sensor", "number"]
                )
            }
        )
        return self.async_show_form(step_id="add_entity", data_schema=schema)

    # ---------- Manage: pick an existing entity to edit or remove ----------
    async def async_step_manage_entities(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entities = self._entities()
        if not entities:
            return self.async_show_form(
                step_id="manage_entities",
                data_schema=vol.Schema({}),
                description_placeholders={"info": "No entities configured yet."},
                errors={"base": "no_entities"},
            )

        choices = {
            e[CONF_ENTITY_ID]: f"{e[CONF_NAME]} ({e[CONF_ENTITY_TYPE]}, addr {e[CONF_ADDRESS]})"
            for e in entities
        }

        if user_input is not None:
            selected_id = user_input["entity"]
            action = user_input["action"]
            self._editing_id = selected_id
            if action == "remove":
                new_entities = [e for e in entities if e[CONF_ENTITY_ID] != selected_id]
                return await self._save_entities(new_entities)
            # edit
            target = next(e for e in entities if e[CONF_ENTITY_ID] == selected_id)
            self._pending_entity_type = target[CONF_ENTITY_TYPE]
            if target[CONF_ENTITY_TYPE] == "sensor":
                return await self.async_step_edit_sensor()
            if target[CONF_ENTITY_TYPE] == "binary_sensor":
                return await self.async_step_edit_binary_sensor()
            if target[CONF_ENTITY_TYPE] == "number":
                return await self.async_step_edit_number()
            return await self.async_step_edit_switch()

        schema = vol.Schema(
            {
                vol.Required("entity"): vol.In(choices),
                vol.Required("action", default="edit"): vol.In(["edit", "remove"]),
            }
        )
        return self.async_show_form(step_id="manage_entities", data_schema=schema)

    # ---------- Sensor add/edit form ----------
    async def async_step_edit_sensor(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        existing = None
        if self._editing_id:
            existing = next(
                (e for e in self._entities() if e[CONF_ENTITY_ID] == self._editing_id), None
            )
        d = existing or {}

        if user_input is not None:
            entities = self._entities()
            unit = user_input.get(CONF_UNIT_OF_MEASUREMENT, "")
            device_class = user_input.get(CONF_DEVICE_CLASS, "none")
            state_class = user_input.get(CONF_STATE_CLASS, "none")
            new_entry = {
                CONF_ENTITY_ID: self._editing_id or str(uuid.uuid4()),
                CONF_ENTITY_TYPE: "sensor",
                CONF_NAME: user_input[CONF_NAME],
                CONF_REGISTER_TYPE: user_input[CONF_REGISTER_TYPE],
                CONF_ADDRESS: user_input[CONF_ADDRESS],
                CONF_DATA_TYPE: user_input[CONF_DATA_TYPE],
                CONF_SCALE: user_input.get(CONF_SCALE, 1),
                CONF_UNIT_OF_MEASUREMENT: unit,
                CONF_DEVICE_CLASS: None if device_class == "none" else device_class,
                CONF_STATE_CLASS: None if state_class == "none" else state_class,
            }
            if self._editing_id:
                entities = [
                    new_entry if e[CONF_ENTITY_ID] == self._editing_id else e for e in entities
                ]
            else:
                entities.append(new_entry)
            return await self._save_entities(entities)

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=d.get(CONF_NAME, "")): str,
                vol.Required(
                    CONF_REGISTER_TYPE, default=d.get(CONF_REGISTER_TYPE, REGISTER_TYPE_HOLDING)
                ): vol.In(REGISTER_TYPES_SENSOR),
                vol.Required(CONF_ADDRESS, default=d.get(CONF_ADDRESS, 0)): vol.All(
                    vol.Coerce(int), vol.Range(min=0, max=65535)
                ),
                vol.Required(CONF_DATA_TYPE, default=d.get(CONF_DATA_TYPE, "uint16")): vol.In(
                    DATA_TYPES
                ),
                vol.Optional(CONF_SCALE, default=d.get(CONF_SCALE, 1)): vol.Coerce(float),
                vol.Optional(CONF_UNIT_OF_MEASUREMENT, default=d.get(CONF_UNIT_OF_MEASUREMENT, "")): str,
                vol.Optional(
                    CONF_DEVICE_CLASS, default=d.get(CONF_DEVICE_CLASS) or "none"
                ): vol.In(DEVICE_CLASS_OPTIONS),
                vol.Optional(
                    CONF_STATE_CLASS, default=d.get(CONF_STATE_CLASS) or "none"
                ): vol.In(STATE_CLASS_OPTIONS),
            }
        )
        return self.async_show_form(step_id="edit_sensor", data_schema=schema)

    # ---------- Switch add/edit form ----------
    async def async_step_edit_switch(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        existing = None
        if self._editing_id:
            existing = next(
                (e for e in self._entities() if e[CONF_ENTITY_ID] == self._editing_id), None
            )
        d = existing or {}

        if user_input is not None:
            entities = self._entities()
            new_entry = {
                CONF_ENTITY_ID: self._editing_id or str(uuid.uuid4()),
                CONF_ENTITY_TYPE: "switch",
                CONF_NAME: user_input[CONF_NAME],
                CONF_REGISTER_TYPE: user_input[CONF_REGISTER_TYPE],
                CONF_ADDRESS: user_input[CONF_ADDRESS],
                CONF_ON_VALUE: user_input.get(CONF_ON_VALUE, 1),
                CONF_OFF_VALUE: user_input.get(CONF_OFF_VALUE, 0),
            }
            if self._editing_id:
                entities = [
                    new_entry if e[CONF_ENTITY_ID] == self._editing_id else e for e in entities
                ]
            else:
                entities.append(new_entry)
            return await self._save_entities(entities)

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=d.get(CONF_NAME, "")): str,
                vol.Required(
                    CONF_REGISTER_TYPE, default=d.get(CONF_REGISTER_TYPE, REGISTER_TYPE_COIL)
                ): vol.In(REGISTER_TYPES_SWITCH),
                vol.Required(CONF_ADDRESS, default=d.get(CONF_ADDRESS, 0)): vol.All(
                    vol.Coerce(int), vol.Range(min=0, max=65535)
                ),
                vol.Optional(CONF_ON_VALUE, default=d.get(CONF_ON_VALUE, 1)): vol.Coerce(int),
                vol.Optional(CONF_OFF_VALUE, default=d.get(CONF_OFF_VALUE, 0)): vol.Coerce(int),
            }
        )
        return self.async_show_form(step_id="edit_switch", data_schema=schema)

    # ---------- Binary sensor add/edit form ----------
    async def async_step_edit_binary_sensor(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        existing = None
        if self._editing_id:
            existing = next(
                (e for e in self._entities() if e[CONF_ENTITY_ID] == self._editing_id), None
            )
        d = existing or {}

        if user_input is not None:
            entities = self._entities()
            device_class = user_input.get(CONF_DEVICE_CLASS, "none")
            new_entry = {
                CONF_ENTITY_ID: self._editing_id or str(uuid.uuid4()),
                CONF_ENTITY_TYPE: "binary_sensor",
                CONF_NAME: user_input[CONF_NAME],
                CONF_REGISTER_TYPE: user_input[CONF_REGISTER_TYPE],
                CONF_ADDRESS: user_input[CONF_ADDRESS],
                CONF_DEVICE_CLASS: None if device_class == "none" else device_class,
            }
            if self._editing_id:
                entities = [
                    new_entry if e[CONF_ENTITY_ID] == self._editing_id else e
                    for e in entities
                ]
            else:
                entities.append(new_entry)
            return await self._save_entities(entities)

        _BINARY_REGISTER_TYPES = [REGISTER_TYPE_COIL, REGISTER_TYPE_DISCRETE]
        _BINARY_DEVICE_CLASSES = [
            "none", "motion", "door", "window", "smoke", "moisture",
            "connectivity", "power", "plug", "battery", "occupancy",
        ]
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=d.get(CONF_NAME, "")): str,
                vol.Required(
                    CONF_REGISTER_TYPE, default=d.get(CONF_REGISTER_TYPE, REGISTER_TYPE_COIL)
                ): vol.In(_BINARY_REGISTER_TYPES),
                vol.Required(CONF_ADDRESS, default=d.get(CONF_ADDRESS, 0)): vol.All(
                    vol.Coerce(int), vol.Range(min=0, max=65535)
                ),
                vol.Optional(
                    CONF_DEVICE_CLASS, default=d.get(CONF_DEVICE_CLASS) or "none"
                ): vol.In(_BINARY_DEVICE_CLASSES),
            }
        )
        return self.async_show_form(step_id="edit_binary_sensor", data_schema=schema)

    # ---------- Number add/edit form ----------
    async def async_step_edit_number(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        existing = None
        if self._editing_id:
            existing = next(
                (e for e in self._entities() if e[CONF_ENTITY_ID] == self._editing_id), None
            )
        d = existing or {}

        if user_input is not None:
            entities = self._entities()
            new_entry = {
                CONF_ENTITY_ID: self._editing_id or str(uuid.uuid4()),
                CONF_ENTITY_TYPE: "number",
                CONF_NAME: user_input[CONF_NAME],
                CONF_REGISTER_TYPE: user_input[CONF_REGISTER_TYPE],
                CONF_ADDRESS: user_input[CONF_ADDRESS],
                CONF_DATA_TYPE: user_input[CONF_DATA_TYPE],
                CONF_SCALE: user_input.get(CONF_SCALE, 1),
                CONF_UNIT_OF_MEASUREMENT: user_input.get(CONF_UNIT_OF_MEASUREMENT, ""),
                CONF_MIN_VALUE: user_input.get(CONF_MIN_VALUE, 0),
                CONF_MAX_VALUE: user_input.get(CONF_MAX_VALUE, 65535),
                CONF_STEP: user_input.get(CONF_STEP, 1),
                CONF_MODE: user_input.get(CONF_MODE, "slider"),
            }
            if self._editing_id:
                entities = [
                    new_entry if e[CONF_ENTITY_ID] == self._editing_id else e
                    for e in entities
                ]
            else:
                entities.append(new_entry)
            return await self._save_entities(entities)

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=d.get(CONF_NAME, "")): str,
                vol.Required(
                    CONF_REGISTER_TYPE, default=d.get(CONF_REGISTER_TYPE, REGISTER_TYPE_HOLDING)
                ): vol.In([REGISTER_TYPE_HOLDING]),
                vol.Required(CONF_ADDRESS, default=d.get(CONF_ADDRESS, 0)): vol.All(
                    vol.Coerce(int), vol.Range(min=0, max=65535)
                ),
                vol.Required(CONF_DATA_TYPE, default=d.get(CONF_DATA_TYPE, "uint16")): vol.In(
                    DATA_TYPES
                ),
                vol.Optional(CONF_SCALE, default=d.get(CONF_SCALE, 1)): vol.Coerce(float),
                vol.Optional(
                    CONF_UNIT_OF_MEASUREMENT, default=d.get(CONF_UNIT_OF_MEASUREMENT, "")
                ): str,
                vol.Optional(
                    CONF_MIN_VALUE, default=d.get(CONF_MIN_VALUE, 0)
                ): vol.Coerce(float),
                vol.Optional(
                    CONF_MAX_VALUE, default=d.get(CONF_MAX_VALUE, 65535)
                ): vol.Coerce(float),
                vol.Optional(CONF_STEP, default=d.get(CONF_STEP, 1)): vol.Coerce(float),
                vol.Optional(CONF_MODE, default=d.get(CONF_MODE, "slider")): vol.In(
                    ["slider", "box"]
                ),
            }
        )
        return self.async_show_form(step_id="edit_number", data_schema=schema)