"""Data update coordinator for the Modbus USB Controller integration."""

from __future__ import annotations

import asyncio
import logging
import struct
import threading
import time
from collections import deque
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from threading import Lock
from time import sleep
from typing import Any

from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .boards import select_block_reader
from .boards.r413e16 import R413E16_STATE_ON_VALUES, is_r413e16_switch_config
from .bus import call_modbus_on_client
from .capture import ResponseCapture, install_response_capture
from .circuit_breaker import SlaveCircuitBreaker
from .const import (
    CONF_ADDRESS,
    CONF_ASSUMED_STATE,
    CONF_BAUDRATE,
    CONF_BYTESIZE,
    CONF_DATA_TYPE,
    CONF_DEVICE_ID,
    CONF_DEVICES,
    CONF_ENABLED,
    CONF_ENTITIES,
    CONF_ENTITY_ID,
    CONF_ENTITY_TYPE,
    CONF_NAME,
    CONF_PARITY,
    CONF_PORT,
    CONF_REGISTER_TYPE,
    CONF_SCALE,
    CONF_SLAVE_ID,
    CONF_STOPBITS,
    DATA_TYPE_INT16,
    DATA_TYPE_UINT16,
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
from .decoding import as_float, decode_words
from .diagnostics import diagnostic_request_frame, modbus_crc16
from .optimizer import group_entities_into_blocks
from .transport import (
    build_client,
    connection_error_message,
    describe_transport,
    is_serial,
)

_LOGGER = logging.getLogger(__name__)
# Compatibility alias for callers of the former coordinator-local helper.
_decode_words = decode_words


def traffic_signal(entry_id: str) -> str:
    """Return the dispatcher signal carrying live traffic for one hub.

    Every transaction the coordinator records is announced on this signal so
    the Traffic Inspector WebSocket subscription can push it to open panels.
    """
    return f"{DOMAIN}_{entry_id}_traffic"


class ModbusUsbCoordinator(DataUpdateCoordinator):
    """Polls the USB-connected Modbus controller and shares results with entities."""

    _call_modbus_on_client = staticmethod(call_modbus_on_client)

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
        # Raw TX/RX byte capture for the Traffic Inspector. When pymodbus
        # transaction tracing is available, every transaction records the real
        # response frame from the wire (and the real request bytes); without
        # tracing the log keeps only reconstructed requests.
        self.response_capture = ResponseCapture()
        self.capture_hook: str | None = None
        self._install_response_capture()
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
        self.circuit_breaker = SlaveCircuitBreaker()
        self._entity_last_poll: dict[str, float] = {}
        # Loaded switch entities register after Home Assistant has attached
        # them. This lets a successful Combined Switch command immediately
        # publish only its affected real channel entities to HA.
        self._switch_entities: set[Any] = set()
        self._r413e16_refresh_scheduled = False
        # Modbus RTU is request/response based: concurrent access to one serial
        # adapter can pair a response with the wrong request. Every I/O operation
        # must therefore own this lock for its entire transaction.
        self._serial_lock = Lock()
        # Per-thread latency waterfall stages for the Traffic Inspector. The
        # active serial transaction fills this in; the next _record_transaction
        # call consumes it so failed I/O keeps its timing as well.
        self._tx_stages = threading.local()
        # Temporary scan-interval boost state (modbus_usb.boost_polling).
        self._boost_original_interval: timedelta | None = None
        self._boost_interval: timedelta | None = None
        self._boost_until: datetime | None = None

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
            if str(config.get(CONF_DEVICE_ID)) != str(device_id) or config.get(
                CONF_ASSUMED_STATE
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
            except Exception as err:
                # The next scheduled poll will retry. Never leave an unhandled
                # task exception when a device is disconnected during reload.
                _LOGGER.debug("Post-command R413E16 refresh failed: %s", err)
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
            updated_data.update(
                {entity_id: 1 if state else 0 for entity_id, state in updates.items()}
            )
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

    def _get_tx_stages(self) -> threading.local:
        """Return this thread's latency-stage store, creating it when needed.

        Test helpers build coordinators with ``object.__new__`` and skip
        ``__init__``; lazy creation keeps those minimal instances working.
        """
        stages = getattr(self, "_tx_stages", None)
        if stages is None:
            stages = self._tx_stages = threading.local()
        return stages

    def traffic_signal(self) -> str:
        """Return this hub's live traffic dispatcher signal."""
        return traffic_signal(self.entry_id)

    def _install_response_capture(self) -> None:
        """Hook pymodbus tracing on the current client for real RX bytes.

        Response capture is a diagnostic nicety: a client that exposes no
        supported tracing hook simply keeps the reconstructed-request
        behaviour, and failures here must never break serial I/O.
        """
        capture = getattr(self, "response_capture", None)
        if capture is None:
            capture = self.response_capture = ResponseCapture()
        try:
            self.capture_hook = install_response_capture(self.client, capture)
        except Exception as err:  # pragma: no cover - defensive
            _LOGGER.debug("Response capture hook unavailable: %s", err)
            self.capture_hook = None
        if self.capture_hook:
            _LOGGER.debug(
                "Traffic inspector response capture active via %s", self.capture_hook
            )

    def _notify_traffic_subscribers(self, item: dict[str, Any]) -> None:
        """Push one recorded transaction to WebSocket traffic subscribers.

        ``_record_transaction`` runs in executor threads; the dispatcher must
        run on the Home Assistant event loop, so the signal hops threads when
        needed. Minimal unit-test coordinators without a hass object simply
        skip the notification.
        """
        hass = getattr(self, "hass", None)
        loop = getattr(hass, "loop", None)
        if loop is None:
            return
        signal = self.traffic_signal()
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            async_dispatcher_send(hass, signal, dict(item))
            return
        try:
            loop.call_soon_threadsafe(async_dispatcher_send, hass, signal, dict(item))
        except RuntimeError:
            pass  # loop already closed during shutdown

    def _resolve_port_path(self, port: str) -> str:
        """Detect when /dev/ttyUSB* dynamically switches index or has a persistent /dev/serial/by-id link."""
        import os

        if not port or not isinstance(port, str):
            return port
        if port.startswith("/dev/serial/by-id/") and os.path.exists(port):
            return port
        by_id_dir = "/dev/serial/by-id"
        if os.path.isdir(by_id_dir):
            if os.path.exists(port):
                real_target = os.path.realpath(port)
                for name in sorted(os.listdir(by_id_dir)):
                    link_path = os.path.join(by_id_dir, name)
                    try:
                        if os.path.realpath(link_path) == real_target:
                            return link_path
                    except OSError:
                        continue
                return port
            for name in sorted(os.listdir(by_id_dir)):
                link_path = os.path.join(by_id_dir, name)
                try:
                    real_target = os.path.realpath(link_path)
                    if os.path.exists(real_target) and (
                        "ttyUSB" in real_target or "ttyACM" in real_target
                    ):
                        return link_path
                except OSError:
                    continue
        return port

    def _ensure_connected(self) -> None:
        """Open the serial adapter or raise a useful error before a request."""
        if self.client.connected:
            return
        if not self._connect_with_retries():
            raise UpdateFailed(
                connection_error_message(getattr(self, "serial_config", None))
            )

    def _connect_with_retries(
        self, attempts: int = 3, base_delay: float = 0.25, max_delay: float = 3.0
    ) -> bool:
        """Connect a USB serial client with exponential backoff and jitter."""
        import random

        # Check if port needs persistent resolution or dynamically switched
        # (USB adapters only — ESPHome transports address a network host).
        serial_cfg = getattr(self, "serial_config", None)
        if is_serial(serial_cfg):
            current_port = getattr(self.client, "port", None) or (
                serial_cfg.get(CONF_PORT) if serial_cfg else None
            )
            resolved = self._resolve_port_path(str(current_port or ""))
            if resolved and resolved != getattr(self.client, "port", None):
                self.client.port = resolved
                if serial_cfg is not None:
                    serial_cfg[CONF_PORT] = resolved

        for attempt in range(attempts):
            try:
                if self.client.connect():
                    return True
            except Exception:
                pass
            if attempt < attempts - 1:
                # Exponential backoff with random jitter
                backoff = min(max_delay, base_delay * (2**attempt))
                jitter = random.uniform(0, 0.25 * backoff)
                sleep(backoff + jitter)
        return False

    def reconfigure_serial(self, serial_config: dict[str, Any]) -> bool:
        """Replace the bus client in place without unloading HA entities.

        Works for every transport (serial adapter, ESPHome RTU-over-TCP,
        ESPHome API): the new connection dict is merged over the current one
        and a fresh client is built through ``transport.build_client``.
        """
        config = {**self.serial_config, **serial_config}
        with self._serial_lock:
            self.client.close()
            capture = getattr(self, "response_capture", None)
            if capture is not None:
                capture.detach()  # drop any logging fallback for the old client
            self.client = build_client(config, hass=getattr(self, "hass", None))
            self.serial_config = config
            self._install_response_capture()
            return self._connect_with_retries()

    reconfigure_connection = reconfigure_serial

    def _apply_inter_frame_delay(self) -> None:
        delay_ms = 0
        if getattr(self, "hass", None) and hasattr(self.hass, "config_entries"):
            entry = self.hass.config_entries.async_get_entry(self.entry_id)
            if entry:
                delay_ms = (
                    entry.options.get("inter_frame_delay_ms")
                    or entry.data.get("inter_frame_delay_ms")
                    or 0
                )
        if delay_ms > 0:
            sleep(delay_ms / 1000.0)

    @contextmanager
    def _serial_transaction(self):
        """Own the bus for one transaction and record its latency waterfall.

        Stages are measured around lock acquisition, port (re)connection, the
        optional inter-frame delay, and the serial request itself. They are
        stored on a thread-local so the matching _record_transaction call can
        attach them even when the transaction raised.
        """
        stages: dict[str, float] = {}
        wait_started = time.monotonic()
        with self._serial_lock:
            stages["lock_wait_ms"] = round((time.monotonic() - wait_started) * 1000, 2)
            connect_started = time.monotonic()
            self._ensure_connected()
            stages["connect_ms"] = round((time.monotonic() - connect_started) * 1000, 2)
            self._get_tx_stages().stages = stages
            yield stages

    def _call_modbus(
        self, method_name: str, *args: Any, slave: int, **kwargs: Any
    ) -> Any:
        """Call a method on this hub's configured serial client."""
        stages = getattr(self._get_tx_stages(), "stages", None)
        delay_started = time.monotonic()
        self._apply_inter_frame_delay()
        if stages is not None:
            stages["frame_delay_ms"] = round(
                (time.monotonic() - delay_started) * 1000, 2
            )
        request_started = time.monotonic()
        result = call_modbus_on_client(
            self.client, method_name, *args, slave=slave, **kwargs
        )
        if stages is not None:
            stages["request_ms"] = round((time.monotonic() - request_started) * 1000, 2)
        return result

    def scan_bus(
        self,
        baudrates: list[int],
        parities: list[str] | None = None,
        start_slave: int = 1,
        end_slave: int = 20,
        templates: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Probe a bounded RS-485 range and report devices that answer.

        The primary client is temporarily closed while an isolated, short-timeout
        client tests each baud rate. Any valid Modbus exception response still
        counts as a detected device: it proves the slave and serial settings.
        """
        start_slave = max(1, min(247, int(start_slave)))
        end_slave = max(start_slave, min(247, int(end_slave)))
        valid_bauds = sorted({int(rate) for rate in baudrates if int(rate) > 0})
        if not valid_bauds:
            raise ValueError("Select at least one baud rate to scan")
        valid_parities = sorted(
            {
                str(parity).upper()
                for parity in (parities or [self.serial_config.get(CONF_PARITY, "N")])
            }
        )
        if not set(valid_parities).issubset({"N", "E", "O"}):
            raise ValueError("Parity must be N (none), E (even), or O (odd)")

        # ESPHome bridges fix baud/parity in their own `uart:` block, so the
        # scan can only sweep slave IDs there — one probe with the configured
        # line settings, results reported without a baud rate.
        baudrate_fixed = not is_serial(self.serial_config)
        if baudrate_fixed:
            valid_bauds = [int(self.serial_config.get(CONF_BAUDRATE) or 0) or 0]
            valid_parities = [str(self.serial_config.get(CONF_PARITY, "N"))]

        found: list[dict[str, Any]] = []
        probed = 0
        total = len(valid_bauds) * len(valid_parities) * (end_slave - start_slave + 1)
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
                    for parity in valid_parities:
                        probe = build_client(
                            self.serial_config,
                            baudrate=baudrate or None,
                            parity=parity,
                            timeout=0.2 if not baudrate_fixed else 0.5,
                            retries=0,
                            hass=getattr(self, "hass", None),
                        )
                        try:
                            if not probe.connect():
                                continue
                            for slave in range(start_slave, end_slave + 1):
                                probed += 1
                                self.scan_progress["completed"] = probed
                                try:
                                    result = call_modbus_on_client(
                                        probe,
                                        "read_holding_registers",
                                        0,
                                        count=1,
                                        slave=slave,
                                    )
                                    message = str(result)
                                    no_response = (
                                        "no response received" in message.lower()
                                    )
                                    if not no_response:
                                        response_kind = (
                                            "register response"
                                            if not result.isError()
                                            else "exception response"
                                        )
                                        suggestions = self._match_templates(
                                            probe, slave, templates or []
                                        )
                                        found.append(
                                            {
                                                "slave_id": slave,
                                                "baudrate": None
                                                if baudrate_fixed
                                                else baudrate,
                                                "parity": parity,
                                                "response": response_kind,
                                                "suggestions": suggestions,
                                            }
                                        )
                                        self.scan_progress["found"] = len(found)
                                        self._record_transaction(
                                            "scan_found",
                                            slave=slave,
                                            address=0,
                                            result=(
                                                f"{response_kind} (bridge line settings)"
                                                if baudrate_fixed
                                                else f"{baudrate} baud, {parity} parity — {response_kind}"
                                            ),
                                        )
                                except (
                                    Exception
                                ):  # A timeout is expected for unused IDs.
                                    continue
                        finally:
                            probe.close()
            finally:
                self.client.connect()
                self.scan_progress["active"] = False
                self.scan_progress["completed"] = probed
        return {
            "found": found,
            "probed": probed,
            "start_slave": start_slave,
            "end_slave": end_slave,
            "transport": describe_transport(self.serial_config)["transport"],
            "baudrate_fixed": baudrate_fixed,
        }

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
                        result = call_modbus_on_client(
                            client,
                            "read_holding_registers",
                            address,
                            count=count,
                            slave=slave,
                        )
                    elif register_type == REGISTER_TYPE_INPUT:
                        result = call_modbus_on_client(
                            client,
                            "read_input_registers",
                            address,
                            count=count,
                            slave=slave,
                        )
                    else:
                        raise ValueError(
                            "Fingerprint register type must be holding or input"
                        )
                    if result.isError():
                        raise UpdateFailed(str(result))
                    value = decode_words(result.registers, data_type)
                    if (
                        not float(probe["min_value"])
                        <= value
                        <= float(probe["max_value"])
                    ):
                        raise ValueError("Value outside expected range")
                matches.append(
                    template.get("name", template.get("id", "Unknown template"))
                )
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
        function_code: str | None = None,
        request_hex: str | None = None,
    ) -> None:
        """Keep a rolling, UI-safe record of every RS-485 operation and health.

        When pymodbus transaction tracing is hooked (see ``capture.py``), the
        real TX/RX bytes of the transaction that just finished are attached:
        ``request_captured`` marks a request frame read from the wire instead
        of reconstructed, and ``response_hex`` carries the actual response
        frame (byte-count frames, write echoes, and exception frames).
        Operations that send several frames before one record (batch writes,
        board block readers) keep the last TX frame as ``request_hex`` and
        the full raw RX stream as ``response_frames`` (one entry per
        response frame, oldest first) together with the per-frame arrival
        times ``response_frame_times_ms`` (milliseconds since the first TX
        of the operation, same index as ``response_frames``).
        """
        timestamp = datetime.now().astimezone().isoformat()
        detected_function, detected_request = diagnostic_request_frame(
            operation, slave, address, count, value
        )
        item: dict[str, Any] = {
            "timestamp": timestamp,
            "operation": operation,
            "slave": slave,
            "address": address,
            "count": count,
            "value": value,
            "result": result,
            "status": "error" if error else "ok",
            "function_code": function_code or detected_function,
            "request_hex": request_hex or detected_request,
            "retries_configured": (
                int(getattr(self.client, "retries", 0))
                if isinstance(getattr(self.client, "retries", None), (int, float, str))
                else 0
            ),
        }
        capture = getattr(self, "response_capture", None)
        window = capture.consume() if capture is not None else None
        if window:
            if request_hex is None and window.get("request_hex"):
                # Real TX bytes beat the reconstructed request frame.
                item["request_hex"] = window["request_hex"]
                item["request_captured"] = True
            if window.get("response_hex"):
                item["response_hex"] = window["response_hex"]
            if window.get("response_frames"):
                # Full raw RX stream of the transaction, already framed —
                # multi-frame responses (batch reads, block readers) keep
                # every frame instead of only the last one.
                item["response_frames"] = list(window["response_frames"])
                frame_times = window.get("response_frame_times_ms")
                if frame_times and len(frame_times) == len(item["response_frames"]):
                    # Per-frame arrival times (ms since the first TX) feed
                    # the inspector's inter-frame gap waterfall and the
                    # per-frame CSV export.
                    item["response_frame_times_ms"] = list(frame_times)
        if duration_ms is not None:
            item["duration_ms"] = round(duration_ms, 1)
        tx_stages = self._get_tx_stages()
        stages = getattr(tx_stages, "stages", None)
        if stages:
            item["latency"] = dict(stages)
        tx_stages.stages = None
        if error:
            item["error"] = str(error)
        self.transaction_log.appendleft(item)
        self._notify_traffic_subscribers(item)

        # Health must reflect every real wire transaction, not just coordinator
        # polling. Boards with assumed-state switches (including R413E16) are
        # controlled mainly through writes, so counting reads alone leaves the
        # diagnostic page at zero despite successful RS-485 traffic.
        self.diag[DIAG_TOTAL_READS] += 1
        if error:
            self.diag[DIAG_FAILED_READS] += 1
            self.diag[DIAG_CONSECUTIVE_FAILURES] += 1
            self.diag[DIAG_LAST_ERROR] = str(error)
            _LOGGER.warning(
                "RS-485 %s failed: slave=%s address=%s error=%s",
                operation,
                slave,
                address,
                error,
            )
        else:
            self.diag[DIAG_CONSECUTIVE_FAILURES] = 0
            self.diag[DIAG_LAST_SUCCESS] = timestamp
            _LOGGER.debug(
                "RS-485 %s: slave=%s address=%s value=%s result=%s duration=%.1fms",
                operation,
                slave,
                address,
                value,
                result,
                duration_ms or 0,
            )

    def execute_manual_hex_write(
        self, frame_hex: str, generate_crc: bool = False
    ) -> dict[str, Any]:
        """Execute one CRC-checked standard Modbus RTU write via the owned client.

        The Diagnostics page accepts complete RTU frames for documented writes,
        but deliberately does not expose a raw serial bypass.  Decoding the PDU
        here preserves the integration's lock, reconnection behavior, error
        handling, and transaction audit trail.
        """
        compact = "".join(frame_hex.replace("0x", "").replace("0X", "").split())
        if (
            not compact
            or len(compact) % 2
            or any(char not in "0123456789abcdefABCDEF" for char in compact)
        ):
            raise ValueError(
                "Paste hexadecimal bytes only, for example: 01 06 00 80 00 01 49 E2"
            )
        frame = bytes.fromhex(compact)
        if generate_crc:
            if len(frame) >= 4 and modbus_crc16(frame[:-2]) == (
                frame[-2] | (frame[-1] << 8)
            ):
                # A valid CRC is already present; keep the exact documented frame.
                pass
            else:
                crc = modbus_crc16(frame)
                frame += bytes((crc & 0xFF, crc >> 8))
        if len(frame) < 8:
            raise ValueError(
                "A complete Modbus RTU write frame must include slave, function, data, and two CRC bytes"
            )
        crc_expected = frame[-2] | (frame[-1] << 8)
        crc_actual = modbus_crc16(frame[:-2])
        if crc_actual != crc_expected:
            raise ValueError(
                f"CRC mismatch: frame has {crc_expected:04X}, expected {crc_actual:04X}"
            )

        slave, function_code = frame[0], frame[1]
        if not 1 <= slave <= 247:
            raise ValueError(
                "Broadcast or invalid slave IDs are not allowed for dangerous manual writes"
            )
        payload = frame[:-2]
        address = (frame[2] << 8) | frame[3]
        count = 1
        value: Any = None
        started = datetime.now()
        try:
            with self._serial_transaction():
                if function_code == 0x05:
                    if len(payload) != 6 or payload[4:] not in (
                        b"\x00\x00",
                        b"\xff\x00",
                    ):
                        raise ValueError(
                            "FC05 must contain exactly one coil value: FF00 (on) or 0000 (off)"
                        )
                    value = payload[4:] == b"\xff\x00"
                    result = self._call_modbus(
                        "write_coil", address, value, slave=slave
                    )
                elif function_code == 0x06:
                    if len(payload) != 6:
                        raise ValueError(
                            "FC06 must contain exactly one 16-bit holding-register value"
                        )
                    value = (payload[4] << 8) | payload[5]
                    result = self._call_modbus(
                        "write_register", address, value, slave=slave
                    )
                elif function_code == 0x0F:
                    if len(payload) < 8:
                        raise ValueError("FC0F frame is incomplete")
                    count = (payload[4] << 8) | payload[5]
                    byte_count = payload[6]
                    if (
                        not 1 <= count <= 1968
                        or byte_count != (count + 7) // 8
                        or len(payload) != 7 + byte_count
                    ):
                        raise ValueError(
                            "FC0F count or byte count does not match the supplied coil data"
                        )
                    value = [
                        bool(payload[7 + index // 8] & (1 << (index % 8)))
                        for index in range(count)
                    ]
                    result = self._call_modbus(
                        "write_coils", address, value, slave=slave
                    )
                elif function_code == 0x10:
                    if len(payload) < 9:
                        raise ValueError("FC10 frame is incomplete")
                    count = (payload[4] << 8) | payload[5]
                    byte_count = payload[6]
                    if (
                        not 1 <= count <= 123
                        or byte_count != count * 2
                        or len(payload) != 7 + byte_count
                    ):
                        raise ValueError(
                            "FC10 register count or byte count does not match the supplied data"
                        )
                    value = [
                        (payload[index] << 8) | payload[index + 1]
                        for index in range(7, 7 + byte_count, 2)
                    ]
                    result = self._call_modbus(
                        "write_registers", address, value, slave=slave
                    )
                else:
                    raise ValueError(
                        "Only documented write functions FC05, FC06, FC0F, and FC10 are accepted"
                    )
                if result.isError():
                    raise UpdateFailed(str(result))
            self._record_transaction(
                "manual_hex_write",
                slave=slave,
                address=address,
                count=count,
                value=value,
                result="accepted",
                duration_ms=(datetime.now() - started).total_seconds() * 1000,
                function_code=f"0x{function_code:02X}",
                request_hex=" ".join(f"{byte:02X}" for byte in frame),
            )
            return {
                "slave_id": slave,
                "function_code": f"0x{function_code:02X}",
                "address": address,
                "count": count,
            }
        except Exception as err:
            self._record_transaction(
                "manual_hex_write",
                slave=slave,
                address=address,
                count=count,
                value=value,
                error=err,
                duration_ms=(datetime.now() - started).total_seconds() * 1000,
                function_code=f"0x{function_code:02X}",
                request_hex=" ".join(f"{byte:02X}" for byte in frame),
            )
            raise

    def get_diagnostics(self) -> dict[str, Any]:
        """Return a serial-health snapshot and rolling RS-485 transaction log."""
        return {
            "connected": bool(getattr(self.client, "connected", False)),
            "entry_id": self.entry_id,
            "default_slave_id": self.slave_id,
            "health": dict(self.diag),
            "transactions": list(self.transaction_log),
            "scan": dict(self.scan_progress),
            "boost": self.boost_state(),
            "circuit_breaker": self.circuit_breaker.get_summary()
            if hasattr(self, "circuit_breaker")
            else {},
            "capture": {
                # Which pymodbus tracing hook (if any) records real RX bytes
                # for the Traffic Inspector: trace_packet, client_trace_packet,
                # logging, or None when unavailable.
                "hook": getattr(self, "capture_hook", None),
            },
            "serial": {
                "port": self.serial_config.get(CONF_PORT),
                "baudrate": self.serial_config.get(CONF_BAUDRATE),
                "bytesize": self.serial_config.get(CONF_BYTESIZE),
                "parity": self.serial_config.get(CONF_PARITY),
                "stopbits": self.serial_config.get(CONF_STOPBITS),
                "connection_owner": "Home Assistant Modbus USB",
                "operation_active": self._serial_lock.locked(),
                # v2.8.0: transport summary (never includes credentials).
                **self._transport_diagnostics(),
            },
        }

    def _transport_diagnostics(self) -> dict[str, Any]:
        summary = describe_transport(self.serial_config)
        client = self.client
        esphome = getattr(client, "device_info", None)
        if isinstance(esphome, dict) and esphome:
            summary["esphome_device"] = dict(esphome)
        stats = getattr(client, "stats", None)
        if isinstance(stats, dict):
            summary["bridge_stats"] = dict(stats)
        last_error = getattr(client, "last_error", None)
        if isinstance(last_error, str) and last_error:
            summary["last_error"] = last_error
        return summary

    def clear_transaction_log(self) -> None:
        """Clear only the rolling activity log; preserve health counters."""
        self.transaction_log.clear()

    def close(self) -> None:
        """Close the serial client only after any in-flight transaction finishes."""
        with self._serial_lock:
            self.client.close()
        capture = getattr(self, "response_capture", None)
        if capture is not None:
            # Remove a logging-fallback handler so an unloaded hub does not
            # keep receiving (and buffering) pymodbus frame dumps.
            capture.detach()

    def _get_entities(self) -> list[dict]:
        entry = self.hass.config_entries.async_get_entry(self.entry_id)
        if entry is None:
            return []
        enabled_devices = {
            str(device.get("id")): device.get(CONF_ENABLED, True)
            for device in entry.options.get(CONF_DEVICES, [])
            if device.get("id")
        }
        return [
            entity
            for entity in entry.options.get(CONF_ENTITIES, [])
            if entity.get(CONF_DEVICE_ID) is None
            or enabled_devices.get(str(entity.get(CONF_DEVICE_ID)), True)
        ]

    def _get_device_slave_map(self) -> dict[str, int]:
        entry = self.hass.config_entries.async_get_entry(self.entry_id)
        if entry is None:
            return {}
        devices = entry.options.get(CONF_DEVICES, [])
        return {
            str(d.get("id")): int(d.get(CONF_SLAVE_ID, self.slave_id))
            for d in devices
            if d.get(CONF_ENABLED, True)
            and "id" in d
            and d.get(CONF_SLAVE_ID) is not None
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
        except Exception as err:
            raise UpdateFailed(
                f"Error communicating with Modbus device: {err}"
            ) from err

    def _read_all(
        self, entities: list[dict], device_slave_map: dict[str, int] | None = None
    ) -> dict[str, Any]:
        import time

        from .const import (
            CONF_GAP_TOLERANCE,
            CONF_MAX_READ_REGISTERS,
            CONF_OPTIMIZE_BLOCKS,
            CONF_SCAN_INTERVAL,
            REGISTER_TYPE_COIL,
            REGISTER_TYPE_DISCRETE,
            REGISTER_TYPE_HOLDING,
            REGISTER_TYPE_INPUT,
        )
        from .decoding import decode_words

        device_slave_map = device_slave_map or {}
        data: dict[str, Any] = {}
        entry = (
            self.hass.config_entries.async_get_entry(self.entry_id)
            if (self.hass and hasattr(self.hass, "config_entries"))
            else None
        )
        devices = {
            str(item.get("id")): item
            for item in (entry.options.get(CONF_DEVICES, []) if entry else [])
        }
        handled: set[str] = set()
        now_ts = time.time()
        if not hasattr(self, "_entity_last_poll"):
            self._entity_last_poll = {}

        # Filter entities by entity-level scan_interval (adaptive cycle optimization)
        active_entities: list[dict] = []
        for ent in entities:
            ent_id = ent.get(CONF_ENTITY_ID)
            if not ent_id:
                continue
            scan_int = ent.get(CONF_SCAN_INTERVAL)
            if scan_int is not None:
                try:
                    interval_sec = float(scan_int)
                    if not hasattr(self, "_entity_last_poll"):
                        self._entity_last_poll = {}
                    last_poll = self._entity_last_poll.get(ent_id, 0.0)
                    if now_ts - last_poll < interval_sec:
                        # Keep previous coordinator value if available
                        if (
                            hasattr(self, "data")
                            and isinstance(self.data, dict)
                            and ent_id in self.data
                        ):
                            data[ent_id] = self.data[ent_id]
                        handled.add(ent_id)
                        continue
                except (ValueError, TypeError):
                    pass
            active_entities.append(ent)

        # 1. Device-specific block readers (e.g. R4D6F20 Command 1 / Command 2)
        for device_id, device in devices.items():
            dev_slave = int(device.get(CONF_SLAVE_ID, self.slave_id))
            if hasattr(
                self, "circuit_breaker"
            ) and not self.circuit_breaker.should_poll(dev_slave):
                # Slave circuit breaker is tripped; skip polling this slave
                continue

            reader = select_block_reader(device)
            members = [
                item
                for item in active_entities
                if str(item.get(CONF_DEVICE_ID)) == device_id
            ]
            if reader is None or not members:
                continue
            try:
                block_data, block_handled = reader(self, members, dev_slave)
                data.update(block_data)
                handled.update(block_handled)
                for eid in block_handled:
                    self._entity_last_poll[eid] = now_ts
                if hasattr(self, "circuit_breaker"):
                    if any(v is not None for v in block_data.values()):
                        self.circuit_breaker.record_success(dev_slave)
                    elif block_data:
                        self.circuit_breaker.record_failure(
                            dev_slave, "Device block read returned all None"
                        )
            except Exception as err:
                if hasattr(self, "circuit_breaker"):
                    self.circuit_breaker.record_failure(dev_slave, err)

        # 2. Multi-Register Block Read Optimizer (Section 2.2)
        optimize_blocks = True
        max_read_regs = 64
        gap_tol = 2
        if entry:
            opts = {**entry.data, **entry.options}
            optimize_blocks = opts.get(CONF_OPTIMIZE_BLOCKS, True)
            max_read_regs = int(opts.get(CONF_MAX_READ_REGISTERS, 64))
            gap_tol = int(opts.get(CONF_GAP_TOLERANCE, 2))

        remaining_entities = [
            ent
            for ent in active_entities
            if ent.get(CONF_ENTITY_ID)
            and ent.get(CONF_ENTITY_ID) not in handled
            and not ent.get(CONF_ASSUMED_STATE)
        ]

        if optimize_blocks and remaining_entities:
            # Group remaining entities by target slave ID
            by_slave: dict[int, list[dict]] = {}
            for ent in remaining_entities:
                slave = ent.get(CONF_SLAVE_ID)
                if slave is None and device_slave_map and ent.get(CONF_DEVICE_ID):
                    slave = device_slave_map.get(str(ent.get(CONF_DEVICE_ID)))
                if slave is None:
                    slave = self.slave_id
                target_slave = int(slave)
                by_slave.setdefault(target_slave, []).append(ent)

            for target_slave, slave_ents in by_slave.items():
                if hasattr(
                    self, "circuit_breaker"
                ) and not self.circuit_breaker.should_poll(target_slave):
                    continue

                blocks, singletons = group_entities_into_blocks(
                    slave_ents,
                    max_read_registers=max_read_regs,
                    max_gap_tolerance=gap_tol,
                )

                for block in blocks:
                    # If block only contains 1 span of length 1, fall back to individual read
                    if len(block.spans) == 1 and block.count <= 2:
                        continue

                    # Execute packed multi-register block read
                    started = datetime.now()
                    try:
                        with self._serial_transaction():
                            if block.register_type == REGISTER_TYPE_HOLDING:
                                result = self._call_modbus(
                                    "read_holding_registers",
                                    block.start_address,
                                    count=block.count,
                                    slave=target_slave,
                                )
                            elif block.register_type == REGISTER_TYPE_INPUT:
                                result = self._call_modbus(
                                    "read_input_registers",
                                    block.start_address,
                                    count=block.count,
                                    slave=target_slave,
                                )
                            elif block.register_type == REGISTER_TYPE_COIL:
                                result = self._call_modbus(
                                    "read_coils",
                                    block.start_address,
                                    count=block.count,
                                    slave=target_slave,
                                )
                            elif block.register_type == REGISTER_TYPE_DISCRETE:
                                result = self._call_modbus(
                                    "read_discrete_inputs",
                                    block.start_address,
                                    count=block.count,
                                    slave=target_slave,
                                )
                            else:
                                continue

                            if result.isError():
                                raise UpdateFailed(str(result))

                        # Decode registers for each span in this block
                        for span in block.spans:
                            offset = span.start_address - block.start_address
                            if block.register_type in (
                                REGISTER_TYPE_COIL,
                                REGISTER_TYPE_DISCRETE,
                            ):
                                val = bool(result.bits[offset])
                            else:
                                words = result.registers[offset : offset + span.count]
                                val = decode_words(words, span.data_type)
                                if span.scale != 1.0:
                                    val = val * span.scale
                            data[span.entity_id] = val
                            self._entity_last_poll[span.entity_id] = now_ts
                            handled.add(span.entity_id)

                        dur = (datetime.now() - started).total_seconds() * 1000
                        self._record_transaction(
                            f"read_{block.register_type}",
                            slave=target_slave,
                            address=block.start_address,
                            count=block.count,
                            result="packed_block",
                            duration_ms=dur,
                        )
                        if hasattr(self, "circuit_breaker"):
                            self.circuit_breaker.record_success(target_slave)
                    except Exception as err:
                        for span in block.spans:
                            data[span.entity_id] = None
                            handled.add(span.entity_id)
                        dur = (datetime.now() - started).total_seconds() * 1000
                        self._record_transaction(
                            f"read_{block.register_type}",
                            slave=target_slave,
                            address=block.start_address,
                            count=block.count,
                            error=err,
                            duration_ms=dur,
                        )
                        if hasattr(self, "circuit_breaker"):
                            self.circuit_breaker.record_failure(target_slave, err)

        # 3. Remaining individual entity reads
        for ent in entities:
            ent_id = ent.get(CONF_ENTITY_ID)
            if not ent_id or ent_id in handled:
                continue
            if ent.get(CONF_ASSUMED_STATE):
                continue
            if CONF_REGISTER_TYPE not in ent or CONF_ADDRESS not in ent:
                _LOGGER.warning(
                    "Skipping entity %s: missing register type or address",
                    ent.get(CONF_NAME, ent_id),
                )
                continue

            slave = ent.get(CONF_SLAVE_ID)
            if slave is None and device_slave_map and ent.get(CONF_DEVICE_ID):
                slave = device_slave_map.get(str(ent.get(CONF_DEVICE_ID)))
            if slave is None:
                slave = self.slave_id
            target_slave = int(slave)

            if hasattr(
                self, "circuit_breaker"
            ) and not self.circuit_breaker.should_poll(target_slave):
                # Skip polling offline/degraded slave
                continue

            try:
                value = self._read_one(ent, device_slave_map)
                data[ent_id] = value
                self._entity_last_poll[ent_id] = now_ts
                if ent.get(CONF_ENTITY_TYPE) == "switch" and is_r413e16_switch_config(
                    ent
                ):
                    self._command_states[ent_id] = value in R413E16_STATE_ON_VALUES
                if hasattr(self, "circuit_breaker"):
                    self.circuit_breaker.record_success(target_slave)
            except Exception as err:
                _LOGGER.warning("Failed reading %s: %s", ent.get("name", ent_id), err)
                data[ent_id] = None
                if hasattr(self, "circuit_breaker"):
                    self.circuit_breaker.record_failure(target_slave, err)
        return data

    def _read_one(
        self, ent: dict, device_slave_map: dict[str, int] | None = None
    ) -> Any:
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
            with self._serial_transaction():
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
                            "read_holding_registers",
                            address,
                            count=count,
                            slave=target_slave,
                        )
                    elif register_type == REGISTER_TYPE_INPUT:
                        result = self._call_modbus(
                            "read_input_registers",
                            address,
                            count=count,
                            slave=target_slave,
                        )
                    else:
                        raise UpdateFailed(f"Unknown register type: {register_type}")
                    if result.isError():
                        raise UpdateFailed(str(result))
                    value = decode_words(result.registers, data_type)
                    scale = as_float(ent.get(CONF_SCALE), 1)
                    if scale != 1:
                        value = value * scale
            self._record_transaction(
                operation,
                slave=target_slave,
                address=address,
                count=count,
                result=value,
                duration_ms=(datetime.now() - started).total_seconds() * 1000,
            )
            return value
        except Exception as err:
            self._record_transaction(
                operation,
                slave=target_slave,
                address=address,
                count=count,
                error=err,
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
            with self._serial_transaction():
                result = self._call_modbus(
                    "write_coil", address, value, slave=target_slave
                )
                if result.isError():
                    raise UpdateFailed(str(result))
            self._record_transaction(
                "write_coil",
                slave=target_slave,
                address=address,
                value=value,
                result="accepted",
                duration_ms=(datetime.now() - started).total_seconds() * 1000,
            )
        except Exception as err:
            self._record_transaction(
                "write_coil",
                slave=target_slave,
                address=address,
                value=value,
                error=err,
                duration_ms=(datetime.now() - started).total_seconds() * 1000,
            )
            raise

    def write_register(
        self, address: int, value: int, slave: int | None = None
    ) -> None:
        """Write a single holding register (used by switches modeled as registers)."""
        target_slave = int(slave if slave is not None else self.slave_id)
        started = datetime.now()
        try:
            with self._serial_transaction():
                result = self._call_modbus(
                    "write_register", address, value, slave=target_slave
                )
                if result.isError():
                    raise UpdateFailed(str(result))
            self._record_transaction(
                "write_holding",
                slave=target_slave,
                address=address,
                value=value,
                result="accepted",
                duration_ms=(datetime.now() - started).total_seconds() * 1000,
            )
        except Exception as err:
            self._record_transaction(
                "write_holding",
                slave=target_slave,
                address=address,
                value=value,
                error=err,
                duration_ms=(datetime.now() - started).total_seconds() * 1000,
            )
            raise

    def write_registers_32bit(
        self, address: int, value: int | float, data_type: str, slave: int | None = None
    ) -> None:
        """Write two consecutive holding registers for 32-bit data types."""
        target_slave = int(slave if slave is not None else self.slave_id)
        if data_type in ("int32",):
            raw = struct.pack(">i", value)
        elif data_type in ("float32",):
            raw = struct.pack(">f", value)
        elif data_type == "uint32":
            raw = struct.pack(">I", value)
        else:
            raise ValueError(f"Unsupported 32-bit data type: {data_type}")
        high, low = struct.unpack(">HH", raw)
        started = datetime.now()
        try:
            with self._serial_transaction():
                result = self._call_modbus(
                    "write_registers", address, [high, low], slave=target_slave
                )
                if result.isError():
                    raise UpdateFailed(str(result))
            self._record_transaction(
                "write_holding_32bit",
                slave=target_slave,
                address=address,
                count=2,
                value=value,
                result="accepted",
                duration_ms=(datetime.now() - started).total_seconds() * 1000,
            )
        except Exception as err:
            self._record_transaction(
                "write_holding_32bit",
                slave=target_slave,
                address=address,
                count=2,
                value=value,
                error=err,
                duration_ms=(datetime.now() - started).total_seconds() * 1000,
            )
            raise

    def read_raw_words(
        self, address: int, register_type: str, count: int, slave: int
    ) -> list[int]:
        """Read raw register words / coil bits without decoding.

        Used by the interactive Template Designer, which decodes one response
        into several candidate data types so users can verify their mapping.
        """
        target_slave = int(slave)
        started = datetime.now()
        operation = f"read_{register_type}"
        try:
            with self._serial_transaction():
                if register_type == REGISTER_TYPE_COIL:
                    result = self._call_modbus(
                        "read_coils", address, count=count, slave=target_slave
                    )
                elif register_type == REGISTER_TYPE_DISCRETE:
                    result = self._call_modbus(
                        "read_discrete_inputs",
                        address,
                        count=count,
                        slave=target_slave,
                    )
                elif register_type == REGISTER_TYPE_HOLDING:
                    result = self._call_modbus(
                        "read_holding_registers",
                        address,
                        count=count,
                        slave=target_slave,
                    )
                elif register_type == REGISTER_TYPE_INPUT:
                    result = self._call_modbus(
                        "read_input_registers",
                        address,
                        count=count,
                        slave=target_slave,
                    )
                else:
                    raise ValueError(f"Unknown register type: {register_type}")
                if result.isError():
                    raise UpdateFailed(str(result))
                if register_type in (REGISTER_TYPE_COIL, REGISTER_TYPE_DISCRETE):
                    words = [int(bit) for bit in result.bits[:count]]
                else:
                    words = list(result.registers[:count])
            self._record_transaction(
                operation,
                slave=target_slave,
                address=address,
                count=count,
                result="raw_words",
                duration_ms=(datetime.now() - started).total_seconds() * 1000,
            )
            return words
        except Exception as err:
            self._record_transaction(
                operation,
                slave=target_slave,
                address=address,
                count=count,
                error=err,
                duration_ms=(datetime.now() - started).total_seconds() * 1000,
            )
            raise

    @staticmethod
    def _encode_write_words(value: Any, data_type: str) -> list[int] | None:
        """Encode one value into register words for a batch write item."""
        if data_type in (DATA_TYPE_UINT16, DATA_TYPE_INT16):
            return None
        if data_type == "uint32":
            raw = struct.pack(">I", int(value))
        elif data_type == "int32":
            raw = struct.pack(">i", int(value))
        elif data_type == "float32":
            raw = struct.pack(">f", float(value))
        else:
            raise ValueError(f"Unsupported batch write data type: {data_type}")
        high, low = struct.unpack(">HH", raw)
        return [high, low]

    def batch_write(
        self, writes: list[dict[str, Any]], slave: int | None = None
    ) -> dict[str, Any]:
        """Write several coils/holding registers under one coordinator lock.

        Keeping the bus for the whole batch guarantees that no other poll or
        service interleaves between the individual write frames. The batch
        aborts on the first failed write and reports what was applied.
        """
        if not writes:
            raise ValueError("Batch write requires at least one write item")
        target_slave = int(slave if slave is not None else self.slave_id)
        results: list[dict[str, Any]] = []
        started = datetime.now()
        try:
            with self._serial_transaction():
                for write in writes:
                    address = int(write["address"])
                    register_type = write.get(CONF_REGISTER_TYPE, REGISTER_TYPE_HOLDING)
                    value = write["value"]
                    if register_type == REGISTER_TYPE_COIL:
                        result = self._call_modbus(
                            "write_coil", address, bool(value), slave=target_slave
                        )
                        written = bool(value)
                    elif register_type == REGISTER_TYPE_HOLDING:
                        data_type = write.get(CONF_DATA_TYPE, DATA_TYPE_UINT16)
                        words = self._encode_write_words(value, data_type)
                        if words is None:
                            written = int(value) & 0xFFFF
                            result = self._call_modbus(
                                "write_register", address, written, slave=target_slave
                            )
                        else:
                            written = value
                            result = self._call_modbus(
                                "write_registers",
                                address,
                                words,
                                slave=target_slave,
                            )
                    else:
                        raise ValueError(
                            "Batch writes support holding registers and coils, "
                            f"got {register_type!r}"
                        )
                    if result.isError():
                        raise UpdateFailed(str(result))
                    results.append(
                        {
                            "address": address,
                            "register_type": register_type,
                            "value": written,
                            "success": True,
                        }
                    )
            self._record_transaction(
                "batch_write",
                slave=target_slave,
                address=int(writes[0]["address"]),
                count=len(writes),
                result=f"{len(results)} writes accepted",
                duration_ms=(datetime.now() - started).total_seconds() * 1000,
            )
            return {
                "slave_id": target_slave,
                "written": len(results),
                "results": results,
            }
        except Exception as err:
            self._record_transaction(
                "batch_write",
                slave=target_slave,
                address=int(writes[0]["address"]),
                count=len(writes),
                error=err,
                duration_ms=(datetime.now() - started).total_seconds() * 1000,
            )
            raise

    def boost_state(self) -> dict[str, Any]:
        """Describe an active polling boost for diagnostics and the panel."""
        boost_until = getattr(self, "_boost_until", None)
        if boost_until is None or datetime.now(UTC) >= boost_until:
            return {"active": False}
        boost_interval = getattr(self, "_boost_interval", None)
        remaining = (boost_until - datetime.now(UTC)).total_seconds()
        return {
            "active": True,
            "scan_interval": boost_interval.total_seconds() if boost_interval else None,
            "until": boost_until.isoformat(),
            "remaining_seconds": round(remaining, 1),
        }

    def apply_polling_boost(self, scan_interval: float, duration: float) -> dict:
        """Temporarily shorten the coordinator poll interval."""
        now = datetime.now(UTC)
        if getattr(self, "_boost_original_interval", None) is None:
            self._boost_original_interval = self.update_interval
        self._boost_interval = timedelta(seconds=scan_interval)
        self.update_interval = self._boost_interval
        self._boost_until = now + timedelta(seconds=duration)
        _LOGGER.info(
            "Polling boost active: %ss interval for %ss", scan_interval, duration
        )
        return self.boost_state()

    def restore_polling_boost(self) -> None:
        """Restore the poll interval that was active before the boost."""
        original = getattr(self, "_boost_original_interval", None)
        if original is not None:
            self.update_interval = original
        self._boost_original_interval = None
        self._boost_interval = None
        self._boost_until = None
        _LOGGER.info("Polling boost finished; interval restored")

    def read_register_raw(
        self, address: int, register_type: str, data_type: str, slave: int | None = None
    ) -> Any:
        """Perform a one-shot read of a register for the diagnostics/service call."""
        return self._read_one(
            {
                CONF_REGISTER_TYPE: register_type,
                CONF_ADDRESS: address,
                CONF_DATA_TYPE: data_type,
                CONF_SLAVE_ID: slave if slave is not None else self.slave_id,
            }
        )


# Changelog:
# 2026-09-21 — v2.8.0: transport-aware client construction (serial / ESPHome TCP /
#              ESPHome API); scan_bus sweeps slave IDs only on fixed-baud bridges.
# 2026-09-20 — v2.7.1: recorded transactions carry per-frame RX arrival times
#              (response_frame_times_ms) next to response_frames.
# 2026-09-20 — v2.7.0: recorded transactions keep the full framed RX stream
#              (response_frames) of multi-frame operations, not only the last pair.
# 2026-09-20 — v2.6.0: real TX/RX response capture (capture.py) attached to every
#              recorded transaction, plus a per-entry live traffic dispatcher signal.
# 2026-09-08 — Support both Pymodbus device_id and legacy slave keywords.
# Date modified: 2026-09-20
