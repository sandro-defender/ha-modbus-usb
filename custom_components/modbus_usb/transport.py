"""Hub transport selection: serial adapter, ESPHome TCP bridge, ESPHome API.

Pure helpers (no Home Assistant imports) that turn a config-entry ``data``
mapping into a pymodbus-compatible client and a human-readable description.
The coordinator only ever sees "a client with the pymodbus method surface", so
the rest of the integration does not care which transport is active.

Transports
----------
``serial``
    ``pymodbus.client.ModbusSerialClient`` on a local USB/RS-485 adapter.
``esphome_tcp``
    ``pymodbus.client.ModbusTcpClient`` with the **RTU framer**: an ESPHome
    device runs a UART<->TCP stream server (``stream_server`` external
    component) and forwards raw RTU bytes, so pymodbus keeps doing the
    framing, CRC, retries and transaction tracing (Traffic Inspector capture).
``esphome_api``
    :class:`~.esphome_api_client.EsphomeApiModbusClient` — frames are sent via
    the ESPHome native API (a user-defined ``modbus_send`` service) and the
    replies arrive as Home Assistant events fired by the device.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .const import (
    CONF_API_ENCRYPTION_KEY,
    CONF_API_PASSWORD,
    CONF_API_PORT,
    CONF_BAUDRATE,
    CONF_BYTESIZE,
    CONF_ESPHOME_EVENT,
    CONF_ESPHOME_SERVICE,
    CONF_HOST,
    CONF_PARITY,
    CONF_PORT,
    CONF_RESPONSE_TIMEOUT,
    CONF_STOPBITS,
    CONF_TCP_PORT,
    CONF_TRANSPORT,
    DEFAULT_API_PORT,
    DEFAULT_ESPHOME_EVENT,
    DEFAULT_ESPHOME_SERVICE,
    DEFAULT_RESPONSE_TIMEOUT_API,
    DEFAULT_RESPONSE_TIMEOUT_TCP,
    DEFAULT_TCP_PORT,
    SECRET_CONF_KEYS,
    TRANSPORT_ESPHOME_API,
    TRANSPORT_ESPHOME_TCP,
    TRANSPORT_SERIAL,
    TRANSPORTS,
)

TRANSPORT_LABELS = {
    TRANSPORT_SERIAL: "Serial (USB adapter)",
    TRANSPORT_ESPHOME_TCP: "ESPHome · RTU over TCP",
    TRANSPORT_ESPHOME_API: "ESPHome · native API",
}

# Keys that describe the RS-485 side and are shared by every transport.
LINE_SETTING_KEYS = (CONF_BAUDRATE, CONF_BYTESIZE, CONF_PARITY, CONF_STOPBITS)


def transport_of(config: Mapping[str, Any] | None) -> str:
    """Return the transport of a hub config (legacy entries are serial)."""
    if not config:
        return TRANSPORT_SERIAL
    transport = str(config.get(CONF_TRANSPORT) or TRANSPORT_SERIAL)
    return transport if transport in TRANSPORTS else TRANSPORT_SERIAL


# Every key that describes the bus connection (all transports).
CONNECTION_KEYS = (
    CONF_TRANSPORT,
    CONF_PORT,
    *LINE_SETTING_KEYS,
    CONF_HOST,
    CONF_TCP_PORT,
    CONF_API_PORT,
    CONF_API_ENCRYPTION_KEY,
    CONF_API_PASSWORD,
    CONF_ESPHOME_SERVICE,
    CONF_ESPHOME_EVENT,
    CONF_RESPONSE_TIMEOUT,
)


def connection_config(data: Mapping[str, Any] | None) -> dict[str, Any]:
    """Extract the connection subset of a config entry's ``data``.

    The transport is always materialised (legacy entries → ``serial``) so the
    coordinator's ``serial_config`` never needs defaulting downstream.
    """
    data = data or {}
    config = {key: data[key] for key in CONNECTION_KEYS if key in data}
    config[CONF_TRANSPORT] = transport_of(data)
    return config


def is_serial(config: Mapping[str, Any] | None) -> bool:
    return transport_of(config) == TRANSPORT_SERIAL


def is_esphome(config: Mapping[str, Any] | None) -> bool:
    return transport_of(config) in (TRANSPORT_ESPHOME_TCP, TRANSPORT_ESPHOME_API)


def default_response_timeout(transport: str) -> float:
    return (
        DEFAULT_RESPONSE_TIMEOUT_API
        if transport == TRANSPORT_ESPHOME_API
        else DEFAULT_RESPONSE_TIMEOUT_TCP
    )


def response_timeout(config: Mapping[str, Any] | None) -> float:
    """Per-request timeout in seconds for the configured transport."""
    transport = transport_of(config)
    raw = (config or {}).get(CONF_RESPONSE_TIMEOUT)
    try:
        value = float(raw) if raw is not None else default_response_timeout(transport)
    except (TypeError, ValueError):
        value = default_response_timeout(transport)
    return max(0.1, min(value, 30.0))


def tcp_port(config: Mapping[str, Any] | None) -> int:
    try:
        return int((config or {}).get(CONF_TCP_PORT) or DEFAULT_TCP_PORT)
    except (TypeError, ValueError):
        return DEFAULT_TCP_PORT


def api_port(config: Mapping[str, Any] | None) -> int:
    try:
        return int((config or {}).get(CONF_API_PORT) or DEFAULT_API_PORT)
    except (TypeError, ValueError):
        return DEFAULT_API_PORT


def endpoint_of(config: Mapping[str, Any] | None) -> str | None:
    """Human-readable endpoint: ``/dev/ttyUSB0``, ``host:8899`` or ``host``."""
    config = config or {}
    transport = transport_of(config)
    if transport == TRANSPORT_SERIAL:
        port = config.get(CONF_PORT)
        return str(port) if port else None
    host = str(config.get(CONF_HOST) or "").strip()
    if not host:
        return None
    if transport == TRANSPORT_ESPHOME_TCP:
        return f"{host}:{tcp_port(config)}"
    return f"{host}:{api_port(config)}"


def unique_id_for(config: Mapping[str, Any]) -> str:
    """Stable config-entry unique id: one entry owns one bus endpoint.

    Serial entries keep the historical ``<port>_<slave_id>`` shape so existing
    installations are not re-identified; ESPHome entries are keyed by the
    device endpoint only — the same bridge must never be shared by two hubs.
    """
    transport = transport_of(config)
    if transport == TRANSPORT_SERIAL:
        return f"{config.get(CONF_PORT)}_{config.get('slave_id')}"
    host = str(config.get(CONF_HOST) or "").strip().lower()
    if transport == TRANSPORT_ESPHOME_TCP:
        return f"{TRANSPORT_ESPHOME_TCP}:{host}:{tcp_port(config)}"
    return f"{TRANSPORT_ESPHOME_API}:{host}"


def connection_error_message(config: Mapping[str, Any] | None) -> str:
    """Wording of the UpdateFailed raised when the hub cannot connect."""
    transport = transport_of(config)
    endpoint = endpoint_of(config) or "the configured endpoint"
    if transport == TRANSPORT_SERIAL:
        return "Could not open the configured serial port"
    if transport == TRANSPORT_ESPHOME_TCP:
        return f"Could not connect to the ESPHome RTU-over-TCP bridge at {endpoint}"
    return f"Could not connect to the ESPHome native API at {endpoint}"


def redact_connection(config: Mapping[str, Any] | None) -> dict[str, Any]:
    """Copy of a hub config with credentials replaced by a presence flag."""
    result = dict(config or {})
    for key in SECRET_CONF_KEYS:
        if key in result:
            result[key] = "***" if result[key] else ""
    return result


def describe_transport(config: Mapping[str, Any] | None) -> dict[str, Any]:
    """Diagnostics/panel summary of the configured transport."""
    config = config or {}
    transport = transport_of(config)
    summary: dict[str, Any] = {
        "transport": transport,
        "label": TRANSPORT_LABELS[transport],
        "endpoint": endpoint_of(config),
        "host": config.get(CONF_HOST) if transport != TRANSPORT_SERIAL else None,
        # Baud/parity are fixed by the ESPHome `uart:` block; the RS-485
        # scanner can only sweep slave IDs on those transports.
        "baudrate_fixed": transport != TRANSPORT_SERIAL,
        # Both ESPHome transports still deliver real RX bytes to the
        # inspector: pymodbus tracing for TCP, the client shim for the API.
        "capture_support": "trace_packet",
        "response_timeout": response_timeout(config),
    }
    if transport == TRANSPORT_ESPHOME_TCP:
        summary["tcp_port"] = tcp_port(config)
    if transport == TRANSPORT_ESPHOME_API:
        summary["api_port"] = api_port(config)
        summary["esphome_service"] = (
            config.get(CONF_ESPHOME_SERVICE) or DEFAULT_ESPHOME_SERVICE
        )
        summary["esphome_event"] = (
            config.get(CONF_ESPHOME_EVENT) or DEFAULT_ESPHOME_EVENT
        )
        summary["encrypted"] = bool(config.get(CONF_API_ENCRYPTION_KEY))
        summary["password_set"] = bool(config.get(CONF_API_PASSWORD))
    return summary


def _rtu_framer() -> Any:
    """Return pymodbus's RTU framer selector across the 3.6 → 3.x API change."""
    try:
        from pymodbus.framer import FramerType

        return FramerType.RTU
    except ImportError:  # pymodbus < 3.7
        from pymodbus.transaction import ModbusRtuFramer

        return ModbusRtuFramer


def build_client(
    config: Mapping[str, Any],
    *,
    timeout: float | None = None,
    retries: int | None = None,
    hass: Any = None,
    baudrate: int | None = None,
    parity: str | None = None,
) -> Any:
    """Create the pymodbus-compatible client for a hub config.

    ``baudrate``/``parity`` override the configured line settings (bus scan
    probes); they only apply to the serial transport, since ESPHome fixes the
    UART settings in its own YAML. ``hass`` is required by the ESPHome API
    transport (it listens for the device's HA events) and ignored otherwise.
    """
    transport = transport_of(config)
    request_timeout = timeout if timeout is not None else response_timeout(config)
    if transport == TRANSPORT_SERIAL:
        from pymodbus.client import ModbusSerialClient

        kwargs: dict[str, Any] = {
            "port": config[CONF_PORT],
            "baudrate": int(baudrate or config[CONF_BAUDRATE]),
            "bytesize": int(config[CONF_BYTESIZE]),
            "parity": str(parity or config[CONF_PARITY]),
            "stopbits": int(config[CONF_STOPBITS]),
            "timeout": request_timeout,
        }
        if retries is not None:
            kwargs["retries"] = retries
        return ModbusSerialClient(**kwargs)
    if transport == TRANSPORT_ESPHOME_TCP:
        from pymodbus.client import ModbusTcpClient

        kwargs = {
            "port": tcp_port(config),
            "framer": _rtu_framer(),
            "timeout": request_timeout,
        }
        if retries is not None:
            kwargs["retries"] = retries
        return ModbusTcpClient(str(config[CONF_HOST]).strip(), **kwargs)
    from .esphome_api_client import EsphomeApiModbusClient

    return EsphomeApiModbusClient(
        hass,
        host=str(config[CONF_HOST]).strip(),
        port=api_port(config),
        encryption_key=config.get(CONF_API_ENCRYPTION_KEY) or None,
        password=config.get(CONF_API_PASSWORD) or None,
        service_name=config.get(CONF_ESPHOME_SERVICE) or DEFAULT_ESPHOME_SERVICE,
        event_type=config.get(CONF_ESPHOME_EVENT) or DEFAULT_ESPHOME_EVENT,
        timeout=request_timeout,
        retries=retries if retries is not None else 1,
    )


def probe_connection(
    config: Mapping[str, Any], hass: Any = None, *, timeout: float = 5.0
) -> dict[str, Any]:
    """Blocking reachability check used by the config flow and the panel.

    Returns ``{"reachable": bool, "latency_ms": float | None, "error": str |
    None, "error_key": str | None, "esphome": {...} | None}``. Runs in an
    executor: serial → open/close the port; ESPHome TCP → plain socket
    connect (the stream server accepts one client, so nothing is sent);
    ESPHome API → full API handshake, device info, and service check.
    """
    import socket
    import time

    transport = transport_of(config)
    started = time.monotonic()
    result: dict[str, Any] = {
        "transport": transport,
        "endpoint": endpoint_of(config),
        "reachable": False,
        "latency_ms": None,
        "error": None,
        "error_key": None,
        "esphome": None,
    }
    try:
        if transport == TRANSPORT_SERIAL:
            client = build_client(config, timeout=timeout, retries=0)
            try:
                result["reachable"] = bool(client.connect())
            finally:
                client.close()
            if not result["reachable"]:
                result["error"] = "Serial port could not be opened"
                result["error_key"] = "cannot_connect"
        elif transport == TRANSPORT_ESPHOME_TCP:
            host = str(config.get(CONF_HOST) or "").strip()
            with socket.create_connection((host, tcp_port(config)), timeout=timeout):
                result["reachable"] = True
        else:
            client = build_client(config, timeout=timeout, hass=hass)
            try:
                if client.connect():
                    result["reachable"] = True
                    result["esphome"] = dict(getattr(client, "device_info", {}) or {})
                else:
                    result["error"] = getattr(client, "last_error", None) or (
                        "ESPHome API connection failed"
                    )
            finally:
                client.close()
    except Exception as err:
        result["error"] = str(err) or err.__class__.__name__
    if result["reachable"]:
        result["latency_ms"] = round((time.monotonic() - started) * 1000, 1)
    elif result["error"] and not result["error_key"]:
        text = result["error"].lower()
        if "credential" in text or "encryption" in text or "auth" in text:
            result["error_key"] = "invalid_auth"
        elif "api service" in text:
            result["error_key"] = "service_missing"
        else:
            result["error_key"] = "cannot_connect"
    return result


# Changelog:
# 2026-09-21 — v2.8.0: introduced; hub transports serial / esphome_tcp / esphome_api.
