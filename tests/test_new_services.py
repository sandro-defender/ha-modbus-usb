"""Tests for the v2.5.0 automation services and their coordinator support.

Covers modbus_usb.batch_write, modbus_usb.boost_polling, and
modbus_usb.reset_circuit_breaker plus the circuit breaker reset, batch
write, polling boost, raw-word read, and latency-stage coordinator APIs.
"""

from __future__ import annotations

import struct
from collections import deque
from datetime import timedelta
from threading import Lock
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
import voluptuous as vol
import yaml
from homeassistant.exceptions import HomeAssistantError

from custom_components.modbus_usb.circuit_breaker import (
    STATE_HEALTHY,
    STATE_OFFLINE,
    SlaveCircuitBreaker,
)
from custom_components.modbus_usb.const import (
    DIAG_CONSECUTIVE_FAILURES,
    DIAG_FAILED_READS,
    DIAG_LAST_ERROR,
    DIAG_LAST_SUCCESS,
    DIAG_TOTAL_READS,
    DOMAIN,
)
from custom_components.modbus_usb.coordinator import ModbusUsbCoordinator
from custom_components.modbus_usb.services import (
    BATCH_WRITE_SCHEMA,
    BOOST_POLLING_SCHEMA,
    RESET_CIRCUIT_BREAKER_SCHEMA,
    async_register_services,
    async_unregister_services,
)

ENTRY_ID = "test-entry"


# ─────────────────────────── fakes & helpers ───────────────────────────


class _Response:
    def __init__(self, registers=None, bits=None, error=False) -> None:
        self.registers = registers or []
        self.bits = bits or []
        self._error = error

    def isError(self) -> bool:
        return self._error


class CountingLock:
    """A lock wrapper that counts acquisitions to prove single-lock batches."""

    def __init__(self) -> None:
        self._lock = Lock()
        self.count = 0

    def __enter__(self):
        self.count += 1
        return self._lock.__enter__()

    def __exit__(self, *args):
        return self._lock.__exit__(*args)

    def locked(self) -> bool:
        return self._lock.locked()


class _BatchClient:
    connected = True
    port = "/dev/ttyUSB0"

    def __init__(self) -> None:
        self.holding_writes: list[tuple[int, int]] = []
        self.multi_writes: list[tuple[int, list[int]]] = []
        self.coil_writes: list[tuple[int, bool]] = []
        self.fail_next_write = False

    def connect(self) -> bool:
        return True

    def close(self) -> None:
        self.connected = False

    def _maybe_fail(self):
        if self.fail_next_write:
            self.fail_next_write = False
            return _Response(error=True)
        return None

    def write_register(self, address, value, *, slave):
        failed = self._maybe_fail()
        if failed:
            return failed
        self.holding_writes.append((address, value))
        return _Response()

    def write_registers(self, address, values, *, slave):
        failed = self._maybe_fail()
        if failed:
            return failed
        self.multi_writes.append((address, list(values)))
        return _Response()

    def write_coil(self, address, value, *, slave):
        failed = self._maybe_fail()
        if failed:
            return failed
        self.coil_writes.append((address, bool(value)))
        return _Response()

    def read_holding_registers(self, address, count=1, *, slave):
        return _Response(registers=list(range(100, 100 + count)))

    def read_input_registers(self, address, count=1, *, slave):
        return _Response(registers=[7] * count)

    def read_coils(self, address, count=1, *, slave):
        return _Response(bits=[True] * count)

    def read_discrete_inputs(self, address, count=1, *, slave):
        return _Response(bits=[False] * count)


def _coordinator(client=None) -> ModbusUsbCoordinator:
    coordinator = object.__new__(ModbusUsbCoordinator)
    coordinator.client = client or _BatchClient()
    coordinator.slave_id = 1
    coordinator.entry_id = ENTRY_ID
    coordinator._serial_lock = CountingLock()
    coordinator.transaction_log = deque(maxlen=200)
    coordinator.diag = {
        DIAG_TOTAL_READS: 0,
        DIAG_FAILED_READS: 0,
        DIAG_CONSECUTIVE_FAILURES: 0,
        DIAG_LAST_ERROR: None,
        DIAG_LAST_SUCCESS: None,
    }
    coordinator.circuit_breaker = SlaveCircuitBreaker()
    coordinator.update_interval = timedelta(seconds=10)
    return coordinator


class FakeServiceRegistry:
    def __init__(self) -> None:
        self.registered: dict[str, tuple] = {}

    def has_service(self, domain, service):
        return service in self.registered

    def async_register(self, domain, service, handler, schema=None):
        self.registered[service] = (handler, schema)

    def async_remove(self, domain, service):
        self.registered.pop(service, None)


def _hass(coordinator=None):
    domain_data = {}
    if coordinator is not None:
        domain_data[ENTRY_ID] = coordinator
    return SimpleNamespace(
        data={DOMAIN: domain_data},
        services=FakeServiceRegistry(),
        bus=SimpleNamespace(async_fire=Mock()),
        async_add_executor_job=AsyncMock(side_effect=lambda func, *args: func(*args)),
    )


async def _register(hass):
    await async_register_services(hass)
    return hass.services.registered


def _call(handler, data):
    return handler(SimpleNamespace(data=data))


# ─────────────────────────────── schemas ────────────────────────────────


def test_batch_write_schema_accepts_and_rejects() -> None:
    valid = BATCH_WRITE_SCHEMA(
        {
            "entry_id": ENTRY_ID,
            "writes": [
                {"address": 0, "value": 1},
                {"address": 5, "value": 1, "register_type": "coil"},
                {"address": 10, "value": 230.4, "data_type": "float32"},
            ],
            "slave_id": 3,
        }
    )
    assert len(valid["writes"]) == 3
    assert valid["writes"][0]["register_type"] == "holding"
    assert valid["writes"][0]["data_type"] == "uint16"

    with pytest.raises(vol.Invalid):
        BATCH_WRITE_SCHEMA({"entry_id": ENTRY_ID, "writes": []})
    with pytest.raises(vol.Invalid):
        BATCH_WRITE_SCHEMA(
            {"entry_id": ENTRY_ID, "writes": [{"address": 70000, "value": 1}]}
        )
    with pytest.raises(vol.Invalid):
        BATCH_WRITE_SCHEMA(
            {
                "entry_id": ENTRY_ID,
                "writes": [{"address": 0, "value": 1, "register_type": "input"}],
            }
        )
    with pytest.raises(vol.Invalid):
        BATCH_WRITE_SCHEMA(
            {
                "entry_id": ENTRY_ID,
                "writes": [{"address": i, "value": 1} for i in range(124)],
            }
        )


def test_boost_and_reset_schemas_validate_bounds() -> None:
    boost = BOOST_POLLING_SCHEMA({"entry_id": ENTRY_ID, "duration": 60})
    assert boost["scan_interval"] == 1
    with pytest.raises(vol.Invalid):
        BOOST_POLLING_SCHEMA({"entry_id": ENTRY_ID, "duration": 2})
    with pytest.raises(vol.Invalid):
        BOOST_POLLING_SCHEMA({"entry_id": ENTRY_ID, "duration": 60, "scan_interval": 0})
    with pytest.raises(vol.Invalid):
        BOOST_POLLING_SCHEMA(
            {"entry_id": ENTRY_ID, "duration": 60, "scan_interval": 4000}
        )

    reset_all = RESET_CIRCUIT_BREAKER_SCHEMA({"entry_id": ENTRY_ID})
    assert "slave_id" not in reset_all
    with pytest.raises(vol.Invalid):
        RESET_CIRCUIT_BREAKER_SCHEMA({"entry_id": ENTRY_ID, "slave_id": 248})


# ─────────────────────────── registration ───────────────────────────────


async def test_registers_all_five_services_once() -> None:
    hass = _hass()
    services = await _register(hass)
    assert set(services) == {
        "read_register",
        "write_register",
        "batch_write",
        "boost_polling",
        "reset_circuit_breaker",
    }
    first_handler = services["batch_write"][0]
    await async_register_services(hass)  # idempotent: handlers stay registered once
    assert hass.services.registered["batch_write"][0] is first_handler

    await async_unregister_services(hass)
    assert hass.services.registered == {}
    await async_unregister_services(hass)  # double-unregister is safe


def test_services_yaml_documents_every_service() -> None:
    import os

    path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "custom_components",
        "modbus_usb",
        "services.yaml",
    )
    with open(path, encoding="utf-8") as file:
        documented = yaml.safe_load(file)
    assert set(documented) == {
        "read_register",
        "write_register",
        "batch_write",
        "boost_polling",
        "reset_circuit_breaker",
    }
    for service in documented.values():
        assert "entry_id" in service["fields"]


def test_manifest_version_is_260() -> None:
    import json
    import os

    path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "custom_components",
        "modbus_usb",
        "manifest.json",
    )
    with open(path, encoding="utf-8") as file:
        assert json.load(file)["version"] == "2.6.0"


# ───────────────────────────── batch_write ──────────────────────────────


async def test_batch_write_service_calls_coordinator_and_refreshes() -> None:
    coordinator = Mock(spec=ModbusUsbCoordinator)
    coordinator.batch_write.return_value = {"slave_id": 1, "written": 2, "results": []}
    coordinator.async_request_refresh = AsyncMock()
    hass = _hass(coordinator)
    services = await _register(hass)

    handler, _ = services["batch_write"]
    await _call(
        handler,
        {
            "entry_id": ENTRY_ID,
            "writes": [{"address": 0, "value": 1}, {"address": 1, "value": 2}],
            "slave_id": 3,
        },
    )
    coordinator.batch_write.assert_called_once()
    writes, slave = coordinator.batch_write.call_args[0]
    assert len(writes) == 2
    assert slave == 3
    coordinator.async_request_refresh.assert_awaited_once()


async def test_batch_write_service_raises_home_assistant_error() -> None:
    coordinator = Mock(spec=ModbusUsbCoordinator)
    coordinator.batch_write.side_effect = TimeoutError("no response")
    hass = _hass(coordinator)
    services = await _register(hass)

    handler, _ = services["batch_write"]
    with pytest.raises(HomeAssistantError, match="batch write failed"):
        await _call(
            handler, {"entry_id": ENTRY_ID, "writes": [{"address": 0, "value": 1}]}
        )


async def test_batch_write_unknown_entry_raises() -> None:
    hass = _hass()
    services = await _register(hass)
    handler, _ = services["batch_write"]
    with pytest.raises(HomeAssistantError, match="No Modbus USB hub"):
        await _call(
            handler, {"entry_id": "missing", "writes": [{"address": 0, "value": 1}]}
        )


def test_coordinator_batch_write_holds_lock_for_whole_batch() -> None:
    client = _BatchClient()
    coordinator = _coordinator(client)

    result = coordinator.batch_write(
        [
            {"address": 0, "value": 11},
            {"address": 1, "value": True, "register_type": "coil"},
            {"address": 2, "value": -1, "data_type": "int16"},
        ],
        slave=4,
    )

    assert coordinator._serial_lock.count == 1, "batch must own one lock acquisition"
    assert result["written"] == 3
    assert result["slave_id"] == 4
    assert client.holding_writes == [(0, 11), (2, 0xFFFF)]
    assert client.coil_writes == [(1, True)]
    entry = coordinator.transaction_log[0]
    assert entry["operation"] == "batch_write"
    assert entry["count"] == 3
    assert entry["status"] == "ok"
    assert entry["slave"] == 4


def test_coordinator_batch_write_encodes_32bit_values() -> None:
    client = _BatchClient()
    coordinator = _coordinator(client)

    coordinator.batch_write(
        [
            {"address": 10, "value": 230.4, "data_type": "float32"},
            {"address": 20, "value": 70000, "data_type": "uint32"},
        ]
    )
    expected_float = list(struct.unpack(">HH", struct.pack(">f", 230.4)))
    expected_uint = list(struct.unpack(">HH", struct.pack(">I", 70000)))
    assert client.multi_writes == [(10, expected_float), (20, expected_uint)]
    assert client.holding_writes == []


def test_coordinator_batch_write_aborts_on_first_failure() -> None:
    client = _BatchClient()
    coordinator = _coordinator(client)

    original_write = client.write_register

    calls = {"n": 0}

    def failing_second(address, value, *, slave):
        calls["n"] += 1
        if calls["n"] == 2:
            return _Response(error=True)
        return original_write(address, value, slave=slave)

    client.write_register = failing_second

    with pytest.raises(Exception):
        coordinator.batch_write(
            [{"address": 0, "value": 1}, {"address": 1, "value": 2}]
        )

    assert client.holding_writes == [(0, 1)], "second write must not be applied"
    entry = coordinator.transaction_log[0]
    assert entry["status"] == "error"
    assert coordinator.diag[DIAG_FAILED_READS] == 1


def test_coordinator_batch_write_rejects_input_registers_and_empty() -> None:
    coordinator = _coordinator()
    with pytest.raises(ValueError, match="at least one"):
        coordinator.batch_write([])
    with pytest.raises(ValueError, match="holding registers and coils"):
        coordinator.batch_write([{"address": 0, "value": 1, "register_type": "input"}])


# ──────────────────────────── boost_polling ─────────────────────────────


def test_coordinator_polling_boost_apply_and_restore() -> None:
    coordinator = _coordinator()
    assert coordinator.update_interval == timedelta(seconds=10)
    assert coordinator.boost_state() == {"active": False}

    state = coordinator.apply_polling_boost(2, 60)
    assert coordinator.update_interval == timedelta(seconds=2)
    assert state["active"] is True
    assert state["scan_interval"] == 2.0
    assert 55 <= state["remaining_seconds"] <= 60

    coordinator.restore_polling_boost()
    assert coordinator.update_interval == timedelta(seconds=10)
    assert coordinator.boost_state() == {"active": False}


def test_coordinator_polling_boost_stacking_keeps_original_interval() -> None:
    coordinator = _coordinator()
    coordinator.apply_polling_boost(5, 60)
    coordinator.apply_polling_boost(1, 120)
    assert coordinator.update_interval == timedelta(seconds=1)
    coordinator.restore_polling_boost()
    assert coordinator.update_interval == timedelta(seconds=10)


def test_coordinator_diagnostics_report_boost() -> None:
    coordinator = _coordinator()
    coordinator.serial_config = {}
    coordinator.scan_progress = {"active": False}
    coordinator.apply_polling_boost(1, 30)
    diagnostics = coordinator.get_diagnostics()
    assert diagnostics["boost"]["active"] is True
    coordinator.restore_polling_boost()
    assert coordinator.get_diagnostics()["boost"]["active"] is False


async def test_boost_polling_service_schedules_restore_and_refreshes() -> None:
    coordinator = Mock(spec=ModbusUsbCoordinator)
    coordinator.apply_polling_boost.return_value = {"active": True}
    coordinator.boost_state.side_effect = [{"active": False}]
    coordinator.restore_polling_boost = Mock()
    coordinator.async_request_refresh = AsyncMock()
    hass = _hass(coordinator)
    services = await _register(hass)

    with patch("homeassistant.helpers.event.async_call_later") as call_later:
        handler, _ = services["boost_polling"]
        await _call(handler, {"entry_id": ENTRY_ID, "duration": 60, "scan_interval": 2})

    coordinator.apply_polling_boost.assert_called_once_with(2, 60.0)
    call_later.assert_called_once()
    assert call_later.call_args[0][0] is hass
    assert call_later.call_args[0][1] == 60.0
    coordinator.async_request_refresh.assert_awaited_once()

    # The scheduled callback restores only when the boost window has ended.
    restore_callback = call_later.call_args[0][2]
    restore_callback(None)
    coordinator.restore_polling_boost.assert_called_once()


async def test_boost_polling_extended_window_skips_early_restore() -> None:
    coordinator = Mock(spec=ModbusUsbCoordinator)
    coordinator.apply_polling_boost.return_value = {"active": True}
    coordinator.boost_state.side_effect = [{"active": True}, {"active": False}]
    coordinator.restore_polling_boost = Mock()
    coordinator.async_request_refresh = AsyncMock()
    hass = _hass(coordinator)
    services = await _register(hass)

    callbacks = []
    with patch(
        "homeassistant.helpers.event.async_call_later",
        side_effect=lambda hass_arg, delay, callback: callbacks.append(callback),
    ):
        handler, _ = services["boost_polling"]
        await _call(handler, {"entry_id": ENTRY_ID, "duration": 30})
        await _call(handler, {"entry_id": ENTRY_ID, "duration": 90})

    callbacks[0](None)  # earlier timer fires while the boost was extended
    coordinator.restore_polling_boost.assert_not_called()
    callbacks[1](None)  # final timer fires after the extended window
    coordinator.restore_polling_boost.assert_called_once()


# ───────────────────────── reset_circuit_breaker ────────────────────────


def test_circuit_breaker_reset_single_slave() -> None:
    breaker = SlaveCircuitBreaker()
    for _ in range(4):
        breaker.record_failure(5, "timeout")
    assert breaker.get_state(5).state == STATE_OFFLINE
    assert breaker.should_poll(5) is False

    assert breaker.reset(5) == [5]
    state = breaker.get_state(5)
    assert state.state == STATE_HEALTHY
    assert state.consecutive_failures == 0
    assert state.last_error is None
    assert breaker.should_poll(5) is True
    assert breaker.get_summary()[5]["state"] == STATE_HEALTHY


def test_circuit_breaker_reset_all_and_unknown_slave() -> None:
    breaker = SlaveCircuitBreaker()
    breaker.record_failure(2, "x")
    breaker.record_failure(2, "x")
    breaker.record_failure(7, "y")
    assert sorted(breaker.reset()) == [2, 7]
    assert all(
        item["state"] == STATE_HEALTHY for item in breaker.get_summary().values()
    )
    assert breaker.reset(99) == []


async def test_reset_circuit_breaker_service_targets_slave_or_all() -> None:
    coordinator = _coordinator()
    for _ in range(4):
        coordinator.circuit_breaker.record_failure(6, "timeout")
    hass = _hass(coordinator)
    services = await _register(hass)

    handler, _ = services["reset_circuit_breaker"]
    await _call(handler, {"entry_id": ENTRY_ID, "slave_id": 6})
    assert coordinator.circuit_breaker.get_state(6).state == STATE_HEALTHY

    coordinator.circuit_breaker.record_failure(8, "timeout")
    coordinator.circuit_breaker.record_failure(8, "timeout")
    await _call(handler, {"entry_id": ENTRY_ID})
    assert coordinator.circuit_breaker.get_state(8).state == STATE_HEALTHY


async def test_reset_circuit_breaker_without_breaker_raises() -> None:
    coordinator = Mock(spec=ModbusUsbCoordinator, circuit_breaker=None)
    hass = _hass(coordinator)
    services = await _register(hass)
    handler, _ = services["reset_circuit_breaker"]
    with pytest.raises(HomeAssistantError, match="circuit breaker"):
        await _call(handler, {"entry_id": ENTRY_ID})


# ───────────────────── coordinator support for the panels ───────────────


def test_transactions_record_latency_waterfall_stages() -> None:
    coordinator = _coordinator()
    coordinator.read_register_raw(7, "holding", "uint16", 3)
    entry = coordinator.transaction_log[0]
    assert entry["duration_ms"] is not None
    latency = entry["latency"]
    assert set(latency) >= {"lock_wait_ms", "connect_ms", "request_ms"}
    assert all(value >= 0 for value in latency.values())


async def test_api_registers_inspector_and_designer_commands() -> None:
    from custom_components.modbus_usb import api

    api._API_REGISTERED = False
    hass = SimpleNamespace(data={}, http=None)
    try:
        await api.async_register_api(hass)
        commands = hass.data["websocket_api"]
        assert "modbus_usb/traffic_inspector" in commands
        assert "modbus_usb/subscribe_traffic" in commands
        assert "modbus_usb/designer_validate" in commands
        assert "modbus_usb/save_template" in commands
        assert "modbus_usb/save_and_apply_template" in commands
    finally:
        api._API_REGISTERED = False


def test_read_raw_words_returns_words_and_bits() -> None:
    coordinator = _coordinator()
    assert coordinator.read_raw_words(0, "holding", 2, 1) == [100, 101]
    assert coordinator.read_raw_words(0, "input", 3, 1) == [7, 7, 7]
    assert coordinator.read_raw_words(0, "coil", 2, 1) == [1, 1]
    assert coordinator.read_raw_words(0, "discrete", 2, 1) == [0, 0]
    assert coordinator.transaction_log[0]["operation"] == "read_discrete"
    with pytest.raises(ValueError):
        coordinator.read_raw_words(0, "bogus", 1, 1)
