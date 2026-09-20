"""WebSocket commands for board-specific hardware tools."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv

from ..const import (
    BAUDRATE_OPTIONS,
    CONF_ADDRESS,
    CONF_BAUDRATE,
    CONF_DATA_TYPE,
    CONF_DEVICE_CONTROLS,
    CONF_DEVICE_ID,
    CONF_DEVICES,
    CONF_ENTITIES,
    CONF_M0_SHORT,
    CONF_MODEL,
    CONF_NAME,
    CONF_OFF_VALUE,
    CONF_ON_VALUE,
    CONF_PARITY,
    CONF_REGISTER_TYPE,
    CONF_SLAVE_ID,
    CONF_STATE_ON_VALUE,
    DATA_PRESERVE_SERIAL_RELOAD,
    DOMAIN,
    REGISTER_TYPE_COIL,
)
from ..templates import (
    async_load_templates,
)
from .helpers import _get_entry, _get_r4d6f20_device, _get_r413e16_device

_LOGGER = logging.getLogger(__name__)
_R4D6F20_BAUD_CODES = {
    1200: 0,
    2400: 1,
    4800: 2,
    9600: 3,
    19200: 4,
    38400: 5,
    57600: 6,
    115200: 7,
}


_R4D6F20_PARITY_CODES = {"N": 0, "O": 1, "E": 2}


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "modbus_usb/r413e16_command",
        vol.Required("entry_id"): cv.string,
        vol.Required("device_id"): cv.string,
        vol.Required("command"): vol.In(
            [
                "all_on",
                "all_off",
                "read_states",
                "configure",
                "channel_action",
                "factory_reset",
            ]
        ),
        # The R413E16 command guide assigns codes 0–4 to 1200–19200. Code 5 is
        # factory reset, so higher rates must never be offered for this board.
        vol.Optional("baudrate"): vol.In([1200, 2400, 4800, 9600, 19200]),
        vol.Optional("slave_id"): vol.All(vol.Coerce(int), vol.Range(min=1, max=247)),
        vol.Optional("channel"): vol.All(vol.Coerce(int), vol.Range(min=1, max=16)),
        vol.Optional("action"): vol.In(["toggle", "interlock", "momentary", "delay"]),
        vol.Optional("delay_seconds"): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=255)
        ),
    }
)
@websocket_api.async_response
async def ws_r413e16_command(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Run verified R413E16 group commands and serial-setup writes."""
    try:
        entry = _get_entry(hass, msg["entry_id"])
        device = _get_r413e16_device(entry, msg["device_id"])
        coordinator = hass.data[DOMAIN][entry.entry_id]
        current_slave = int(device.get(CONF_SLAVE_ID, entry.data.get(CONF_SLAVE_ID, 1)))
        command = msg["command"]

        if command == "read_states":
            # The confirmed R413E16 channel map returns 1 for ON and 0 for OFF.
            # Refresh the configured channel switches from this real board
            # feedback, rather than leaving an older accepted command cached.
            states: list[dict[str, Any]] = []
            for channel in range(1, 17):
                try:
                    value = await hass.async_add_executor_job(
                        coordinator.read_register_raw,
                        channel,
                        "holding",
                        "uint16",
                        current_slave,
                    )
                    states.append({"channel": channel, "value": value, "ok": True})
                except Exception as err:
                    states.append({"channel": channel, "ok": False, "error": str(err)})
            coordinator.set_r413e16_channel_states(
                device["id"],
                {
                    item["channel"]: int(item["value"]) in (1, 0x0100)
                    for item in states
                    if item["ok"]
                },
            )
            connection.send_result(
                msg["id"],
                {
                    "success": True,
                    "command": command,
                    "slave_id": current_slave,
                    "states": states,
                },
            )
            return

        if command == "factory_reset":
            # The vendor guide assigns baud register value 5 to factory reset.
            # It takes effect after the board is powered up again. The published
            # defaults are 9600 baud and slave ID 1, so keep HA in sync.
            await hass.async_add_executor_job(
                coordinator.write_register, 0x00FE, 5, current_slave
            )
            new_data = {**entry.data, CONF_BAUDRATE: 9600}
            new_options = dict(entry.options or {})
            new_options[CONF_DEVICES] = [
                {**item, CONF_SLAVE_ID: 1} if item.get("id") == device["id"] else item
                for item in new_options.get(CONF_DEVICES, [])
            ]
            new_options[CONF_ENTITIES] = [
                {**item, CONF_SLAVE_ID: 1}
                if item.get(CONF_DEVICE_ID) == device["id"]
                else item
                for item in new_options.get(CONF_ENTITIES, [])
            ]
            hass.config_entries.async_update_entry(
                entry, data=new_data, options=new_options
            )
            connection.send_result(
                msg["id"],
                {
                    "success": True,
                    "command": command,
                    "baudrate": 9600,
                    "slave_id": 1,
                    "power_cycle_required": True,
                },
            )
            return

        if command == "channel_action":
            channel = msg.get("channel")
            action = msg.get("action")
            if channel is None or action is None:
                raise ValueError("Choose a channel and an action")
            controls = device.get(CONF_DEVICE_CONTROLS, {})
            channel_actions = controls.get("channel_actions", {})
            values = {
                "toggle": 0x0300,
                "interlock": 0x0400,
                "momentary": 0x0500,
            }
            if action == "delay":
                delay_seconds = msg.get("delay_seconds")
                if delay_seconds is None:
                    raise ValueError("Enter a delay from 0 to 255 seconds")
                value = int(channel_actions.get("delay_base", 0x0600)) + delay_seconds
            else:
                value = int(channel_actions.get(action, values[action]))
            await hass.async_add_executor_job(
                coordinator.write_register, channel, value, current_slave
            )
            if action == "interlock":
                coordinator.set_r413e16_channel_states(
                    device["id"], {item: item == channel for item in range(1, 17)}
                )
            connection.send_result(
                msg["id"],
                {
                    "success": True,
                    "command": command,
                    "channel": channel,
                    "action": action,
                    "value": value,
                    "slave_id": current_slave,
                },
            )
            return

        if command in ("all_on", "all_off"):
            # Some R413E16 board revisions do not implement the optional
            # register-0 broadcast command consistently. Interlock and normal
            # channels prove the individual FC06 commands, so use those known
            # good writes for a reliable all-channel operation.
            value = 0x0100 if command == "all_on" else 0x0200
            for channel in range(1, 17):
                await hass.async_add_executor_job(
                    coordinator.write_register, channel, value, current_slave
                )
            coordinator.set_r413e16_channel_states(
                device["id"], {channel: command == "all_on" for channel in range(1, 17)}
            )
            connection.send_result(
                msg["id"],
                {
                    "success": True,
                    "command": command,
                    "slave_id": current_slave,
                    "channels_written": 16,
                    "value": value,
                },
            )
            return

        baudrate = msg.get("baudrate")
        new_slave = msg.get("slave_id")
        if baudrate is None and new_slave is None:
            raise ValueError("Choose a baud rate, a slave ID, or both")

        # Each write applies immediately. Change the address first, then send
        # the baud-rate command to the new address while the adapter still
        # uses the old rate.
        target_slave = current_slave
        if new_slave is not None:
            await hass.async_add_executor_job(
                coordinator.write_register, 0x00FF, new_slave, current_slave
            )
            target_slave = new_slave
        if baudrate is not None:
            baud_codes = {
                1200: 0,
                2400: 1,
                4800: 2,
                9600: 3,
                19200: 4,
            }
            await hass.async_add_executor_job(
                coordinator.write_register, 0x00FE, baud_codes[baudrate], target_slave
            )

        new_options = dict(entry.options or {})
        devices = [dict(item) for item in new_options.get(CONF_DEVICES, [])]
        if new_slave is not None:
            devices = [
                {**item, CONF_SLAVE_ID: new_slave}
                if item.get("id") == device["id"]
                else item
                for item in devices
            ]
            new_options[CONF_ENTITIES] = [
                {**item, CONF_SLAVE_ID: new_slave}
                if item.get(CONF_DEVICE_ID) == device["id"]
                else item
                for item in new_options.get(CONF_ENTITIES, [])
            ]
        new_options[CONF_DEVICES] = devices
        new_data = dict(entry.data)
        if baudrate is not None:
            new_data[CONF_BAUDRATE] = baudrate
        hass.config_entries.async_update_entry(
            entry, data=new_data, options=new_options
        )
        connection.send_result(
            msg["id"],
            {
                "success": True,
                "baudrate": baudrate,
                "slave_id": new_slave,
                "reload_required": baudrate is not None,
            },
        )
    except Exception as err:
        _LOGGER.warning("R413E16 command failed: %s", err)
        connection.send_error(msg["id"], "r413e16_command_failed", str(err))


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "modbus_usb/r4d6f20_command",
        vol.Required("entry_id"): cv.string,
        vol.Required("device_id"): cv.string,
        vol.Required("command"): vol.In(
            [
                "all_on",
                "all_off",
                "read_slave_id",
                "configure_serial",
                "channel_action",
                "factory_reset",
            ]
        ),
        vol.Optional("baudrate"): vol.In(BAUDRATE_OPTIONS),
        vol.Optional("parity"): vol.In(["N", "O", "E"]),
        vol.Optional("channel"): vol.All(vol.Coerce(int), vol.Range(min=0, max=19)),
        vol.Optional("action"): vol.In(["toggle", "interlock", "momentary", "delay"]),
        vol.Optional("delay_seconds"): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=255)
        ),
    }
)
@websocket_api.async_response
async def ws_r4d6f20_command(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Run documented R4D6F20 board controls for the selected M0 mode."""
    try:
        entry = _get_entry(hass, msg["entry_id"])
        device = _get_r4d6f20_device(entry, msg["device_id"])
        coordinator = hass.data[DOMAIN][entry.entry_id]
        slave = int(device.get(CONF_SLAVE_ID, entry.data.get(CONF_SLAVE_ID, 1)))
        command = msg["command"]
        if command in ("all_on", "all_off"):
            if device.get(CONF_M0_SHORT, False):
                # Command 2 maps relays to coils 0–19. Its manual does not
                # define Command 1's 0x0700/0x0800 all-relay register values.
                for channel in range(20):
                    await hass.async_add_executor_job(
                        coordinator.write_coil, channel, command == "all_on", slave
                    )
            else:
                await hass.async_add_executor_job(
                    coordinator.write_register,
                    0,
                    0x0700 if command == "all_on" else 0x0800,
                    slave,
                )
            await coordinator.async_request_refresh()
            connection.send_result(msg["id"], {"success": True, "command": command})
            return
        if command == "read_slave_id":
            value = await hass.async_add_executor_job(
                coordinator.read_register_raw, 0x00FD, "holding", "uint16", slave
            )
            connection.send_result(msg["id"], {"success": True, "slave_id": value})
            return
        if command == "channel_action":
            if device.get(CONF_M0_SHORT, False):
                raise ValueError(
                    "Command 2 has individual relay coils, not Command 1 action registers. "
                    "Use the CH switches for ON/OFF control."
                )
            channel, action = msg.get("channel"), msg.get("action")
            if channel is None or action is None:
                raise ValueError("Choose a relay channel and action")
            value = {"toggle": 0x0300, "interlock": 0x0400, "momentary": 0x0500}.get(
                action
            )
            if action == "delay":
                if msg.get("delay_seconds") is None:
                    raise ValueError("Enter a delay from 0 to 255 seconds")
                value = 0x0600 + msg["delay_seconds"]
            await hass.async_add_executor_job(
                coordinator.write_register, channel, value, slave
            )
            await coordinator.async_request_refresh()
            connection.send_result(msg["id"], {"success": True, "command": command})
            return
        if command == "factory_reset":
            await hass.async_add_executor_job(
                coordinator.write_register, 0x00FB, 0, slave
            )
            hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_BAUDRATE: 9600, CONF_PARITY: "N"}
            )
            connection.send_result(
                msg["id"], {"success": True, "reload_required": True}
            )
            return
        baudrate, parity = msg.get("baudrate"), msg.get("parity")
        if baudrate is None and parity is None:
            raise ValueError("Choose a baud rate, parity setting, or both")
        if baudrate is not None:
            await hass.async_add_executor_job(
                coordinator.write_register, 0x00FE, _R4D6F20_BAUD_CODES[baudrate], slave
            )
        if parity is not None:
            await hass.async_add_executor_job(
                coordinator.write_register, 0x00FF, _R4D6F20_PARITY_CODES[parity], slave
            )
        data = dict(entry.data)
        if baudrate is not None:
            data[CONF_BAUDRATE] = baudrate
        if parity is not None:
            data[CONF_PARITY] = parity
        hass.config_entries.async_update_entry(entry, data=data)
        connection.send_result(msg["id"], {"success": True, "reload_required": True})
    except Exception as err:
        _LOGGER.warning("R4D6F20 command failed: %s", err)
        connection.send_error(msg["id"], "r4d6f20_command_failed", str(err))


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "modbus_usb/r4d6f20_set_mode",
        vol.Required("entry_id"): cv.string,
        vol.Required("device_id"): cv.string,
        vol.Required("m0_short"): bool,
    }
)
@websocket_api.async_response
async def ws_r4d6f20_set_mode(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Install the R4D6F20 entity profile matching its physical M0 jumper."""
    try:
        entry = _get_entry(hass, msg["entry_id"])
        device = _get_r4d6f20_device(entry, msg["device_id"])
        mode_key = "command_2" if msg["m0_short"] else "command_1"
        controls = device.get(CONF_DEVICE_CONTROLS, {})
        mode = controls.get("command_modes", {}).get(mode_key, {})
        if not mode:
            # Existing boards can have older Command 1-only metadata. Upgrade
            # that metadata in place while retaining their name and entities.
            templates = await async_load_templates(hass)
            template = next(
                (
                    item
                    for item in templates
                    if str(item.get(CONF_MODEL, "")).lower() == "r4d6f20"
                    and item.get(CONF_DEVICE_CONTROLS, {}).get("command_modes")
                ),
                None,
            )
            if template is None:
                raise ValueError("The combined R4D6F20 template could not be found")
            controls = template[CONF_DEVICE_CONTROLS]
            mode = controls["command_modes"][mode_key]

        relay = mode["relay"]
        digital_input = mode["digital_input"]
        analog_input = mode.get(
            "analog_input", {"register_type": "holding", "address_start": 160}
        )
        new_entities = []
        for entity in entry.options.get(CONF_ENTITIES, []):
            if entity.get(CONF_DEVICE_ID) != device["id"]:
                new_entities.append(entity)
                continue
            updated = dict(entity)
            name = str(updated.get(CONF_NAME, ""))
            if (
                name.startswith("CH-")
                and name[3:].isdigit()
                and 1 <= int(name[3:]) <= 20
            ):
                updated[CONF_REGISTER_TYPE] = relay["register_type"]
                updated[CONF_ADDRESS] = int(relay["address_start"]) + int(name[3:]) - 1
                if relay["register_type"] == REGISTER_TYPE_COIL:
                    updated[CONF_DATA_TYPE] = "bool"
                    updated.pop(CONF_ON_VALUE, None)
                    updated.pop(CONF_OFF_VALUE, None)
                    updated.pop(CONF_STATE_ON_VALUE, None)
                else:
                    updated[CONF_DATA_TYPE] = "uint16"
                    updated[CONF_ON_VALUE] = int(relay["on_value"])
                    updated[CONF_OFF_VALUE] = int(relay["off_value"])
                    updated[CONF_STATE_ON_VALUE] = 1
            elif name in ("DI-01", "DI-02"):
                updated[CONF_REGISTER_TYPE] = digital_input["register_type"]
                updated[CONF_ADDRESS] = (
                    int(digital_input["address_start"]) + int(name[-2:]) - 1
                )
                updated[CONF_DATA_TYPE] = (
                    "bool" if digital_input["register_type"] != "holding" else "uint16"
                )
            elif name in ("Current Input", "Voltage Input"):
                updated[CONF_REGISTER_TYPE] = analog_input["register_type"]
                updated[CONF_ADDRESS] = int(analog_input["address_start"]) + (
                    0 if name == "Current Input" else 1
                )
                updated[CONF_DATA_TYPE] = "uint16"
            new_entities.append(updated)

        new_options = dict(entry.options or {})
        new_options[CONF_DEVICES] = [
            {**item, CONF_DEVICE_CONTROLS: controls, CONF_M0_SHORT: msg["m0_short"]}
            if item.get("id") == device["id"]
            else item
            for item in entry.options.get(CONF_DEVICES, [])
        ]
        new_options[CONF_ENTITIES] = new_entities
        hass.data.setdefault(DOMAIN, {}).setdefault(
            DATA_PRESERVE_SERIAL_RELOAD, set()
        ).add(entry.entry_id)
        hass.config_entries.async_update_entry(entry, options=new_options)
        connection.send_result(msg["id"], {"success": True, "mode": mode_key})
    except Exception as err:
        _LOGGER.warning("R4D6F20 mode switch failed: %s", err)
        connection.send_error(msg["id"], "r4d6f20_set_mode_failed", str(err))
