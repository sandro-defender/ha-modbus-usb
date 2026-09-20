"""pymodbus-compatible Modbus client that talks through the ESPHome native API.

The ESPHome device exposes a user-defined API service (``modbus_send``) that
writes a raw RTU frame to its UART, and fires a ``homeassistant.event``
(``esphome.modbus_rx``) with the bytes it receives back. Both travel over the
device's native-API connection, which this client owns exclusively: the event
is delivered to *this* connection as a ``HomeassistantServiceCall`` with
``is_event=True``, so no dependency on Home Assistant's ``esphome``
integration or the HA event bus is needed.

The coordinator drives Modbus from its serial executor thread with the sync
pymodbus method surface (``read_holding_registers(address, count=…, slave=…)``,
``connected``, ``connect()``, ``close()``…). This class provides exactly that
surface; internally every call hops onto the Home Assistant event loop via
``asyncio.run_coroutine_threadsafe`` and waits for the paired reply with a
:class:`~.esphome_bridge.FramePairer`.

Traffic Inspector capture works unchanged: the client exposes a
``trace_packet(sending, data)`` attribute that ``capture.install_response_capture``
chains into, and it is invoked with the real TX frame and every RX frame.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from .esphome_bridge import (
    FC_READ_COILS,
    FC_READ_DISCRETE,
    FC_READ_HOLDING,
    FC_READ_INPUT,
    FC_WRITE_COIL,
    FC_WRITE_COILS,
    FC_WRITE_REGISTER,
    FC_WRITE_REGISTERS,
    FramePairer,
    ModbusFrameError,
    ModbusNoResponse,
    ModbusResponse,
    build_rtu_request,
    frame_to_hex,
    hex_to_frame,
    parse_rtu_response,
)

_LOGGER = logging.getLogger(__name__)

# Payload keys the example ESPHome YAML uses for the RX event.
EVENT_FRAME_KEYS = ("frame", "data", "hex")
# Argument name of the ``modbus_send`` service (an ``int[]`` of frame bytes).
SERVICE_DATA_ARG = "data"
CONNECT_TIMEOUT = 10.0


class EsphomeApiError(ConnectionError):
    """The ESPHome device is unreachable, rejected the login, or lacks the service."""


class EsphomeApiModbusClient:
    """Sync pymodbus-like client backed by an ``aioesphomeapi.APIClient``."""

    def __init__(
        self,
        hass: Any,
        *,
        host: str,
        port: int = 6053,
        encryption_key: str | None = None,
        password: str | None = None,
        service_name: str = "modbus_send",
        event_type: str = "esphome.modbus_rx",
        timeout: float = 1.5,
        retries: int = 1,
        api_client_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.hass = hass
        self.host = host
        self.port = port
        self._encryption_key = encryption_key or None
        self._password = password or None
        self.service_name = service_name
        self.event_type = event_type
        self.timeout = float(timeout)
        self.retries = int(retries)
        self._api_client_factory = api_client_factory
        self._api: Any = None
        self._service: Any = None
        self._connected = False
        self._request_lock = threading.Lock()
        self._pairer = FramePairer()
        self.device_info: dict[str, Any] = {}
        self.last_error: str | None = None
        # capture.install_response_capture chains its recorder into this hook.
        self.trace_packet: Callable[[bool, bytes], bytes] | None = None
        # Bookkeeping surfaced in diagnostics.
        self.stats = {"requests": 0, "responses": 0, "timeouts": 0, "reconnects": 0}

    # ------------------------------------------------------------------
    # pymodbus surface: connection state
    # ------------------------------------------------------------------
    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def comm_params(self) -> Any:  # pragma: no cover - parity with pymodbus
        return {"host": self.host, "port": self.port}

    def connect(self) -> bool:
        """Connect (blocking; called from the executor thread)."""
        try:
            self._run(self.async_connect(), CONNECT_TIMEOUT + 2)
        except Exception as err:
            self.last_error = str(err) or err.__class__.__name__
            self._connected = False
            _LOGGER.debug("ESPHome API connect to %s failed: %s", self.host, err)
            return False
        return self._connected

    def close(self) -> None:
        try:
            self._run(self.async_close(), CONNECT_TIMEOUT)
        except Exception:  # pragma: no cover - best effort on shutdown
            pass

    # ------------------------------------------------------------------
    # pymodbus surface: Modbus functions (executor thread)
    # ------------------------------------------------------------------
    def read_coils(self, address: int, *, count: int = 1, slave: int = 1) -> Any:
        return self._request(FC_READ_COILS, slave, address, count=count)

    def read_discrete_inputs(
        self, address: int, *, count: int = 1, slave: int = 1
    ) -> Any:
        return self._request(FC_READ_DISCRETE, slave, address, count=count)

    def read_holding_registers(
        self, address: int, *, count: int = 1, slave: int = 1
    ) -> Any:
        return self._request(FC_READ_HOLDING, slave, address, count=count)

    def read_input_registers(
        self, address: int, *, count: int = 1, slave: int = 1
    ) -> Any:
        return self._request(FC_READ_INPUT, slave, address, count=count)

    def write_coil(self, address: int, value: bool, *, slave: int = 1) -> Any:
        return self._request(FC_WRITE_COIL, slave, address, value=value)

    def write_register(self, address: int, value: int, *, slave: int = 1) -> Any:
        return self._request(FC_WRITE_REGISTER, slave, address, value=value)

    def write_coils(self, address: int, values: list[bool], *, slave: int = 1) -> Any:
        return self._request(FC_WRITE_COILS, slave, address, values=list(values))

    def write_registers(
        self, address: int, values: list[int], *, slave: int = 1
    ) -> Any:
        return self._request(FC_WRITE_REGISTERS, slave, address, values=list(values))

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _loop(self) -> asyncio.AbstractEventLoop:
        loop = getattr(self.hass, "loop", None)
        if loop is None:
            raise EsphomeApiError("Home Assistant event loop is not available")
        return loop

    def _run(self, coro: Any, timeout: float) -> Any:
        """Run a coroutine on the HA loop from a worker thread and wait."""
        try:
            loop = self._loop()
        except EsphomeApiError:
            coro.close()
            raise
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:  # pragma: no cover - never called from the loop
            raise EsphomeApiError("ESPHome API client must not block the event loop")
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        try:
            return future.result(timeout)
        except TimeoutError:
            future.cancel()
            raise

    def _make_api_client(self) -> Any:
        if self._api_client_factory is not None:
            return self._api_client_factory(
                self.host,
                self.port,
                self._password,
                noise_psk=self._encryption_key,
                client_info="Home Assistant Modbus USB Controller",
            )
        from aioesphomeapi import APIClient

        return APIClient(
            self.host,
            self.port,
            self._password,
            noise_psk=self._encryption_key,
            client_info="Home Assistant Modbus USB Controller",
        )

    async def async_connect(self) -> dict[str, Any]:
        """Open the API connection, verify the service, subscribe to events."""
        if self._connected and self._api is not None:
            return self.device_info
        await self.async_close()
        api = self._make_api_client()
        try:
            await asyncio.wait_for(
                api.connect(login=True, on_stop=self._on_stop), CONNECT_TIMEOUT
            )
            info = await api.device_info()
            _, services = await api.list_entities_services()
        except Exception as err:
            try:
                await api.disconnect(force=True)
            except Exception:  # pragma: no cover - best effort
                pass
            raise EsphomeApiError(self._describe_connect_error(err)) from err
        service = next(
            (
                item
                for item in services
                if getattr(item, "name", None) == self.service_name
            ),
            None,
        )
        if service is None:
            try:
                await api.disconnect(force=True)
            except Exception:  # pragma: no cover
                pass
            available = ", ".join(sorted(getattr(s, "name", "?") for s in services))
            raise EsphomeApiError(
                f"ESPHome device '{getattr(info, 'name', self.host)}' has no "
                f"'{self.service_name}' API service (found: {available or 'none'}). "
                "Flash the modbus_api_bridge YAML from the integration docs."
            )
        api.subscribe_service_calls(self._on_service_call)
        self._api = api
        self._service = service
        self._connected = True
        self.last_error = None
        self.device_info = {
            "name": getattr(info, "name", ""),
            "friendly_name": getattr(info, "friendly_name", ""),
            "mac_address": getattr(info, "mac_address", ""),
            "esphome_version": getattr(info, "esphome_version", ""),
            "model": getattr(info, "model", ""),
            "manufacturer": getattr(info, "manufacturer", ""),
        }
        return self.device_info

    async def async_close(self) -> None:
        api, self._api = self._api, None
        self._service = None
        self._connected = False
        self._pairer.close()
        if api is not None:
            try:
                await api.disconnect()
            except Exception:  # pragma: no cover - best effort
                pass

    async def _on_stop(self, expected_disconnect: bool) -> None:
        """aioesphomeapi tells us the connection dropped."""
        self._connected = False
        self._service = None
        self._pairer.close()
        if not expected_disconnect:
            self.stats["reconnects"] += 1
            self.last_error = "ESPHome API connection lost"

    @staticmethod
    def _describe_connect_error(err: Exception) -> str:
        name = err.__class__.__name__
        text = str(err) or name
        if "InvalidEncryptionKey" in name or "InvalidAuth" in name:
            return f"ESPHome API rejected the credentials ({text})"
        if "RequiresEncryption" in name:
            return "ESPHome API requires an encryption key"
        if isinstance(err, TimeoutError) or "Timeout" in name:
            return "Timed out connecting to the ESPHome API"
        return f"ESPHome API connection failed ({text})"

    def _on_service_call(self, call: Any) -> None:
        """Receive ``homeassistant.event`` calls fired by the device."""
        if not getattr(call, "is_event", False):
            return
        if getattr(call, "service", None) != self.event_type:
            return
        data = dict(getattr(call, "data", None) or {})
        raw = next((data[key] for key in EVENT_FRAME_KEYS if data.get(key)), None)
        if raw is None:
            return
        try:
            frame = hex_to_frame(raw)
        except ModbusFrameError:
            _LOGGER.debug("Ignoring unparsable RX event payload %r", raw)
            return
        if self.trace_packet is not None:
            try:
                self.trace_packet(False, frame)
            except Exception:  # pragma: no cover - capture must never break I/O
                _LOGGER.debug("trace_packet(RX) raised", exc_info=True)
        self._pairer.deliver(frame)

    async def _async_send(self, frame: bytes) -> None:
        api, service = self._api, self._service
        if api is None or service is None or not self._connected:
            raise EsphomeApiError("ESPHome API is not connected")
        await api.execute_service(service, {SERVICE_DATA_ARG: list(frame)})

    def _request(
        self,
        function_code: int,
        slave: int,
        address: int,
        *,
        count: int | None = None,
        value: Any = None,
        values: list[Any] | None = None,
    ) -> Any:
        """Send one request and return the decoded reply (or an error result).

        Mirrors pymodbus's sync behaviour: a transport failure raises, a
        timeout after all retries raises ``ModbusIOException`` (message
        ``No response received``), and a Modbus exception response is
        returned as a result whose ``isError()`` is True.
        """
        from pymodbus.exceptions import ConnectionException, ModbusIOException

        frame = build_rtu_request(
            slave, function_code, address, count=count, value=value, values=values
        )
        with self._request_lock:
            if not self._connected and not self.connect():
                raise ConnectionException(
                    f"Failed to connect[ESPHome API {self.host}:{self.port}]"
                )
            attempts = max(1, self.retries + 1)
            last_error: Exception | None = None
            for attempt in range(attempts):
                self.stats["requests"] += 1
                self._pairer.begin(slave, function_code)
                if self.trace_packet is not None:
                    try:
                        self.trace_packet(True, frame)
                    except Exception:  # pragma: no cover
                        _LOGGER.debug("trace_packet(TX) raised", exc_info=True)
                started = time.monotonic()
                try:
                    self._run(self._async_send(frame), self.timeout + 5)
                except Exception as err:
                    self._pairer.close()
                    self._connected = False
                    raise ConnectionException(
                        f"ESPHome API send failed: {err}"
                    ) from err
                try:
                    reply = self._pairer.wait(self.timeout)
                except ModbusNoResponse as err:
                    self.stats["timeouts"] += 1
                    last_error = err
                    _LOGGER.debug(
                        "No reply from slave %s FC%02X via %s (attempt %s/%s)",
                        slave,
                        function_code,
                        self.host,
                        attempt + 1,
                        attempts,
                    )
                    continue
                self.stats["responses"] += 1
                try:
                    return parse_rtu_response(
                        reply,
                        expected_slave=slave,
                        expected_function=function_code,
                        requested_count=count,
                    )
                except ModbusFrameError as err:
                    last_error = err
                    _LOGGER.debug(
                        "Bad reply %s for slave %s FC%02X after %.0f ms: %s",
                        frame_to_hex(reply),
                        slave,
                        function_code,
                        (time.monotonic() - started) * 1000,
                        err,
                    )
                    continue
            if isinstance(last_error, ModbusFrameError):
                raise ModbusIOException(str(last_error))
            raise ModbusIOException(
                f"No response received after {attempts} attempt(s) "
                f"(slave {slave}, FC{function_code:02X}, ESPHome API {self.host})"
            )


def response_like(result: Any) -> bool:
    """True for objects that expose the pymodbus result surface we rely on."""
    return isinstance(result, ModbusResponse) or (
        hasattr(result, "isError") and hasattr(result, "function_code")
    )


# Changelog:
# 2026-09-21 — v2.8.0: introduced; ESPHome native-API transport for the hub.
