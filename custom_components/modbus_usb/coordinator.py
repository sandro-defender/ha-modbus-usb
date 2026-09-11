"""Data update coordinator for the Modbus USB Controller integration."""
from __future__ import annotations

import logging
import struct
from collections import deque
from datetime import datetime, timedelta
from threading import Lock
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_ADDRESS,
    CONF_ASSUMED_STATE,
    CONF_BAUDRATE,
    CONF_BYTESIZE,
    CONF_DATA_TYPE,
    CONF_DEVICES,
    CONF_DEVICE_ID,
    CONF_ENTITIES,
    CONF_ENTITY_TYPE,
    CONF_IMAGE,
    CONF_MANUFACTURER,
    CONF_MODEL,
    CONF_NAME,
    CONF_PARITY,
    CONF_PORT,
    CONF_REGISTER_TYPE,
    CONF_SCALE,
    CONF_SLAVE_ID,
    CONF_STOPBITS,
    DATA_TYPE_FLOAT32,
    DATA_TYPE_INT16,
    DATA_TYPE_INT32,
    DATA_TYPE_UINT16,
    DATA_TYPE_UINT32,
    DATA_TYPE_WORD_COUNT,
    DIAG_CONSECUTIVE_FAILURES,
    DIAG_FAILED_READS,
    DIAG_LAST_ERROR,
    DIAG_LAST_SUCCESS,
    DIAG_TOTAL_READS,
    DOMAIN,
    REGISTER_TYPE_COIL,
    REGISTER_TYPE_DISCRETE,
    REGISTER_TYPE_HOLDING,
    REGISTER_TYPE_INPUT,
)

_LOGGER = logging.getLogger(__name__)


def _decode_words(words: list[int], data_type: str) -> float | int:
    """Decode a list of 16-bit register words into a number."""
    if data_type == DATA_TYPE_UINT16:
        return words[0]
    if data_type == DATA_TYPE_INT16:
        val = words[0]
        return val - 0x10000 if val >= 0x8000 else val
    # 32-bit types: big-endian word order (high word first)
    raw = struct.pack(">HH", words[0], words[1])
    if data_type == DATA_TYPE_UINT32:
        return struct.unpack(">I", raw)[0]
    if data_type == DATA_TYPE_INT32:
        return struct.unpack(">i", raw)[0]
    if data_type == DATA_TYPE_FLOAT32:
        return struct.unpack(">f", raw)[0]
    return words[0]


def is_r413e16_switch_config(entity: dict[str, Any]) -> bool:
    """Return whether a config uses the tested R413E16 register map.

    Older panel versions could save numeric settings as JSON strings. Normalize
    them here so existing configured boards do not need to be recreated.
    """
    try:
        return (
            entity.get(CONF_REGISTER_TYPE) == REGISTER_TYPE_HOLDING
            and int(entity.get("on_value", 0)) == 0x0100
            and int(entity.get("off_value", 0)) == 0x0200
        )
    except (TypeError, ValueError):
        return False


def get_device_info(entry: ConfigEntry, ent: dict) -> DeviceInfo:
    """Return DeviceInfo for an entity, linking it to a separated device or the hub."""
    device_id = ent.get(CONF_DEVICE_ID)
    if device_id:
        devices = entry.options.get(CONF_DEVICES, [])
        device = next((d for d in devices if str(d.get("id")) == str(device_id)), None)
        if device:
            return DeviceInfo(
                identifiers={(DOMAIN, f"{entry.entry_id}_{device_id}")},
                name=device.get(CONF_NAME, f"Device {device_id}"),
                manufacturer=device.get(CONF_MANUFACTURER, "Modbus USB"),
                model=device.get(CONF_MODEL, "Modbus Device"),
                via_device=(DOMAIN, entry.entry_id),
            )
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer="Modbus USB",
        model="Modbus Serial Hub",
    )


def get_entity_picture(entry: ConfigEntry, ent: dict) -> str | None:
    """Return a device/template image URL for HA entity_picture, if set."""
    picture = ent.get(CONF_IMAGE)
    device_id = ent.get(CONF_DEVICE_ID)
    if device_id:
        devices = entry.options.get(CONF_DEVICES, [])
        device = next((d for d in devices if str(d.get("id")) == str(device_id)), None)
        if device and device.get(CONF_IMAGE):
            picture = device.get(CONF_IMAGE)
    return picture or None


class ModbusUsbCoordinator(DataUpdateCoordinator):
    """Polls the USB-connected Modbus controller and shares results with entities."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: Any,
        slave_id: int,
        scan_interval: int,
        entry_id: str,
        serial_config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="Modbus USB Controller",
            update_interval=timedelta(seconds=scan_interval),
            # R413E16 reports are also kept in a verified-state cache.  A
            # manual read can change that cache while the raw values in the
            # coordinator dictionary compare equal to the previous snapshot.
            # Always notify listeners so HA switch entities still recalculate
            # their state from the confirmed board response.
            always_update=True,
        )
        self.client = client
        self.slave_id = slave_id
        self.entry_id = entry_id
        self.serial_config = serial_config or {}
        # entities list is read fresh from config_entry.options each refresh so
        # the options flow (add/edit/remove) takes effect without reload in most cases
        self.hass = hass
        # Health / diagnostics tracking
        self.diag: dict[str, Any] = {
            DIAG_TOTAL_READS: 0,
            DIAG_FAILED_READS: 0,
            DIAG_CONSECUTIVE_FAILURES: 0,
            DIAG_LAST_ERROR: None,
            DIAG_LAST_SUCCESS: None,
        }
        # A concise rolling wire-level activity history for the sidebar.  Pymodbus
        # does not retain raw RTU bytes reliably across serial implementations, so
        # log every decoded Modbus request and its response instead.
        self.transaction_log: deque[dict[str, Any]] = deque(maxlen=200)
        self.scan_progress: dict[str, Any] = {
            "active": False,
            "completed": 0,
            "total": 0,
            "found": 0,
        }
        # Keep a last confirmed command only as a short-lived fallback when a
        # channel read fails. R413E16 holding-register feedback is otherwise
        # authoritative and refreshes this cache on every successful poll.
        self._command_states: dict[str, bool] = {}
        # Loaded switch entities register after Home Assistant has attached
        # them. This lets a successful Combined Switch command immediately
        # publish only its affected real channel entities to HA.
        self._switch_entities: set[Any] = set()
        self._r413e16_refresh_scheduled = False
        # Modbus RTU is request/response based: concurrent access to one serial
        # adapter can pair a response with the wrong request. Every I/O operation
        # must therefore own this lock for its entire transaction.
        self._serial_lock = Lock()

    def get_command_state(self, entity_id: str) -> bool | None:
        """Return the last known state when a board read is temporarily unavailable."""
        return self._command_states.get(entity_id)

    def r413e16_state_signal(self) -> str:
        """Return this hub's private signal for verified R413E16 feedback."""
        return f"{DOMAIN}_{self.entry_id}_r413e16_state"

    def register_switch_entity(self, switch_entity: Any) -> None:
        """Register a loaded switch entity for targeted HA state publication."""
        self._switch_entities.add(switch_entity)

    def unregister_switch_entity(self, switch_entity: Any) -> None:
        """Remove a switch entity that Home Assistant has unloaded."""
        self._switch_entities.discard(switch_entity)

    def _publish_r413e16_channel_entities(
        self, device_id: str, channel_states: dict[int, bool]
    ) -> None:
        """Ask HA to write only the R413E16 channels changed by this command."""
        for switch_entity in tuple(self._switch_entities):
            config = getattr(switch_entity, "_ent", {})
            if (
                str(config.get(CONF_DEVICE_ID)) != str(device_id)
                or config.get(CONF_ASSUMED_STATE)
            ):
                continue
            try:
                address = int(config[CONF_ADDRESS])
            except (KeyError, TypeError, ValueError):
                continue
            if address in channel_states:
                switch_entity.async_write_ha_state()
                # Coordinator listeners can be delayed or coalesced by HA.
                # Set the exact loaded entity ID as well, so a successful
                # Combined Switch command immediately changes the selected
                # child entities in HA. The scheduled full read below remains
                # the authoritative reconciliation with the physical board.
                entity_id = getattr(switch_entity, "entity_id", None)
                if entity_id:
                    current = self.hass.states.get(entity_id)
                    attributes = dict(current.attributes) if current else {}
                    self.hass.states.async_set(
                        entity_id,
                        STATE_ON if channel_states[address] else STATE_OFF,
                        attributes,
                        force_update=True,
                    )

    def _schedule_r413e16_full_refresh(self) -> None:
        """Read every configured entity after an R413E16 switch command."""
        if self._r413e16_refresh_scheduled:
            return
        self._r413e16_refresh_scheduled = True

        async def _refresh() -> None:
            try:
                # Do not trust an accepted FC06 write as output feedback. Read
                # all configured registers so HA reflects the board's actual
                # state, including every channel controlled by a group switch.
                await self.async_request_refresh()
            finally:
                self._r413e16_refresh_scheduled = False

        self.hass.async_create_task(_refresh())

    def get_r413e16_group_state(
        self, device_id: str, addresses: list[int]
    ) -> bool | None:
        """Return an R413E16 group state derived from its real channel states.

        A Combined Switch has no state register of its own. Its state is ON
        only when every selected normal channel is confirmed ON. Return None
        until all selected channels have a usable state, so generic assumed
        switches retain their normal fallback behaviour.
        """
        channel_entities: dict[int, str] = {}
        for entity in self._get_entities():
            if (
                str(entity.get(CONF_DEVICE_ID)) != str(device_id)
                or entity.get(CONF_ENTITY_TYPE) != "switch"
                or entity.get(CONF_ASSUMED_STATE)
                or not is_r413e16_switch_config(entity)
            ):
                continue
            try:
                channel_entities[int(entity[CONF_ADDRESS])] = str(entity["id"])
            except (KeyError, TypeError, ValueError):
                continue
        states: list[bool] = []
        for address in addresses:
            entity_id = channel_entities.get(int(address))
            state = self._command_states.get(entity_id) if entity_id else None
            if state is None:
                return None
            states.append(state)
        return all(states) if states else None

    def set_r413e16_channel_states(
        self, device_id: str, channel_states: dict[int, bool]
    ) -> None:
        """Publish confirmed R413E16 command states to matching channel switches."""
        updates: dict[str, bool] = {}
        for entity in self._get_entities():
            if (
                str(entity.get(CONF_DEVICE_ID)) != str(device_id)
                or entity.get(CONF_ENTITY_TYPE) != "switch"
                or not is_r413e16_switch_config(entity)
            ):
                continue
            try:
                channel = int(entity[CONF_ADDRESS])
            except (KeyError, TypeError, ValueError):
                continue
            if channel in channel_states:
                updates[str(entity["id"])] = channel_states[channel]
        if updates:
            self._command_states.update(updates)
            # Publish an updated coordinator snapshot as well as the fallback
            # cache. This guarantees that HA entities receive the new state
            # immediately after an on-demand channel read or group command.
            updated_data = dict(self.data or {})
            updated_data.update({
                entity_id: 1 if state else 0
                for entity_id, state in updates.items()
            })
            self.async_set_updated_data(updated_data)
            self._publish_r413e16_channel_entities(device_id, channel_states)
            self._schedule_r413e16_full_refresh()
            # A coordinator refresh is sufficient for regular polling. An
            # on-demand board read, however, must also wake each loaded HA
            # switch immediately. The dispatcher reaches the actual entity
            # instances instead of relying on a browser refresh or a direct
            # write to HA's global state machine.
            async_dispatcher_send(
                self.hass,
                self.r413e16_state_signal(),
                str(device_id),
            )

    def _ensure_connected(self) -> None:
        """Open the serial adapter or raise a useful error before a request."""
        if self.client.connected:
            return
        if not self.client.connect():
            raise UpdateFailed("Could not open the configured serial port")

    @staticmethod
    def _call_modbus_on_client(
        client: Any, method_name: str, *args: Any, slave: int, **kwargs: Any
    ) -> Any:
        """Call Pymodbus using its current or legacy unit-ID keyword.

        Pymodbus 3.8+ renamed ``slave`` to ``device_id``.  Supporting both
        keeps the integration working with the manifest's older supported
        versions as well as current Home Assistant installations.
        """
        method = getattr(client, method_name)
        try:
            return method(*args, device_id=slave, **kwargs)
        except TypeError as err:
            if "device_id" not in str(err):
                raise
            return method(*args, slave=slave, **kwargs)

    def _call_modbus(
        self, method_name: str, *args: Any, slave: int, **kwargs: Any
    ) -> Any:
        """Call a method on this hub's configured serial client."""
        return self._call_modbus_on_client(
            self.client, method_name, *args, slave=slave, **kwargs
        )

    def scan_bus(
        self, baudrates: list[int], start_slave: int = 1, end_slave: int = 20,
        templates: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Probe a bounded RS-485 range and report devices that answer.

        The primary client is temporarily closed while an isolated, short-timeout
        client tests each baud rate. Any valid Modbus exception response still
        counts as a detected device: it proves the slave and serial settings.
        """
        from pymodbus.client import ModbusSerialClient

        start_slave = max(1, min(247, int(start_slave)))
        end_slave = max(start_slave, min(247, int(end_slave)))
        valid_bauds = sorted({int(rate) for rate in baudrates if int(rate) > 0})
        if not valid_bauds:
            raise ValueError("Select at least one baud rate to scan")

        found: list[dict[str, Any]] = []
        probed = 0
        total = len(valid_bauds) * (end_slave - start_slave + 1)
        self.scan_progress = {
            "active": True,
            "completed": 0,
            "total": total,
            "found": 0,
        }
        with self._serial_lock:
            self.client.close()
            try:
                for baudrate in valid_bauds:
                    probe = ModbusSerialClient(
                        port=self.serial_config[CONF_PORT],
                        baudrate=baudrate,
                        bytesize=self.serial_config[CONF_BYTESIZE],
                        parity=self.serial_config[CONF_PARITY],
                        stopbits=self.serial_config[CONF_STOPBITS],
                        timeout=0.2,
                        retries=0,
                    )
                    try:
                        if not probe.connect():
                            continue
                        for slave in range(start_slave, end_slave + 1):
                            probed += 1
                            self.scan_progress["completed"] = probed
                            try:
                                result = self._call_modbus_on_client(
                                    probe, "read_holding_registers", 0,
                                    count=1, slave=slave,
                                )
                                message = str(result)
                                no_response = "no response received" in message.lower()
                                if not no_response:
                                    response_kind = "register response" if not result.isError() else "exception response"
                                    suggestions = self._match_templates(
                                        probe, slave, templates or []
                                    )
                                    found.append({
                                        "slave_id": slave,
                                        "baudrate": baudrate,
                                        "response": response_kind,
                                        "suggestions": suggestions,
                                    })
                                    self.scan_progress["found"] = len(found)
                                    self._record_transaction(
                                        "scan_found", slave=slave, address=0,
                                        result=f"{baudrate} baud — {response_kind}",
                                    )
                            except Exception:  # A timeout is expected for unused IDs.
                                continue
                    finally:
                        probe.close()
            finally:
                self.client.connect()
                self.scan_progress["active"] = False
                self.scan_progress["completed"] = probed
        return {"found": found, "probed": probed, "start_slave": start_slave, "end_slave": end_slave}

    def _match_templates(
        self, client: Any, slave: int, templates: list[dict[str, Any]]
    ) -> list[str]:
        """Return templates whose optional read-only fingerprints all match."""
        matches: list[str] = []
        for template in templates:
            fingerprint = template.get("fingerprint") or []
            if not fingerprint:
                continue
            try:
                for probe in fingerprint:
                    register_type = probe[CONF_REGISTER_TYPE]
                    address = int(probe[CONF_ADDRESS])
                    data_type = probe.get(CONF_DATA_TYPE, DATA_TYPE_UINT16)
                    count = DATA_TYPE_WORD_COUNT.get(data_type, 1)
                    if register_type == REGISTER_TYPE_HOLDING:
                        result = self._call_modbus_on_client(
                            client, "read_holding_registers", address, count=count, slave=slave
                        )
                    elif register_type == REGISTER_TYPE_INPUT:
                        result = self._call_modbus_on_client(
                            client, "read_input_registers", address, count=count, slave=slave
                        )
                    else:
                        raise ValueError("Fingerprint register type must be holding or input")
                    if result.isError():
                        raise UpdateFailed(str(result))
                    value = _decode_words(result.registers, data_type)
                    if not float(probe["min_value"]) <= value <= float(probe["max_value"]):
                        raise ValueError("Value outside expected range")
                matches.append(template.get("name", template.get("id", "Unknown template")))
            except Exception:  # A mismatch is normal; do not present a weak match.
                continue
        return matches

    def _record_transaction(
        self,
        operation: str,
        *,
        slave: int,
        address: int,
        count: int = 1,
        value: Any = None,
        result: Any = None,
        error: Exception | str | None = None,
        duration_ms: float | None = None,
    ) -> None:
        """Keep a rolling, UI-safe record of every RS-485 operation."""
        item: dict[str, Any] = {
            "timestamp": datetime.now().astimezone().isoformat(),
            "operation": operation,
            "slave": slave,
            "address": address,
            "count": count,
            "value": value,
            "result": result,
            "status": "error" if error else "ok",
        }
        if duration_ms is not None:
            item["duration_ms"] = round(duration_ms, 1)
        if error:
            item["error"] = str(error)
        self.transaction_log.appendleft(item)
        if error:
            _LOGGER.warning(
                "RS-485 %s failed: slave=%s address=%s error=%s",
                operation, slave, address, error,
            )
        else:
            _LOGGER.debug(
                "RS-485 %s: slave=%s address=%s value=%s result=%s duration=%.1fms",
                operation, slave, address, value, result, duration_ms or 0,
            )

    def get_diagnostics(self) -> dict[str, Any]:
        """Return a serial-health snapshot and rolling RS-485 transaction log."""
        return {
            "connected": bool(getattr(self.client, "connected", False)),
            "entry_id": self.entry_id,
            "default_slave_id": self.slave_id,
            "health": dict(self.diag),
            "transactions": list(self.transaction_log),
            "scan": dict(self.scan_progress),
        }

    def clear_transaction_log(self) -> None:
        """Clear only the rolling activity log; preserve health counters."""
        self.transaction_log.clear()

    def close(self) -> None:
        """Close the serial client only after any in-flight transaction finishes."""
        with self._serial_lock:
            self.client.close()

    def _get_entities(self) -> list[dict]:
        entry = self.hass.config_entries.async_get_entry(self.entry_id)
        if entry is None:
            return []
        return entry.options.get(CONF_ENTITIES, [])

    def _get_device_slave_map(self) -> dict[str, int]:
        entry = self.hass.config_entries.async_get_entry(self.entry_id)
        if entry is None:
            return {}
        devices = entry.options.get(CONF_DEVICES, [])
        return {
            str(d.get("id")): int(d.get(CONF_SLAVE_ID, self.slave_id))
            for d in devices
            if "id" in d and d.get(CONF_SLAVE_ID) is not None
        }

    async def _async_update_data(self) -> dict[str, Any]:
        entities = self._get_entities()
        if not entities:
            return {}
        try:
            device_slave_map = self._get_device_slave_map()
            return await self.hass.async_add_executor_job(
                self._read_all, entities, device_slave_map
            )
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(f"Error communicating with Modbus device: {err}") from err

    def _read_all(
        self, entities: list[dict], device_slave_map: dict[str, int] | None = None
    ) -> dict[str, Any]:
        device_slave_map = device_slave_map or {}
        data: dict[str, Any] = {}
        for ent in entities:
            ent_id = ent["id"]
            if ent.get(CONF_ASSUMED_STATE):
                continue
            self.diag[DIAG_TOTAL_READS] += 1
            try:
                value = self._read_one(ent, device_slave_map)
                data[ent_id] = value
                # Tested R413E16 boards report 1 for ON and 0 for OFF from
                # holding registers 1–16. Keep the fallback synchronized with
                # this real feedback so manual/external changes show in HA.
                if (
                    ent.get(CONF_ENTITY_TYPE) == "switch"
                    and is_r413e16_switch_config(ent)
                ):
                    self._command_states[ent_id] = value in (1, 0x0100)
                self.diag[DIAG_CONSECUTIVE_FAILURES] = 0
                self.diag[DIAG_LAST_SUCCESS] = datetime.now().isoformat()
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Failed reading %s: %s", ent.get("name", ent_id), err)
                data[ent_id] = None
                self.diag[DIAG_FAILED_READS] += 1
                self.diag[DIAG_CONSECUTIVE_FAILURES] += 1
                self.diag[DIAG_LAST_ERROR] = str(err)
        return data

    def _read_one(self, ent: dict, device_slave_map: dict[str, int] | None = None) -> Any:
        register_type = ent[CONF_REGISTER_TYPE]
        address = ent[CONF_ADDRESS]

        # Resolve slave ID: entity -> device -> hub default
        slave = ent.get(CONF_SLAVE_ID)
        if slave is None and device_slave_map and ent.get(CONF_DEVICE_ID):
            slave = device_slave_map.get(str(ent.get(CONF_DEVICE_ID)))
        if slave is None:
            slave = self.slave_id
        target_slave = int(slave)

        started = datetime.now()
        operation = f"read_{register_type}"
        count = 1
        try:
            with self._serial_lock:
                self._ensure_connected()
                if register_type == REGISTER_TYPE_COIL:
                    result = self._call_modbus(
                        "read_coils", address, count=count, slave=target_slave
                    )
                    if result.isError():
                        raise UpdateFailed(str(result))
                    value = bool(result.bits[0])
                elif register_type == REGISTER_TYPE_DISCRETE:
                    result = self._call_modbus(
                        "read_discrete_inputs", address, count=count, slave=target_slave
                    )
                    if result.isError():
                        raise UpdateFailed(str(result))
                    value = bool(result.bits[0])
                else:
                    data_type = ent.get(CONF_DATA_TYPE, DATA_TYPE_UINT16)
                    count = DATA_TYPE_WORD_COUNT.get(data_type, 1)
                    if register_type == REGISTER_TYPE_HOLDING:
                        result = self._call_modbus(
                            "read_holding_registers", address, count=count, slave=target_slave
                        )
                    elif register_type == REGISTER_TYPE_INPUT:
                        result = self._call_modbus(
                            "read_input_registers", address, count=count, slave=target_slave
                        )
                    else:
                        raise UpdateFailed(f"Unknown register type: {register_type}")
                    if result.isError():
                        raise UpdateFailed(str(result))
                    value = _decode_words(result.registers, data_type)
                    scale = ent.get(CONF_SCALE, 1)
                    if scale not in (1, None):
                        value = value * scale
            self._record_transaction(
                operation, slave=target_slave, address=address, count=count, result=value,
                duration_ms=(datetime.now() - started).total_seconds() * 1000,
            )
            return value
        except Exception as err:
            self._record_transaction(
                operation, slave=target_slave, address=address, count=count, error=err,
                duration_ms=(datetime.now() - started).total_seconds() * 1000,
            )
            raise

    def read_entity_value(self, entity: dict[str, Any], slave: int) -> Any:
        """Read one configured entity for a user-requested device test."""
        test_entity = dict(entity)
        test_entity[CONF_SLAVE_ID] = slave
        return self._read_one(test_entity)

    def write_coil(self, address: int, value: bool, slave: int | None = None) -> None:
        """Write a coil value (used by switches). Runs synchronously - call via executor."""
        target_slave = int(slave if slave is not None else self.slave_id)
        started = datetime.now()
        try:
            with self._serial_lock:
                self._ensure_connected()
                result = self._call_modbus("write_coil", address, value, slave=target_slave)
                if result.isError():
                    raise UpdateFailed(str(result))
            self._record_transaction("write_coil", slave=target_slave, address=address, value=value,
                                     result="accepted", duration_ms=(datetime.now() - started).total_seconds() * 1000)
        except Exception as err:
            self._record_transaction("write_coil", slave=target_slave, address=address, value=value, error=err,
                                     duration_ms=(datetime.now() - started).total_seconds() * 1000)
            raise

    def write_register(self, address: int, value: int, slave: int | None = None) -> None:
        """Write a single holding register (used by switches modeled as registers)."""
        target_slave = int(slave if slave is not None else self.slave_id)
        started = datetime.now()
        try:
            with self._serial_lock:
                self._ensure_connected()
                result = self._call_modbus(
                    "write_register", address, value, slave=target_slave
                )
                if result.isError():
                    raise UpdateFailed(str(result))
            self._record_transaction("write_holding", slave=target_slave, address=address, value=value,
                                     result="accepted", duration_ms=(datetime.now() - started).total_seconds() * 1000)
        except Exception as err:
            self._record_transaction("write_holding", slave=target_slave, address=address, value=value, error=err,
                                     duration_ms=(datetime.now() - started).total_seconds() * 1000)
            raise

    def write_registers_32bit(
        self, address: int, value: int, data_type: str, slave: int | None = None
    ) -> None:
        """Write two consecutive holding registers for 32-bit data types."""
        target_slave = int(slave if slave is not None else self.slave_id)
        if data_type in ("int32",):
            raw = struct.pack(">i", value)
        elif data_type in ("float32",):
            raw = struct.pack(">f", value)
        else:  # uint32
            raw = struct.pack(">I", value)
        high, low = struct.unpack(">HH", raw)
        started = datetime.now()
        try:
            with self._serial_lock:
                self._ensure_connected()
                result = self._call_modbus(
                    "write_registers", address, [high, low], slave=target_slave
                )
                if result.isError():
                    raise UpdateFailed(str(result))
            self._record_transaction("write_holding_32bit", slave=target_slave, address=address,
                                     count=2, value=value, result="accepted",
                                     duration_ms=(datetime.now() - started).total_seconds() * 1000)
        except Exception as err:
            self._record_transaction("write_holding_32bit", slave=target_slave, address=address,
                                     count=2, value=value, error=err,
                                     duration_ms=(datetime.now() - started).total_seconds() * 1000)
            raise

    def read_register_raw(
        self, address: int, register_type: str, data_type: str, slave: int | None = None
    ) -> Any:
        """Perform a one-shot read of a register for the diagnostics/service call."""
        self.diag[DIAG_TOTAL_READS] += 1
        try:
            value = self._read_one({
                CONF_REGISTER_TYPE: register_type,
                CONF_ADDRESS: address,
                CONF_DATA_TYPE: data_type,
                CONF_SLAVE_ID: slave if slave is not None else self.slave_id,
            })
            self.diag[DIAG_CONSECUTIVE_FAILURES] = 0
            self.diag[DIAG_LAST_SUCCESS] = datetime.now().isoformat()
            return value
        except Exception as err:
            self.diag[DIAG_FAILED_READS] += 1
            self.diag[DIAG_CONSECUTIVE_FAILURES] += 1
            self.diag[DIAG_LAST_ERROR] = str(err)
            raise


# Changelog:
# 2026-09-08 — Support both Pymodbus device_id and legacy slave keywords.
# Date modified: 2026-09-08

