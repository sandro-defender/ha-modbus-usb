"""v2.9.0: hub health entities (connected / error rate / reconnects)."""

from __future__ import annotations

import time
from collections import deque
from threading import Lock
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.sensor import SensorStateClass
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.modbus_usb.binary_sensor import HubConnectedSensor
from custom_components.modbus_usb.coordinator import ModbusUsbCoordinator
from custom_components.modbus_usb.sensor import HubErrorRateSensor, HubReconnectsSensor


def _coordinator(connected: bool = True, data=None, reconnects: int = 0):
    coordinator = Mock()
    coordinator.client = SimpleNamespace(connected=connected)
    coordinator.data = data
    coordinator.reconnect_count = reconnects
    coordinator.error_rate_percent = Mock(return_value=12.5)
    return coordinator


def _entry():
    return SimpleNamespace(entry_id="hub1", title="Hub", options={})


def test_connected_sensor_unique_id_state_and_device_class():
    for connected in (True, False):
        entity = HubConnectedSensor(_coordinator(connected=connected), _entry())
        assert entity.unique_id == "hub1_hub_connected"
        assert entity.is_on is connected
        assert entity.device_class == BinarySensorDeviceClass.CONNECTIVITY
        # entity lives on the hub device
        info = entity.device_info
        assert ("modbus_usb", "hub1") in info["identifiers"]
        assert info["name"] == "Hub"


def test_error_rate_sensor_reads_coordinator_window():
    coordinator = _coordinator(data={})
    coordinator.error_rate_percent = Mock(return_value=12.5)
    entity = HubErrorRateSensor(coordinator, _entry())
    assert entity.unique_id == "hub1_hub_error_rate"
    assert entity.native_value == 12.5
    assert entity.native_unit_of_measurement == "%"
    assert entity.state_class == SensorStateClass.MEASUREMENT
    assert ("modbus_usb", "hub1") in entity.device_info["identifiers"]
    # no coordinator data yet → unknown, not a number
    coordinator.data = None
    assert entity.native_value is None


def test_reconnects_sensor_total_increasing():
    coordinator = _coordinator(data={}, reconnects=7)
    entity = HubReconnectsSensor(coordinator, _entry())
    assert entity.unique_id == "hub1_hub_reconnects"
    assert entity.native_value == 7
    assert entity.state_class == SensorStateClass.TOTAL_INCREASING
    coordinator.data = None
    assert entity.native_value is None


def _minimal_coordinator(client):
    coordinator = object.__new__(ModbusUsbCoordinator)
    coordinator.client = client
    coordinator.slave_id = 1
    coordinator.entry_id = "hub1"
    coordinator._serial_lock = Lock()
    coordinator.transaction_log = deque(maxlen=200)
    coordinator.diag = {
        "total_reads": 0,
        "failed_reads": 0,
        "consecutive_failures": 0,
        "last_error": None,
        "last_success": None,
    }
    return coordinator


def test_error_rate_percent_empty_window_is_zero():
    coordinator = _minimal_coordinator(Mock(connected=True))
    assert coordinator.error_rate_percent() == 0.0


def test_record_transaction_feeds_error_rate_window():
    class _Response:
        def __init__(self, registers, error=False):
            self.registers = registers
            self._error = error

        def isError(self):
            return self._error

    class _Client:
        connected = True
        port = "/dev/ttyUSB0"

        def read_holding_registers(self, address, count, *, slave):
            return _Response([1])

    class _FailingClient(_Client):
        def read_holding_registers(self, address, count, *, slave):
            return _Response([], error=True)

    ok_coordinator = _minimal_coordinator(_Client())
    ok_coordinator.read_register_raw(0, "holding", "uint16")
    assert ok_coordinator.error_rate_percent() == 0.0
    assert len(ok_coordinator._tx_window) == 1

    bad_coordinator = _minimal_coordinator(_FailingClient())
    with pytest.raises(UpdateFailed):
        bad_coordinator.read_register_raw(0, "holding", "uint16")
    assert bad_coordinator.error_rate_percent() == 100.0
    mixed_coordinator = _minimal_coordinator(_Client())
    mixed_coordinator.read_register_raw(0, "holding", "uint16")
    mixed_coordinator.client = _FailingClient()
    with pytest.raises(UpdateFailed):
        mixed_coordinator.read_register_raw(0, "holding", "uint16")
    assert mixed_coordinator.error_rate_percent() == 50.0


def test_error_rate_window_prunes_samples_older_than_5_minutes():
    coordinator = _minimal_coordinator(Mock(connected=True))
    now = time.time()
    coordinator._tx_window = deque(
        [
            (now - 320, True),  # outside the 5-minute window
            (now - 60, False),
            (now - 30, True),
        ]
    )
    assert coordinator.error_rate_percent() == 50.0
    # a window holding only stale samples reports 0.0 (no samples in window)
    coordinator._tx_window = deque([(now - 320, False)])
    assert coordinator.error_rate_percent() == 0.0
