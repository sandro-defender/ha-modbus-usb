"""Regression tests for serial safety and diagnostic accounting."""

from __future__ import annotations

from collections import deque
from pathlib import Path
from threading import Lock, Thread
from time import sleep

import pytest
import yaml
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.modbus_usb.const import (
    CONF_ADDRESS,
    CONF_DATA_TYPE,
    CONF_REGISTER_TYPE,
    CONF_SLAVE_ID,
    DATA_TYPE_UINT16,
    DIAG_CONSECUTIVE_FAILURES,
    DIAG_FAILED_READS,
    DIAG_LAST_ERROR,
    DIAG_LAST_SUCCESS,
    DIAG_TOTAL_READS,
    REGISTER_TYPE_HOLDING,
)
from custom_components.modbus_usb.coordinator import ModbusUsbCoordinator


class _Response:
    def __init__(self, registers: list[int], error: bool = False) -> None:
        self.registers = registers
        self._error = error

    def isError(self) -> bool:
        return self._error


class _Client:
    def __init__(self, connected: bool = True, value: int = 42) -> None:
        self.connected = connected
        self.value = value
        self.connect_calls = 0

    def connect(self) -> bool:
        self.connect_calls += 1
        self.connected = True
        return True

    def close(self) -> None:
        self.connected = False

    def read_holding_registers(
        self, address: int, count: int, *, slave: int
    ) -> _Response:
        return _Response([self.value])


def _coordinator(client: _Client) -> ModbusUsbCoordinator:
    """Make a minimal coordinator without Home Assistant setup for unit tests."""
    coordinator = object.__new__(ModbusUsbCoordinator)
    coordinator.client = client
    coordinator.slave_id = 1
    coordinator.entry_id = "test-entry"
    coordinator._serial_lock = Lock()
    coordinator.transaction_log = deque(maxlen=200)
    coordinator.diag = {
        DIAG_TOTAL_READS: 0,
        DIAG_FAILED_READS: 0,
        DIAG_CONSECUTIVE_FAILURES: 0,
        DIAG_LAST_ERROR: None,
        DIAG_LAST_SUCCESS: None,
    }
    return coordinator


def test_direct_read_updates_health_and_log() -> None:
    coordinator = _coordinator(_Client(value=123))

    value = coordinator.read_register_raw(7, REGISTER_TYPE_HOLDING, DATA_TYPE_UINT16, 3)

    assert value == 123
    assert coordinator.diag[DIAG_TOTAL_READS] == 1
    assert coordinator.diag[DIAG_FAILED_READS] == 0
    assert coordinator.diag[DIAG_LAST_SUCCESS] is not None
    assert coordinator.transaction_log[0]["status"] == "ok"
    assert coordinator.transaction_log[0]["slave"] == 3
    assert coordinator.transaction_log[0]["address"] == 7


def test_disconnected_client_reconnects_before_read() -> None:
    client = _Client(connected=False)
    coordinator = _coordinator(client)

    assert (
        coordinator.read_register_raw(0, REGISTER_TYPE_HOLDING, DATA_TYPE_UINT16) == 42
    )
    assert client.connect_calls == 1


def test_failed_connection_is_logged_and_counted() -> None:
    class FailingClient(_Client):
        def connect(self) -> bool:
            self.connect_calls += 1
            return False

    coordinator = _coordinator(FailingClient(connected=False))

    with pytest.raises(UpdateFailed, match="Could not open"):
        coordinator.read_register_raw(0, REGISTER_TYPE_HOLDING, DATA_TYPE_UINT16)

    assert coordinator.client.connect_calls == 3
    assert coordinator.diag[DIAG_FAILED_READS] == 1
    assert (
        coordinator.diag[DIAG_LAST_ERROR] == "Could not open the configured serial port"
    )
    assert coordinator.transaction_log[0]["status"] == "error"


def test_read_uses_requested_slave_id() -> None:
    class RecordingClient(_Client):
        def read_holding_registers(
            self, address: int, count: int, *, slave: int
        ) -> _Response:
            self.last_request = (address, count, slave)
            return super().read_holding_registers(address, count, slave=slave)

    client = RecordingClient()
    coordinator = _coordinator(client)
    coordinator._read_one(
        {
            CONF_REGISTER_TYPE: REGISTER_TYPE_HOLDING,
            CONF_ADDRESS: 20,
            CONF_DATA_TYPE: DATA_TYPE_UINT16,
            CONF_SLAVE_ID: 9,
        }
    )

    assert client.last_request == (20, 1, 9)


def test_read_supports_current_pymodbus_device_id_keyword() -> None:
    class ModernClient(_Client):
        def read_holding_registers(
            self, address: int, *, count: int, device_id: int
        ) -> _Response:
            self.last_device_id = device_id
            self.last_count = count
            return _Response([99])

    client = ModernClient()
    coordinator = _coordinator(client)

    assert (
        coordinator.read_register_raw(4, REGISTER_TYPE_HOLDING, DATA_TYPE_UINT16, 6)
        == 99
    )
    assert client.last_device_id == 6
    assert client.last_count == 1


def test_serial_lock_prevents_overlapping_requests() -> None:
    class SlowClient(_Client):
        def __init__(self) -> None:
            super().__init__()
            self.active = 0
            self.maximum_active = 0

        def read_holding_registers(
            self, address: int, count: int, *, slave: int
        ) -> _Response:
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)
            sleep(0.02)
            self.active -= 1
            return _Response([address])

    client = SlowClient()
    coordinator = _coordinator(client)
    request = {
        CONF_REGISTER_TYPE: REGISTER_TYPE_HOLDING,
        CONF_DATA_TYPE: DATA_TYPE_UINT16,
        CONF_SLAVE_ID: 1,
    }
    threads = [
        Thread(target=coordinator._read_one, args=({**request, CONF_ADDRESS: address},))
        for address in (1, 2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert client.maximum_active == 1


def test_r413e16_template_has_16_command_switches() -> None:
    template_path = (
        Path(__file__).parents[1]
        / "custom_components/modbus_usb/templates/eletechsup-R413E16.yaml"
    )
    template = yaml.safe_load(template_path.read_text(encoding="utf-8"))

    assert 1 <= template["default_slave_id"] <= 247
    channels = [
        entity for entity in template["entities"] if not entity.get("addresses")
    ]
    assert len(channels) == 16
    assert [entity["address"] for entity in channels] == list(range(1, 17))
    assert all(entity["on_value"] == 0x0100 for entity in template["entities"])
    assert all(entity["off_value"] == 0x0200 for entity in template["entities"])

    # The optional group switch writes every channel and has no feedback
    # register of its own, so it must stay an assumed-state entity.
    combined = [entity for entity in template["entities"] if entity.get("addresses")]
    assert len(combined) == 1
    assert combined[0]["addresses"] == list(range(1, 17))
    assert combined[0]["assumed_state"] is True


def test_template_fingerprint_matches_only_expected_register_values() -> None:
    coordinator = _coordinator(_Client(value=230))
    templates = [
        {
            "id": "voltage_meter",
            "name": "Voltage Meter",
            "fingerprint": [
                {
                    CONF_REGISTER_TYPE: REGISTER_TYPE_HOLDING,
                    CONF_ADDRESS: 0,
                    CONF_DATA_TYPE: DATA_TYPE_UINT16,
                    "min_value": 200,
                    "max_value": 250,
                }
            ],
        },
        {
            "id": "other_device",
            "name": "Other Device",
            "fingerprint": [
                {
                    CONF_REGISTER_TYPE: REGISTER_TYPE_HOLDING,
                    CONF_ADDRESS: 0,
                    CONF_DATA_TYPE: DATA_TYPE_UINT16,
                    "min_value": 1,
                    "max_value": 100,
                }
            ],
        },
    ]

    assert coordinator._match_templates(coordinator.client, 1, templates) == [
        "Voltage Meter"
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Regression tests for configuration-tolerance helpers
# ─────────────────────────────────────────────────────────────────────────────


def test_number_entity_tolerates_null_numeric_settings() -> None:
    """A sidebar save can persist nulls; float(None) used to kill the platform."""
    from types import SimpleNamespace

    from homeassistant.components.number import NumberMode

    from custom_components.modbus_usb.number import ModbusUsbNumber

    class StubCoordinator:
        data = {}

        def async_add_listener(self, *args, **kwargs):
            return lambda: None

    entry = SimpleNamespace(options={}, entry_id="entry-1", title="Hub")
    ent = {
        "id": "n1",
        "entity_type": "number",
        "name": "Setpoint",
        "register_type": "holding",
        "address": 10,
        "min_value": None,
        "max_value": None,
        "step": None,
        "scale": None,
        "mode": "slider",
    }

    number = ModbusUsbNumber(StubCoordinator(), entry, ent)

    assert number.native_min_value == 0
    assert number.native_max_value == 65535
    assert number.native_step == 1
    assert number.mode is NumberMode.SLIDER
    assert number._scale == 1


def test_sensor_entity_tolerates_none_and_invalid_classes() -> None:
    from types import SimpleNamespace

    from custom_components.modbus_usb.sensor import ModbusUsbSensor

    class StubCoordinator:
        data = {}

        def async_add_listener(self, *args, **kwargs):
            return lambda: None

    entry = SimpleNamespace(options={}, entry_id="entry-1", title="Hub")
    ent = {
        "id": "s1",
        "entity_type": "sensor",
        "name": "Voltage",
        "register_type": "holding",
        "address": 0,
        "device_class": "none",
        "state_class": "measurement",
    }

    sensor = ModbusUsbSensor(StubCoordinator(), entry, ent)

    assert sensor.device_class is None
    assert sensor.state_class == "measurement"

    ent["device_class"] = "voltage"
    ent["state_class"] = "bogus"
    sensor = ModbusUsbSensor(StubCoordinator(), entry, ent)
    assert sensor.device_class == "voltage"
    assert sensor.state_class is None


# ─────────────────────────────────────────────────────────────────────────────
# R4D6F20 block reads must not swallow entities they do not cover
# ─────────────────────────────────────────────────────────────────────────────


class _BlockClient:
    def __init__(self) -> None:
        self.connected = True
        self.calls: list[tuple[int, int]] = []

    def connect(self) -> bool:
        return True

    def read_holding_registers(
        self, address: int, *, count: int, device_id: int
    ) -> _Response:
        self.calls.append((address, count))
        return _Response([0] * count)


class _EntryStub:
    def __init__(self, options: dict, entry_id: str = "test-entry") -> None:
        self.options = options
        self.entry_id = entry_id
        self.title = "Hub"
        self.data = {}


class _HassStub:
    def __init__(self, entry: _EntryStub) -> None:
        self.config_entries = self
        self._entry = entry

    def async_get_entry(self, entry_id: str):
        return self._entry


def test_r4d6f20_uncovered_entity_still_polled_individually() -> None:
    device = {
        "id": "d1",
        "model": "R4D6F20",
        "slave_id": 4,
        "device_controls": {"protocol": "eletechsup_r4d6f20"},
        "m0_short": False,
    }
    covered = {
        "id": "e1",
        "name": "CH-06",
        "entity_type": "switch",
        "device_id": "d1",
        "register_type": "holding",
        "data_type": "uint16",
        "address": 5,
    }
    uncovered = {
        "id": "e2",
        "name": "Custom float",
        "entity_type": "sensor",
        "device_id": "d1",
        "register_type": "holding",
        "data_type": "float32",
        "address": 0,
    }
    entry = _EntryStub({"devices": [device]})
    client = _BlockClient()
    coordinator = _coordinator(client)
    coordinator.hass = _HassStub(entry)

    data = coordinator._read_all([covered, uncovered], {})

    assert data["e1"] == 0
    assert data["e2"] == 0.0
    # The 20-register block read plus one individual 2-register float32 read.
    assert (0, 20) in client.calls
    assert (0, 2) in client.calls


def test_scan_bus_defaults_parity_when_serial_profile_lacks_it() -> None:
    client = _Client()
    coordinator = _coordinator(client)
    coordinator.serial_config = {
        "port": "/dev/null",
        "baudrate": 9600,
        "bytesize": 8,
        "stopbits": 1,
    }

    result = coordinator.scan_bus([9600])

    assert result["found"] == []
    assert coordinator.scan_progress["active"] is False


@pytest.mark.parametrize(("words", "data_type", "expected"), [
    ([65535], "uint16", 65535), ([65535], "int16", -1),
    ([65535, 65535], "uint32", 4294967295), ([65535, 65535], "int32", -1),
    ([0x414C, 0], "float32", 12.75),
])
def test_decode_register_types(words, data_type, expected):
    from custom_components.modbus_usb.coordinator import _decode_words

    assert _decode_words(words, data_type) == expected


@pytest.mark.parametrize(("words", "data_type"), [
    ([], "uint16"), ([0], "float32"), ([0x7FC0, 0], "float32"),
    ([0x7F80, 0], "float32"), ([0, 0], "unknown"),
])
def test_decode_rejects_short_nonfinite_or_unsupported_responses(words, data_type):
    from custom_components.modbus_usb.coordinator import _decode_words

    with pytest.raises(ValueError):
        _decode_words(words, data_type)


@pytest.mark.parametrize("value", [None, "", "garbage", "nan", "inf", "-inf"])
def test_float_config_fallback_is_finite(value):
    from custom_components.modbus_usb.coordinator import as_float

    assert as_float(value, 1) == 1


def test_runtime_type_error_never_retries_a_write():
    from unittest.mock import Mock

    client = Mock()
    client.write_register.side_effect = TypeError("invalid device_id at runtime")
    with pytest.raises(TypeError):
        ModbusUsbCoordinator._call_modbus_on_client(client, "write_register", 1, 256, slave=1)
    client.write_register.assert_called_once()


@pytest.mark.parametrize(("method", "args", "kwargs", "payload"), [
    ("read_holding_registers", (10,), {"count": 2}, b"\x00\x0a\x00\x02"),
    ("write_register", (10, 65535), {}, b"\x00\x0a\xff\xff"),
    ("write_coil", (10, True), {}, b"\x00\x0a\xff\x00"),
    ("write_registers", (10, [0x414C, 0]), {}, b"\x00\x0a\x00\x02\x04\x41\x4c\x00\x00"),
])
def test_installed_pymodbus_request_encoding(method, args, kwargs, payload):
    """Use real client methods/codecs, intercepting execution before serial I/O."""
    from unittest.mock import Mock
    from pymodbus.client import ModbusSerialClient

    client = ModbusSerialClient(port="/dev/null")
    client.execute = Mock(return_value=_Response([0]))
    ModbusUsbCoordinator._call_modbus_on_client(client, method, *args, slave=7, **kwargs)
    request = client.execute.call_args.args[-1]
    assert request.encode() == payload
    # Pymodbus changed the PDU unit-id attribute along with its call keyword.
    assert getattr(request, "dev_id", getattr(request, "slave_id", None)) == 7


@pytest.mark.parametrize(("value", "data_type", "expected"), [
    (12.75, "float32", [0x414C, 0]), (-1, "int32", [65535, 65535]),
    (4294967295, "uint32", [65535, 65535]),
])
def test_32bit_write_payload(value, data_type, expected):
    from unittest.mock import Mock

    client = _Client()
    client.write_registers = Mock(return_value=_Response([]))
    coordinator = _coordinator(client)
    coordinator.write_registers_32bit(10, value, data_type, 7)
    client.write_registers.assert_called_once_with(10, expected, device_id=7)
