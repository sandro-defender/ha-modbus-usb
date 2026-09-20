"""Tests for hub transport selection and client construction (v2.8.0)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from custom_components.modbus_usb import transport
from custom_components.modbus_usb.const import (
    CONF_API_ENCRYPTION_KEY,
    CONF_API_PASSWORD,
    CONF_HOST,
    CONF_PORT,
    CONF_RESPONSE_TIMEOUT,
    CONF_SLAVE_ID,
    CONF_TCP_PORT,
    CONF_TRANSPORT,
    TRANSPORT_ESPHOME_API,
    TRANSPORT_ESPHOME_TCP,
    TRANSPORT_SERIAL,
)

pytestmark = pytest.mark.fast

SERIAL = {
    CONF_PORT: "/dev/ttyUSB0",
    "baudrate": 9600,
    "bytesize": 8,
    "parity": "N",
    "stopbits": 1,
    CONF_SLAVE_ID: 1,
}
TCP = {
    CONF_TRANSPORT: TRANSPORT_ESPHOME_TCP,
    CONF_HOST: "Modbus-Bridge.local",
    CONF_TCP_PORT: 8899,
    "baudrate": 19200,
    "bytesize": 8,
    "parity": "E",
    "stopbits": 1,
    CONF_SLAVE_ID: 2,
}
API = {
    CONF_TRANSPORT: TRANSPORT_ESPHOME_API,
    CONF_HOST: "10.0.0.7",
    CONF_API_ENCRYPTION_KEY: "c2VjcmV0",
    "baudrate": 9600,
    "bytesize": 8,
    "parity": "N",
    "stopbits": 1,
    CONF_SLAVE_ID: 1,
}


def test_transport_of_defaults_legacy_entries_to_serial() -> None:
    assert transport.transport_of(None) == TRANSPORT_SERIAL
    assert transport.transport_of({}) == TRANSPORT_SERIAL
    assert transport.transport_of(SERIAL) == TRANSPORT_SERIAL
    assert transport.transport_of({CONF_TRANSPORT: "bogus"}) == TRANSPORT_SERIAL
    assert transport.transport_of(TCP) == TRANSPORT_ESPHOME_TCP
    assert transport.transport_of(API) == TRANSPORT_ESPHOME_API
    assert transport.is_serial(SERIAL) and not transport.is_esphome(SERIAL)
    assert transport.is_esphome(TCP) and transport.is_esphome(API)


def test_connection_config_materialises_transport_and_filters_keys() -> None:
    config = transport.connection_config({**SERIAL, "entities": [], "junk": 1})
    assert config[CONF_TRANSPORT] == TRANSPORT_SERIAL
    assert "entities" not in config and "junk" not in config
    assert config[CONF_PORT] == "/dev/ttyUSB0"
    api = transport.connection_config(API)
    assert api[CONF_API_ENCRYPTION_KEY] == "c2VjcmV0"


def test_endpoint_and_unique_id_per_transport() -> None:
    assert transport.endpoint_of(SERIAL) == "/dev/ttyUSB0"
    assert transport.endpoint_of(TCP) == "Modbus-Bridge.local:8899"
    assert transport.endpoint_of(API) == "10.0.0.7:6053"
    assert transport.endpoint_of({CONF_TRANSPORT: TRANSPORT_ESPHOME_TCP}) is None
    # Serial keeps the historical shape so existing entries stay identified.
    assert transport.unique_id_for(SERIAL) == "/dev/ttyUSB0_1"
    assert transport.unique_id_for(TCP) == "esphome_tcp:modbus-bridge.local:8899"
    assert transport.unique_id_for(API) == "esphome_api:10.0.0.7"


def test_response_timeout_defaults_and_clamps() -> None:
    assert transport.response_timeout(SERIAL) == 3.0
    assert transport.response_timeout(TCP) == 3.0
    assert transport.response_timeout(API) == 1.5
    assert transport.response_timeout({**API, CONF_RESPONSE_TIMEOUT: "0.5"}) == 0.5
    assert transport.response_timeout({**API, CONF_RESPONSE_TIMEOUT: 99}) == 30.0
    assert transport.response_timeout({**API, CONF_RESPONSE_TIMEOUT: "x"}) == 1.5


def test_describe_transport_never_leaks_secrets() -> None:
    summary = transport.describe_transport({**API, CONF_API_PASSWORD: "hunter2"})
    flat = repr(summary)
    assert "c2VjcmV0" not in flat and "hunter2" not in flat
    assert summary["encrypted"] is True and summary["password_set"] is True
    assert summary["baudrate_fixed"] is True
    assert summary["esphome_service"] == "modbus_send"
    assert summary["esphome_event"] == "esphome.modbus_rx"
    assert summary["capture_support"] == "trace_packet"
    serial = transport.describe_transport(SERIAL)
    assert serial["baudrate_fixed"] is False and serial["host"] is None
    assert transport.redact_connection(API)[CONF_API_ENCRYPTION_KEY] == "***"
    assert transport.redact_connection({CONF_API_PASSWORD: ""})[CONF_API_PASSWORD] == ""


def test_connection_error_message_names_the_endpoint() -> None:
    assert "serial port" in transport.connection_error_message(SERIAL)
    assert "Modbus-Bridge.local:8899" in transport.connection_error_message(TCP)
    assert "native API" in transport.connection_error_message(API)


def test_build_client_serial_forwards_line_settings_and_overrides() -> None:
    calls: list[dict] = []

    class FakeSerial:
        def __init__(self, **kwargs) -> None:
            calls.append(kwargs)

    with patch("pymodbus.client.ModbusSerialClient", FakeSerial):
        transport.build_client(SERIAL)
        transport.build_client(
            SERIAL, baudrate=38400, parity="O", timeout=0.2, retries=0
        )
    assert calls[0] == {
        "port": "/dev/ttyUSB0",
        "baudrate": 9600,
        "bytesize": 8,
        "parity": "N",
        "stopbits": 1,
        "timeout": 3.0,
    }
    assert calls[1]["baudrate"] == 38400
    assert calls[1]["parity"] == "O"
    assert calls[1]["timeout"] == 0.2
    assert calls[1]["retries"] == 0


def test_build_client_esphome_tcp_uses_rtu_framer() -> None:
    calls: list[tuple] = []

    class FakeTcp:
        def __init__(self, host, **kwargs) -> None:
            calls.append((host, kwargs))

    with patch("pymodbus.client.ModbusTcpClient", FakeTcp):
        transport.build_client(TCP, retries=0)
    host, kwargs = calls[0]
    assert host == "Modbus-Bridge.local"
    assert kwargs["port"] == 8899
    assert kwargs["timeout"] == 3.0
    assert kwargs["retries"] == 0
    framer = kwargs["framer"]
    assert "rtu" in str(framer).lower() or "Rtu" in getattr(framer, "__name__", "")


def test_rtu_framer_falls_back_on_old_pymodbus() -> None:
    import builtins

    real_import = builtins.__import__
    sentinel = object()

    def fake_import(name, *args, **kwargs):
        if name == "pymodbus.framer":
            raise ImportError("no FramerType in pymodbus 3.6")
        if name == "pymodbus.transaction":
            return SimpleNamespace(ModbusRtuFramer=sentinel)
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=fake_import):
        assert transport._rtu_framer() is sentinel


def test_build_client_esphome_api_passes_connection_and_secrets() -> None:
    captured: dict = {}

    class FakeApiClient:
        def __init__(self, hass, **kwargs) -> None:
            captured["hass"] = hass
            captured.update(kwargs)

    with patch(
        "custom_components.modbus_usb.esphome_api_client.EsphomeApiModbusClient",
        FakeApiClient,
    ):
        transport.build_client({**API, CONF_RESPONSE_TIMEOUT: 2}, hass="HASS")
    assert captured["hass"] == "HASS"
    assert captured["host"] == "10.0.0.7"
    assert captured["port"] == 6053
    assert captured["encryption_key"] == "c2VjcmV0"
    assert captured["password"] is None
    assert captured["service_name"] == "modbus_send"
    assert captured["event_type"] == "esphome.modbus_rx"
    assert captured["timeout"] == 2.0
    assert captured["retries"] == 1


def test_probe_connection_serial_reports_open_result() -> None:
    class FakeSerial:
        def __init__(self, **kwargs) -> None:
            self.closed = False

        def connect(self) -> bool:
            return True

        def close(self) -> None:
            self.closed = True

    with patch("pymodbus.client.ModbusSerialClient", FakeSerial):
        result = transport.probe_connection(SERIAL)
    assert result["reachable"] is True
    assert result["latency_ms"] is not None
    assert result["error"] is None
    assert result["transport"] == TRANSPORT_SERIAL


def test_probe_connection_tcp_uses_plain_socket_and_classifies_errors() -> None:
    class Sock:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    with patch("socket.create_connection", return_value=Sock()) as create:
        result = transport.probe_connection(TCP, timeout=1.0)
    create.assert_called_once_with(("Modbus-Bridge.local", 8899), timeout=1.0)
    assert result["reachable"] is True

    with patch("socket.create_connection", side_effect=OSError("refused")):
        result = transport.probe_connection(TCP)
    assert result["reachable"] is False
    assert result["error"] == "refused"
    assert result["error_key"] == "cannot_connect"


def test_probe_connection_api_surfaces_device_info_and_auth_errors() -> None:
    class FakeApi:
        def __init__(self, hass, **kwargs) -> None:
            self.device_info = {"name": "modbus-bridge"}
            self.last_error = None

        def connect(self) -> bool:
            return True

        def close(self) -> None:
            pass

    class FailingApi(FakeApi):
        def connect(self) -> bool:
            self.last_error = "ESPHome API rejected the credentials (bad key)"
            return False

    target = "custom_components.modbus_usb.esphome_api_client.EsphomeApiModbusClient"
    with patch(target, FakeApi):
        result = transport.probe_connection(API, hass=object())
    assert result["reachable"] and result["esphome"] == {"name": "modbus-bridge"}
    with patch(target, FailingApi):
        result = transport.probe_connection(API, hass=object())
    assert result["reachable"] is False
    assert result["error_key"] == "invalid_auth"

    class NoService(FakeApi):
        def connect(self) -> bool:
            self.last_error = "device has no 'modbus_send' API service (found: none)"
            return False

    with patch(target, NoService):
        assert transport.probe_connection(API)["error_key"] == "service_missing"
