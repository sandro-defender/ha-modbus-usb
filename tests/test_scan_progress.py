"""v2.9.0: bus-scan hardening — cancellation, live progress, client restore."""

from __future__ import annotations

import asyncio
from collections import deque
from threading import Lock
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from custom_components.modbus_usb import coordinator as coord_mod
from custom_components.modbus_usb.coordinator import (
    ModbusUsbCoordinator,
    scan_progress_signal,
)

pytestmark = pytest.mark.fast

SERIAL_CONFIG = {
    "transport": "serial",
    "port": "/dev/ttyUSB0",
    "baudrate": 9600,
    "bytesize": 8,
    "parity": "N",
    "stopbits": 1,
}


class _MainClient:
    def __init__(self, connected: bool = True) -> None:
        self._connected = connected
        self.connect_calls = 0
        self.close_calls = 0

    @property
    def connected(self) -> bool:
        return self._connected

    def connect(self) -> None:
        self.connect_calls += 1
        self._connected = True

    def close(self) -> None:
        self.close_calls += 1
        self._connected = False


class _Response:
    def __init__(self, registers: list) -> None:
        self.registers = registers

    def isError(self) -> bool:
        return False

    def __str__(self) -> str:
        return f"ReadResponse(registers={self.registers})"


class _ProbeClient:
    """Fake probe client: counts probes, can cancel or raise."""

    def __init__(self, behavior: dict | None = None) -> None:
        self.behavior = behavior or {}
        self.connected = False
        self.probes = 0
        self.closed = False

    def connect(self) -> bool:
        if (
            self.behavior.get("raise_connect")
            and self.behavior.get("client_index", 0) == 0
        ):
            raise RuntimeError("probe connect boom")
        self.connected = True
        return True

    def close(self) -> None:
        self.connected = False
        self.closed = True

    def read_holding_registers(self, address: int, count: int, *, slave: int):
        self.probes += 1
        if slave == 1 and self.behavior.get("answer_slave_1"):
            return _Response([1])
        cancel_after = self.behavior.get("cancel_after")
        if cancel_after and self.probes >= cancel_after:
            self.behavior["coordinator"].cancel_scan()
        raise TimeoutError("no response received")


def _scan_coordinator(
    main_client: _MainClient,
    hass=None,
    resolved_path: str | None = None,
) -> ModbusUsbCoordinator:
    coordinator = object.__new__(ModbusUsbCoordinator)
    coordinator.client = main_client
    coordinator.serial_config = dict(SERIAL_CONFIG)
    coordinator._serial_lock = Lock()
    coordinator.transaction_log = deque(maxlen=200)
    coordinator.diag = {
        "total_reads": 0,
        "failed_reads": 0,
        "consecutive_failures": 0,
        "last_error": None,
        "last_success": None,
    }
    coordinator.entry_id = "hub1"
    coordinator.hass = hass
    coordinator._resolve_adapter_port = Mock(
        return_value=SimpleNamespace(
            path=resolved_path,
            source="identity",
            ambiguous=False,
            candidates=[],
        )
    )
    return coordinator


def _run_scan(
    coordinator,
    probe_clients: list[_ProbeClient],
    baudrates=(9600,),
    parities=("N",),
    start=1,
    end=3,
):
    captured_configs: list[dict] = []

    def factory(config, **kwargs):
        captured_configs.append(dict(config))
        return probe_clients.pop(0)

    with patch.object(coord_mod, "build_client", factory):
        result = coordinator.scan_bus(list(baudrates), list(parities), start, end)
    return result, captured_configs


def test_full_scan_completes_with_cancelled_false():
    main = _MainClient(connected=True)
    coordinator = _scan_coordinator(main)
    probe = _ProbeClient()
    result, _ = _run_scan(coordinator, [probe], end=3)

    assert result["cancelled"] is False
    assert result["found"] == []
    assert result["probed"] == 3
    assert probe.probes == 3
    assert probe.closed is True
    # the hub connection is restored after the scan
    assert main.close_calls == 1
    assert main.connect_calls == 1
    assert main.connected is True
    assert coordinator.scan_progress["active"] is False
    assert coordinator.scan_progress["cancelled"] is False


def test_stop_between_probes_returns_partial_findings():
    main = _MainClient(connected=True)
    coordinator = _scan_coordinator(main)
    behavior = {"cancel_after": 2, "answer_slave_1": True, "coordinator": coordinator}
    probe = _ProbeClient(behavior)
    result, _ = _run_scan(coordinator, [probe], end=5)

    assert result["cancelled"] is True
    assert result["probed"] == 2
    assert probe.probes == 2  # the third slave was never probed
    assert len(result["found"]) == 1
    assert result["found"][0]["slave_id"] == 1
    assert result["found"][0]["baudrate"] == 9600
    # and the hub connection is restored even on a cancelled scan
    assert main.close_calls == 1
    assert main.connect_calls == 1
    assert main.connected is True
    assert coordinator.scan_progress["cancelled"] is True


def test_probe_raise_still_restores_connected_hub_client():
    main = _MainClient(connected=True)
    coordinator = _scan_coordinator(main)
    probe = _ProbeClient({"raise_connect": True, "client_index": 0})
    with pytest.raises(RuntimeError, match="probe connect boom"):
        _run_scan(coordinator, [probe, _ProbeClient()], end=2)
    assert main.close_calls == 1
    assert main.connect_calls == 1
    assert main.connected is True
    assert coordinator.scan_progress["active"] is False


def test_probe_raise_leaves_disconnected_hub_client_disconnected():
    main = _MainClient(connected=False)
    coordinator = _scan_coordinator(main)
    probe = _ProbeClient({"raise_connect": True, "client_index": 0})
    with pytest.raises(RuntimeError, match="probe connect boom"):
        _run_scan(coordinator, [probe, _ProbeClient()], end=2)
    assert main.close_calls == 1
    assert main.connect_calls == 0  # was not open → not reconnected
    assert main.connected is False


def test_probe_uses_resolved_port_path():
    main = _MainClient(connected=True)
    stable = "/dev/serial/by-id/usb-FTDI_TEST-0001"
    coordinator = _scan_coordinator(main, resolved_path=stable)
    probe = _ProbeClient()
    result, configs = _run_scan(coordinator, [probe], end=1)
    assert result["cancelled"] is False
    assert configs[0]["port"] == stable
    assert coordinator.serial_config["port"] == "/dev/ttyUSB0"  # not mutated


def test_progress_events_dispatched_on_entry_signal():
    main = _MainClient(connected=True)
    events: list[tuple[str, dict]] = []
    loop = asyncio.new_event_loop()
    hass = SimpleNamespace(loop=loop)
    coordinator = _scan_coordinator(main, hass=hass)
    probe = _ProbeClient(
        {"cancel_after": 2, "answer_slave_1": True, "coordinator": coordinator}
    )

    def fake_send(hass_, signal, *args):
        events.append((signal, dict(args[0])))

    try:
        with patch.object(coord_mod, "async_dispatcher_send", fake_send):
            result, _ = _run_scan(coordinator, [probe], end=4)
        for _ in range(16):
            loop.run_until_complete(asyncio.sleep(0))
    finally:
        loop.close()

    assert result["cancelled"] is True
    # The scan_found record also announces on the traffic signal — keep both,
    # but assert on the scan-progress stream.
    assert scan_progress_signal("hub1") in {signal for signal, _ in events}
    scan_events = [
        payload for signal, payload in events if signal == scan_progress_signal("hub1")
    ]
    assert scan_events, "scan progress must be announced on the dispatcher"
    first, last = scan_events[0], scan_events[-1]
    for payload in (first, last):
        for key in ("slave", "total", "baudrate", "parity", "found_so_far"):
            assert key in payload
    assert first["active"] is True
    assert first["total"] == 4
    assert last["active"] is False
    assert last["cancelled"] is True
    assert last["found_so_far"] == 1


def test_scan_progress_snapshot_includes_found_so_far():
    coordinator = _scan_coordinator(_MainClient())
    coordinator.scan_progress = {
        "active": True,
        "completed": 5,
        "total": 20,
        "found": 1,
        "slave": 5,
        "baudrate": 9600,
        "parity": "N",
        "cancelled": False,
    }
    snapshot = coordinator.scan_progress_snapshot()
    assert snapshot["found_so_far"] == 1
    assert snapshot["slave"] == 5
    assert snapshot["baudrate"] == 9600
