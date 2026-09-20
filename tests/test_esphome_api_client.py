"""Tests for the ESPHome native-API Modbus client (fake aioesphomeapi)."""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from typing import Any

import pytest
from pymodbus.exceptions import ConnectionException, ModbusIOException

from custom_components.modbus_usb.esphome_api_client import (
    EsphomeApiModbusClient,
    response_like,
)
from custom_components.modbus_usb.esphome_bridge import append_crc, frame_to_hex

pytestmark = pytest.mark.fast


class FakeApi:
    """Stand-in for ``aioesphomeapi.APIClient`` driven by the test."""

    instances: list[FakeApi] = []

    def __init__(self, host, port, password, *, noise_psk=None, client_info=""):
        self.host, self.port, self.password = host, port, password
        self.noise_psk, self.client_info = noise_psk, client_info
        self.connect_error: Exception | None = None
        self.services = [SimpleNamespace(name="modbus_send", key=7)]
        self.executed: list[tuple[Any, dict]] = []
        self.on_stop = None
        self.callback = None
        self.disconnected = 0
        self.responder: Any = None  # callable(frame) -> reply frame | None
        self.info = SimpleNamespace(
            name="modbus-bridge",
            friendly_name="Modbus Bridge",
            mac_address="AA:BB",
            esphome_version="2025.9.0",
            model="esp32dev",
            manufacturer="Espressif",
        )
        FakeApi.instances.append(self)

    async def connect(self, *, login=False, on_stop=None):
        if self.connect_error is not None:
            raise self.connect_error
        self.on_stop = on_stop

    async def device_info(self):
        return self.info

    async def list_entities_services(self):
        return [], list(self.services)

    def subscribe_service_calls(self, callback):
        self.callback = callback

    async def execute_service(self, service, data):
        self.executed.append((service, data))
        if self.responder is not None:
            reply = self.responder(bytes(data["data"]))
            if reply is not None:
                self.emit(reply)

    async def disconnect(self, force=False):
        self.disconnected += 1

    def emit(self, frame: bytes | str, *, service: str = "esphome.modbus_rx") -> None:
        payload = frame if isinstance(frame, str) else frame_to_hex(frame)
        self.callback(
            SimpleNamespace(
                is_event=True,
                service=service,
                data={"frame": payload, "device": "modbus-bridge"},
            )
        )


@pytest.fixture
def loop_thread():
    """A real event loop running in a background thread, like HA's."""
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    yield loop
    loop.call_soon_threadsafe(loop.stop)
    thread.join(timeout=2)
    loop.close()


@pytest.fixture
def client(loop_thread):
    FakeApi.instances.clear()
    hass = SimpleNamespace(loop=loop_thread)
    return EsphomeApiModbusClient(
        hass,
        host="10.0.0.7",
        encryption_key="key==",
        timeout=0.2,
        retries=1,
        api_client_factory=FakeApi,
    )


def _reply(hex_payload: str) -> bytes:
    return append_crc(bytes.fromhex(hex_payload.replace(" ", "")))


def test_connect_verifies_service_and_exposes_device_info(client) -> None:
    assert not client.connected
    assert client.connect() is True
    assert client.connected
    api = FakeApi.instances[-1]
    assert (api.host, api.port, api.noise_psk) == ("10.0.0.7", 6053, "key==")
    assert api.callback is not None
    assert client.device_info["name"] == "modbus-bridge"
    assert client.device_info["esphome_version"] == "2025.9.0"
    # Connecting again is idempotent (no second APIClient).
    assert client.connect() is True
    assert len(FakeApi.instances) == 1
    client.close()
    assert not client.connected and api.disconnected == 1


def test_connect_fails_when_service_missing(client) -> None:
    class NoService(FakeApi):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.services = [SimpleNamespace(name="restart", key=1)]

    client._api_client_factory = NoService
    assert client.connect() is False
    assert "'modbus_send' API service" in client.last_error
    assert "restart" in client.last_error
    assert FakeApi.instances[-1].disconnected == 1


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (type("InvalidEncryptionKeyAPIError", (Exception,), {})("bad"), "credentials"),
        (type("RequiresEncryptionAPIError", (Exception,), {})(), "encryption key"),
        (TimeoutError(), "Timed out"),
        (OSError("unreachable"), "connection failed (unreachable)"),
    ],
)
def test_connect_describes_errors(client, error, expected) -> None:
    class Failing(FakeApi):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.connect_error = error

    client._api_client_factory = Failing
    assert client.connect() is False
    assert expected in client.last_error


def test_read_holding_registers_round_trip(client) -> None:
    assert client.connect()
    api = FakeApi.instances[-1]
    api.responder = lambda frame: _reply("01 03 04 00 2A 12 34")
    result = client.read_holding_registers(0x13, count=2, slave=1)
    assert response_like(result)
    assert result.registers == [42, 0x1234]
    assert not result.isError()
    service, data = api.executed[0]
    assert service.name == "modbus_send"
    assert data["data"][:6] == [1, 3, 0, 0x13, 0, 2]
    assert client.stats["requests"] == 1 and client.stats["responses"] == 1


def test_lazy_connect_and_all_functions(client) -> None:
    replies = {
        0x01: "01 01 01 05",
        0x02: "01 02 01 02",
        0x04: "01 04 02 00 07",
        0x05: "01 05 00 01 FF 00",
        0x06: "01 06 00 01 00 09",
        0x0F: "01 0F 00 01 00 02",
        0x10: "01 10 00 01 00 02",
    }

    def responder(frame: bytes):
        return _reply(replies[frame[1]])

    # First call connects implicitly.
    assert not client.connected
    original_factory = client._api_client_factory

    def factory(*args, **kwargs):
        api = original_factory(*args, **kwargs)
        api.responder = responder
        return api

    client._api_client_factory = factory
    assert client.read_coils(1, count=3, slave=1).bits == [True, False, True]
    assert client.connected
    assert client.read_discrete_inputs(1, count=2, slave=1).bits == [False, True]
    assert client.read_input_registers(1, count=1, slave=1).registers == [7]
    assert client.write_coil(1, True, slave=1).value is True
    assert client.write_register(1, 9, slave=1).value == 9
    assert client.write_coils(1, [True, False], slave=1).count == 2
    assert client.write_registers(1, [1, 2], slave=1).count == 2


def test_exception_response_is_returned_not_raised(client) -> None:
    assert client.connect()
    FakeApi.instances[-1].responder = lambda frame: _reply("01 83 02")
    result = client.read_holding_registers(500, count=1, slave=1)
    assert result.isError() and result.exception_code == 2


def test_timeout_retries_then_raises_modbus_io(client) -> None:
    assert client.connect()
    api = FakeApi.instances[-1]
    with pytest.raises(ModbusIOException, match="No response received"):
        client.read_holding_registers(0, count=1, slave=9)
    assert len(api.executed) == 2  # retries=1 → two attempts
    assert client.stats["timeouts"] == 2


def test_frames_for_other_slave_or_event_are_ignored(client) -> None:
    assert client.connect()
    api = FakeApi.instances[-1]

    def responder(frame: bytes):
        api.emit(_reply("02 03 02 00 01"))  # wrong slave
        api.emit(_reply("01 03 02 00 01"), service="esphome.other")  # wrong event
        api.emit("not hex")  # unparsable
        api.callback(SimpleNamespace(is_event=False, service="x", data={}))
        return _reply("01 03 02 00 63")

    api.responder = responder
    result = client.read_holding_registers(0, count=1, slave=1)
    assert result.registers == [99]


def test_bad_crc_reply_is_retried_and_reported(client) -> None:
    assert client.connect()
    api = FakeApi.instances[-1]
    bad = bytearray(_reply("01 03 02 00 63"))
    bad[-1] ^= 0xFF
    api.responder = lambda frame: bytes(bad)
    with pytest.raises(ModbusIOException, match="CRC mismatch"):
        client.read_holding_registers(0, count=1, slave=1)


def test_send_failure_marks_client_disconnected(client) -> None:
    assert client.connect()
    api = FakeApi.instances[-1]

    async def boom(service, data):
        raise OSError("socket closed")

    api.execute_service = boom
    with pytest.raises(ConnectionException, match="send failed"):
        client.read_holding_registers(0, count=1, slave=1)
    assert not client.connected


def test_unexpected_disconnect_bumps_reconnects_and_reconnects_lazily(
    client, loop_thread
) -> None:
    assert client.connect()
    api = FakeApi.instances[-1]
    asyncio.run_coroutine_threadsafe(api.on_stop(False), loop_thread).result(2)
    assert not client.connected
    assert client.stats["reconnects"] == 1
    assert client.last_error == "ESPHome API connection lost"
    # Next request reconnects with a fresh APIClient.
    original_factory = client._api_client_factory

    def factory(*args, **kwargs):
        new = original_factory(*args, **kwargs)
        new.responder = lambda frame: _reply("01 03 02 00 01")
        return new

    client._api_client_factory = factory
    assert client.read_holding_registers(0, count=1, slave=1).registers == [1]
    assert len(FakeApi.instances) == 2


def test_connect_failure_during_request_raises_connection_exception(client) -> None:
    class Failing(FakeApi):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.connect_error = OSError("nope")

    client._api_client_factory = Failing
    with pytest.raises(ConnectionException, match="Failed to connect"):
        client.read_holding_registers(0, count=1, slave=1)


def test_trace_packet_sees_tx_and_rx(client) -> None:
    assert client.connect()
    FakeApi.instances[-1].responder = lambda frame: _reply("01 03 02 00 01")
    seen: list[tuple[bool, bytes]] = []

    def tracer(sending: bool, data: bytes) -> bytes:
        seen.append((sending, bytes(data)))
        return data

    client.trace_packet = tracer
    client.read_holding_registers(0, count=1, slave=1)
    assert [sending for sending, _ in seen] == [True, False]
    assert seen[0][1][:2] == b"\x01\x03"
    assert seen[1][1] == _reply("01 03 02 00 01")


def test_missing_loop_reports_error(client) -> None:
    client.hass = SimpleNamespace()
    assert client.connect() is False
    assert "event loop" in client.last_error
