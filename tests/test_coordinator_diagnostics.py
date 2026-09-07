"""Regression tests for serial safety and diagnostic accounting."""
from __future__ import annotations

from collections import deque
from threading import Lock, Thread
from time import sleep
from pathlib import Path

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

    def read_holding_registers(self, address: int, count: int, *, slave: int) -> _Response:
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

    assert coordinator.read_register_raw(0, REGISTER_TYPE_HOLDING, DATA_TYPE_UINT16) == 42
    assert client.connect_calls == 1


def test_failed_connection_is_logged_and_counted() -> None:
    class FailingClient(_Client):
        def connect(self) -> bool:
            self.connect_calls += 1
            return False

    coordinator = _coordinator(FailingClient(connected=False))

    with pytest.raises(UpdateFailed, match="Could not open"):
        coordinator.read_register_raw(0, REGISTER_TYPE_HOLDING, DATA_TYPE_UINT16)

    assert coordinator.diag[DIAG_FAILED_READS] == 1
    assert coordinator.diag[DIAG_LAST_ERROR] == "Could not open the configured serial port"
    assert coordinator.transaction_log[0]["status"] == "error"


def test_read_uses_requested_slave_id() -> None:
    class RecordingClient(_Client):
        def read_holding_registers(self, address: int, count: int, *, slave: int) -> _Response:
            self.last_request = (address, count, slave)
            return super().read_holding_registers(address, count, slave=slave)

    client = RecordingClient()
    coordinator = _coordinator(client)
    coordinator._read_one({
        CONF_REGISTER_TYPE: REGISTER_TYPE_HOLDING,
        CONF_ADDRESS: 20,
        CONF_DATA_TYPE: DATA_TYPE_UINT16,
        CONF_SLAVE_ID: 9,
    })

    assert client.last_request == (20, 1, 9)


def test_read_supports_current_pymodbus_device_id_keyword() -> None:
    class ModernClient(_Client):
        def read_holding_registers(
            self, address: int, count: int, *, device_id: int
        ) -> _Response:
            self.last_device_id = device_id
            return _Response([99])

    client = ModernClient()
    coordinator = _coordinator(client)

    assert coordinator.read_register_raw(4, REGISTER_TYPE_HOLDING, DATA_TYPE_UINT16, 6) == 99
    assert client.last_device_id == 6


def test_serial_lock_prevents_overlapping_requests() -> None:
    class SlowClient(_Client):
        def __init__(self) -> None:
            super().__init__()
            self.active = 0
            self.maximum_active = 0

        def read_holding_registers(self, address: int, count: int, *, slave: int) -> _Response:
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
    template_path = Path(__file__).parents[1] / "custom_components/modbus_usb/templates/eletechsup-R413E16.yaml"
    template = yaml.safe_load(template_path.read_text(encoding="utf-8"))

    assert template["default_slave_id"] == 1
    assert len(template["entities"]) == 16
    assert [entity["address"] for entity in template["entities"]] == list(range(1, 17))
    assert all(entity["on_value"] == 0x0100 for entity in template["entities"])
    assert all(entity["off_value"] == 0x0200 for entity in template["entities"])
