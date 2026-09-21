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
    CONF_API_ENCRYPTION_KEY,
    CONF_API_PASSWORD,
    CONF_API_PORT,
    CONF_BAUDRATE,
    CONF_BYTESIZE,
    CONF_DATA_TYPE,
    CONF_DEVICE_CLASS,
    CONF_ENTITIES,
    CONF_ENTITY_ID,
    CONF_ENTITY_TYPE,
    CONF_ESPHOME_EVENT,
    CONF_ESPHOME_SERVICE,
    CONF_HOST,
    CONF_MAX_VALUE,
    CONF_MIN_VALUE,
    CONF_MODE,
    CONF_NAME,
    CONF_OFF_VALUE,
    CONF_ON_VALUE,
    CONF_PARITY,
    CONF_PORT,
    CONF_REGISTER_TYPE,
    CONF_RESPONSE_TIMEOUT,
    CONF_SCALE,
    CONF_SCAN_INTERVAL,
    CONF_SLAVE_ID,
    CONF_STATE_CLASS,
    CONF_STEP,
    CONF_STOPBITS,
    CONF_TCP_PORT,
    CONF_TRANSPORT,
    CONF_UNIT_OF_MEASUREMENT,
    DATA_TYPES,
    DEFAULT_API_PORT,
    DEFAULT_BAUDRATE,
    DEFAULT_BYTESIZE,
    DEFAULT_ESPHOME_EVENT,
    DEFAULT_ESPHOME_SERVICE,
    DEFAULT_PARITY,
    DEFAULT_PORT,
    DEFAULT_RESPONSE_TIMEOUT_API,
    DEFAULT_RESPONSE_TIMEOUT_TCP,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SLAVE_ID,
    DEFAULT_STOPBITS,
    DEFAULT_TCP_PORT,
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
    TRANSPORT_ESPHOME_API,
    TRANSPORT_ESPHOME_TCP,
    TRANSPORT_SERIAL,
    TRANSPORTS,
)
from .transport import (
    connection_config,
    describe_transport,
    probe_connection,
    unique_id_for,
)


def _line_settings_schema(d: dict) -> dict:
    """Baud/data bits/parity/stop bits fields shared by every transport."""
    return {
        vol.Required(
            CONF_BAUDRATE, default=d.get(CONF_BAUDRATE, DEFAULT_BAUDRATE)
        ): vol.In(BAUDRATE_OPTIONS),
        vol.Required(
            CONF_BYTESIZE, default=d.get(CONF_BYTESIZE, DEFAULT_BYTESIZE)
        ): vol.In(BYTESIZE_OPTIONS),
        vol.Required(CONF_PARITY, default=d.get(CONF_PARITY, DEFAULT_PARITY)): vol.In(
            list(PARITY_OPTIONS.keys())
        ),
        vol.Required(
            CONF_STOPBITS, default=d.get(CONF_STOPBITS, DEFAULT_STOPBITS)
        ): vol.In(STOPBITS_OPTIONS),
    }


def _polling_schema(d: dict) -> dict:
    return {
        vol.Required(
            CONF_SLAVE_ID, default=d.get(CONF_SLAVE_ID, DEFAULT_SLAVE_ID)
        ): vol.All(vol.Coerce(int), vol.Range(min=1, max=247)),
        vol.Required(
            CONF_SCAN_INTERVAL,
            default=d.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        ): vol.All(vol.Coerce(int), vol.Range(min=1, max=3600)),
    }


def _transport_schema(defaults: dict | None = None) -> vol.Schema:
    d = defaults or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_TRANSPORT, default=d.get(CONF_TRANSPORT, TRANSPORT_SERIAL)
            ): vol.In(TRANSPORTS),
        }
    )


def _esphome_tcp_schema(defaults: dict | None = None) -> vol.Schema:
    d = defaults or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_NAME, default=d.get(CONF_NAME, "Modbus via ESPHome")
            ): str,
            vol.Required(CONF_HOST, default=d.get(CONF_HOST, "")): str,
            vol.Required(
                CONF_TCP_PORT, default=d.get(CONF_TCP_PORT, DEFAULT_TCP_PORT)
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
            **_line_settings_schema(d),
            vol.Required(
                CONF_RESPONSE_TIMEOUT,
                default=d.get(CONF_RESPONSE_TIMEOUT, DEFAULT_RESPONSE_TIMEOUT_TCP),
            ): vol.All(vol.Coerce(float), vol.Range(min=0.1, max=30)),
            **_polling_schema(d),
        }
    )


def _esphome_api_schema(defaults: dict | None = None) -> vol.Schema:
    d = defaults or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_NAME, default=d.get(CONF_NAME, "Modbus via ESPHome API")
            ): str,
            vol.Required(CONF_HOST, default=d.get(CONF_HOST, "")): str,
            vol.Required(
                CONF_API_PORT, default=d.get(CONF_API_PORT, DEFAULT_API_PORT)
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
            vol.Optional(CONF_API_ENCRYPTION_KEY, default=""): str,
            vol.Optional(CONF_API_PASSWORD, default=""): str,
            vol.Required(
                CONF_ESPHOME_SERVICE,
                default=d.get(CONF_ESPHOME_SERVICE, DEFAULT_ESPHOME_SERVICE),
            ): str,
            vol.Required(
                CONF_ESPHOME_EVENT,
                default=d.get(CONF_ESPHOME_EVENT, DEFAULT_ESPHOME_EVENT),
            ): str,
            **_line_settings_schema(d),
            vol.Required(
                CONF_RESPONSE_TIMEOUT,
                default=d.get(CONF_RESPONSE_TIMEOUT, DEFAULT_RESPONSE_TIMEOUT_API),
            ): vol.All(vol.Coerce(float), vol.Range(min=0.1, max=30)),
            **_polling_schema(d),
        }
    )


def _hub_schema(defaults: dict | None = None) -> vol.Schema:
    d = defaults or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_NAME, default=d.get(CONF_NAME, "Modbus USB Controller")
            ): str,
            vol.Required(CONF_PORT, default=d.get(CONF_PORT, DEFAULT_PORT)): str,
            vol.Required(
                CONF_BAUDRATE, default=d.get(CONF_BAUDRATE, DEFAULT_BAUDRATE)
            ): vol.In(BAUDRATE_OPTIONS),
            vol.Required(
                CONF_BYTESIZE, default=d.get(CONF_BYTESIZE, DEFAULT_BYTESIZE)
            ): vol.In(BYTESIZE_OPTIONS),
            vol.Required(
                CONF_PARITY, default=d.get(CONF_PARITY, DEFAULT_PARITY)
            ): vol.In(list(PARITY_OPTIONS.keys())),
            vol.Required(
                CONF_STOPBITS, default=d.get(CONF_STOPBITS, DEFAULT_STOPBITS)
            ): vol.In(STOPBITS_OPTIONS),
            vol.Required(
                CONF_SLAVE_ID, default=d.get(CONF_SLAVE_ID, DEFAULT_SLAVE_ID)
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=247)),
            vol.Required(
                CONF_SCAN_INTERVAL,
                default=d.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=3600)),
        }
    )


class ModbusUsbConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the initial setup of the Modbus USB hub.

    v2.8.0: the first step picks the hub **transport** — a local serial
    adapter, an ESPHome device bridging RTU over TCP, or an ESPHome device
    driven through its native API — and the second step collects the
    transport-specific connection settings. Legacy callers that post serial
    fields straight to ``async_step_user`` keep working.
    """

    VERSION = 2

    def __init__(self) -> None:
        self._transport: str = TRANSPORT_SERIAL

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None and CONF_TRANSPORT not in user_input:
            # Backward compatibility: a direct serial submission (tests,
            # older automation) skips the transport picker.
            return await self.async_step_serial(user_input)
        if user_input is not None:
            self._transport = user_input[CONF_TRANSPORT]
            if self._transport == TRANSPORT_ESPHOME_TCP:
                return await self.async_step_esphome_tcp()
            if self._transport == TRANSPORT_ESPHOME_API:
                return await self.async_step_esphome_api()
            return await self.async_step_serial()
        return self.async_show_form(step_id="user", data_schema=_transport_schema())

    async def async_step_serial(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            # A serial adapter cannot safely be owned by two entries, even if
            # they specify different default slave IDs.
            self._async_abort_entries_match({CONF_PORT: user_input[CONF_PORT]})
            await self.async_set_unique_id(
                f"{user_input[CONF_PORT]}_{user_input[CONF_SLAVE_ID]}"
            )
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=user_input[CONF_NAME],
                data={
                    CONF_TRANSPORT: TRANSPORT_SERIAL,
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

        return self.async_show_form(
            step_id="serial", data_schema=_hub_schema(), errors=errors
        )

    async def _async_finish_esphome(
        self, transport: str, user_input: dict[str, Any], step_id: str, schema_fn
    ) -> ConfigFlowResult:
        """Shared tail of both ESPHome steps: dedupe, probe, create entry."""
        errors: dict[str, str] = {}
        probe: dict[str, Any] = {}
        data = {
            key: value
            for key, value in user_input.items()
            if key not in (CONF_NAME, CONF_SCAN_INTERVAL)
        }
        data[CONF_TRANSPORT] = transport
        data[CONF_HOST] = str(data.get(CONF_HOST, "")).strip()
        for secret in (CONF_API_ENCRYPTION_KEY, CONF_API_PASSWORD):
            if secret in data and not str(data[secret]).strip():
                data.pop(secret)
        if not data[CONF_HOST]:
            errors[CONF_HOST] = "invalid_host"
        else:
            # One ESPHome bridge serves exactly one hub: RS-485 is
            # request/response and the TCP stream server accepts one client.
            self._async_abort_entries_match(
                {CONF_TRANSPORT: transport, CONF_HOST: data[CONF_HOST]}
            )
            await self.async_set_unique_id(unique_id_for(data))
            self._abort_if_unique_id_configured()
            probe = await self.hass.async_add_executor_job(
                probe_connection, connection_config(data), self.hass
            )
            if not probe.get("reachable"):
                errors["base"] = str(probe.get("error_key") or "cannot_connect")
        if errors:
            return self.async_show_form(
                step_id=step_id,
                data_schema=schema_fn(user_input),
                errors=errors,
                description_placeholders={"detail": str(probe.get("error") or "")},
            )
        title = user_input[CONF_NAME]
        device_name = (probe.get("esphome") or {}).get("name")
        if device_name and device_name not in title:
            title = f"{title} ({device_name})"
        return self.async_create_entry(
            title=title,
            data=data,
            options={
                CONF_SCAN_INTERVAL: user_input[CONF_SCAN_INTERVAL],
                CONF_ENTITIES: [],
            },
        )

    async def async_step_esphome_tcp(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return await self._async_finish_esphome(
                TRANSPORT_ESPHOME_TCP, user_input, "esphome_tcp", _esphome_tcp_schema
            )
        return self.async_show_form(
            step_id="esphome_tcp",
            data_schema=_esphome_tcp_schema(),
            description_placeholders={"detail": ""},
        )

    async def async_step_esphome_api(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return await self._async_finish_esphome(
                TRANSPORT_ESPHOME_API, user_input, "esphome_api", _esphome_api_schema
            )
        return self.async_show_form(
            step_id="esphome_api",
            data_schema=_esphome_api_schema(),
            description_placeholders={"detail": ""},
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> ModbusUsbOptionsFlow:
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
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "settings",
                "capture_info",
                "add_entity",
                "manage_entities",
            ],
        )

    # ---------- Read-only traffic capture status ----------
    async def async_step_capture_info(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show whether this pymodbus release can capture raw TX/RX bytes.

        The Traffic Inspector needs a pymodbus transaction tracing hook to
        record real response frames. This read-only step tells users what
        their installation supports without changing any configuration.
        """
        if user_input is not None:
            return await self.async_step_init()

        hook: str | None = None
        try:
            coordinator = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id)
            hook = getattr(coordinator, "capture_hook", None)
        except Exception:  # pragma: no cover - defensive
            hook = None

        try:
            import pymodbus

            pymodbus_version = str(getattr(pymodbus, "__version__", "unknown"))
        except Exception:  # pragma: no cover - pymodbus is a hard requirement
            pymodbus_version = "unknown"

        if hook in ("trace_packet", "client_trace_packet"):
            details = (
                f"pymodbus {pymodbus_version} · hook: {hook}\n\n"
                "✅ Response capture is ACTIVE via transaction tracing: the "
                "Traffic Inspector records the real TX and RX bytes of every "
                "serial transaction, including the full multi-frame RX stream "
                "of batch operations."
            )
        elif hook == "logging":
            details = (
                f"pymodbus {pymodbus_version} · hook: logging\n\n"
                "⚠️ Response capture is ACTIVE via the pymodbus debug-log "
                "fallback: real RX bytes are parsed from pymodbus's frame "
                "dumps. This works, but depends on pymodbus log output and is "
                "slower than native transaction tracing."
            )
        else:
            details = (
                f"pymodbus {pymodbus_version} · hook: none\n\n"
                "❌ Response capture is UNAVAILABLE: this pymodbus release "
                "exposes no transaction tracing hook. The Traffic Inspector "
                "shows reconstructed request frames only — raw response bytes "
                "cannot be captured."
            )

        summary = describe_transport(connection_config(self.config_entry.data))
        details += (
            f"\n\nHub transport: {summary['label']}"
            f" · endpoint: {summary.get('endpoint') or 'n/a'}"
        )
        if summary["transport"] == TRANSPORT_ESPHOME_API:
            details += (
                "\nRX bytes are delivered by the ESPHome device's "
                f"`{summary['esphome_event']}` events and recorded by the API client."
            )

        return self.async_show_form(
            step_id="capture_info",
            data_schema=vol.Schema({}),
            description_placeholders={"capture_details": details},
        )

    # ---------- Global settings (scan interval) ----------
    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            new_options = dict(self.config_entry.options)
            new_options[CONF_SCAN_INTERVAL] = user_input[CONF_SCAN_INTERVAL]
            return self.async_create_entry(title="", data=new_options)

        current = self.config_entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
        )
        schema = vol.Schema(
            {
                vol.Required(CONF_SCAN_INTERVAL, default=current): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=3600)
                )
            }
        )
        return self.async_show_form(step_id="settings", data_schema=schema)

    # ---------- Add entity: choose type ----------
    async def async_step_add_entity(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
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
            e[
                CONF_ENTITY_ID
            ]: f"{e[CONF_NAME]} ({e[CONF_ENTITY_TYPE]}, addr {e[CONF_ADDRESS]})"
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
    async def async_step_edit_sensor(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        existing = None
        if self._editing_id:
            existing = next(
                (e for e in self._entities() if e[CONF_ENTITY_ID] == self._editing_id),
                None,
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
                    CONF_REGISTER_TYPE,
                    default=d.get(CONF_REGISTER_TYPE, REGISTER_TYPE_HOLDING),
                ): vol.In(REGISTER_TYPES_SENSOR),
                vol.Required(CONF_ADDRESS, default=d.get(CONF_ADDRESS, 0)): vol.All(
                    vol.Coerce(int), vol.Range(min=0, max=65535)
                ),
                vol.Required(
                    CONF_DATA_TYPE, default=d.get(CONF_DATA_TYPE, "uint16")
                ): vol.In(DATA_TYPES),
                vol.Optional(CONF_SCALE, default=d.get(CONF_SCALE, 1)): vol.Coerce(
                    float
                ),
                vol.Optional(
                    CONF_UNIT_OF_MEASUREMENT,
                    default=d.get(CONF_UNIT_OF_MEASUREMENT, ""),
                ): str,
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
    async def async_step_edit_switch(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        existing = None
        if self._editing_id:
            existing = next(
                (e for e in self._entities() if e[CONF_ENTITY_ID] == self._editing_id),
                None,
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
                    CONF_REGISTER_TYPE,
                    default=d.get(CONF_REGISTER_TYPE, REGISTER_TYPE_COIL),
                ): vol.In(REGISTER_TYPES_SWITCH),
                vol.Required(CONF_ADDRESS, default=d.get(CONF_ADDRESS, 0)): vol.All(
                    vol.Coerce(int), vol.Range(min=0, max=65535)
                ),
                vol.Optional(
                    CONF_ON_VALUE, default=d.get(CONF_ON_VALUE, 1)
                ): vol.Coerce(int),
                vol.Optional(
                    CONF_OFF_VALUE, default=d.get(CONF_OFF_VALUE, 0)
                ): vol.Coerce(int),
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
                (e for e in self._entities() if e[CONF_ENTITY_ID] == self._editing_id),
                None,
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
            "none",
            "motion",
            "door",
            "window",
            "smoke",
            "moisture",
            "connectivity",
            "power",
            "plug",
            "battery",
            "occupancy",
        ]
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=d.get(CONF_NAME, "")): str,
                vol.Required(
                    CONF_REGISTER_TYPE,
                    default=d.get(CONF_REGISTER_TYPE, REGISTER_TYPE_COIL),
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
                (e for e in self._entities() if e[CONF_ENTITY_ID] == self._editing_id),
                None,
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
                    CONF_REGISTER_TYPE,
                    default=d.get(CONF_REGISTER_TYPE, REGISTER_TYPE_HOLDING),
                ): vol.In([REGISTER_TYPE_HOLDING]),
                vol.Required(CONF_ADDRESS, default=d.get(CONF_ADDRESS, 0)): vol.All(
                    vol.Coerce(int), vol.Range(min=0, max=65535)
                ),
                vol.Required(
                    CONF_DATA_TYPE, default=d.get(CONF_DATA_TYPE, "uint16")
                ): vol.In(DATA_TYPES),
                vol.Optional(CONF_SCALE, default=d.get(CONF_SCALE, 1)): vol.Coerce(
                    float
                ),
                vol.Optional(
                    CONF_UNIT_OF_MEASUREMENT,
                    default=d.get(CONF_UNIT_OF_MEASUREMENT, ""),
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
