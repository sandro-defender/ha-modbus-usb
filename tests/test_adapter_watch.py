"""v2.9.0: USB adapter hot-plug watch on the coordinator.

Covers the acceptance paths:
- adapter pulled mid-poll → explicit adapter_lost state, polling fails fast;
- no hammering: connect attempts return False while the watch owns recovery;
- recovery loop with exponential backoff (1s → 2s → 4s …) until the port
  reappears, then reopen on the new path and resume (reconnect_count,
  refresh request, identity persistence);
- ambiguous re-discovery stays in adapter_lost without guessing;
- repair issues (adapter_lost / serial_port_busy) raise once the condition
  persists (60 s in production; accelerated here) and clear on recovery;
- diagnostics "serial" dict gains the watch fields (identity redacted).
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from threading import Lock
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed
from serial import SerialException

from custom_components.modbus_usb import coordinator as coord_mod
from custom_components.modbus_usb.coordinator import (
    ModbusUsbCoordinator,
    PortResolution,
)

try:
    from homeassistant.helpers import issue_registry as ir
except ImportError:  # pragma: no cover
    ir = None


# ── fakes ─────────────────────────────────────────────────────────────────


class _FakeClient:
    """Serial-ish client: port attribute, connect/close, optional raise."""

    def __init__(
        self,
        connected: bool = True,
        read_error: Exception | None = None,
        connect_error: Exception | None = None,
    ) -> None:
        self.connected = connected
        self.port = "/dev/ttyUSB0"
        self.read_error = read_error
        self.connect_error = connect_error
        self.connect_calls = 0
        self.closed = 0

    def connect(self) -> bool:
        self.connect_calls += 1
        if self.connect_error is not None:
            raise self.connect_error
        self.connected = True
        return True

    def close(self) -> None:
        self.closed += 1
        self.connected = False

    def read_holding_registers(self, address, count, *, slave):
        if self.read_error is not None:
            raise self.read_error
        return SimpleNamespace(registers=[1], isError=lambda: False)


class _FakeBus:
    def async_listen_once(self, *args, **kwargs):
        return lambda: None

    def async_fire(self, *args, **kwargs):
        return None


class _FakeConfigEntry:
    def __init__(self, entry_id="e1") -> None:
        self.entry_id = entry_id
        self.data = {}
        self.options = {}


class _FakeConfigEntries:
    def __init__(self, entry: _FakeConfigEntry) -> None:
        self._entry = entry
        self.update_calls: list[dict] = []

    def async_get_entry(self, entry_id):
        return self._entry if entry_id == self._entry.entry_id else None

    def async_update_entry(self, entry, data=None, options=None) -> bool:
        self.update_calls.append({"data": data, "options": options})
        if data is not None:
            entry.data = dict(data)
        if options is not None:
            entry.options = dict(options)
        return True


class _FakeHass:
    """Event-loop-backed hass with just enough surface for the watch code."""

    def __init__(self, entry: _FakeConfigEntry) -> None:
        self.loop = None  # bound once the test's event loop is running
        self.data = {}
        self.bus = _FakeBus()
        self.config = SimpleNamespace(path=lambda p: f"/tmp/issue-test/{p}")
        self.state = "running"
        self.config_entries = _FakeConfigEntries(entry)
        self.refresh_requested = 0

    def async_create_task(self, coro):
        self.refresh_requested += 1
        coro.close()  # do not actually run refreshes in these unit tests
        return None

    async def async_add_executor_job(self, target, *args):
        return target(*args)


def _coordinator(client, entry, hass):
    coordinator = object.__new__(ModbusUsbCoordinator)
    coordinator.client = client
    coordinator.slave_id = 1
    coordinator.entry_id = entry.entry_id
    coordinator._serial_lock = Lock()
    coordinator.transaction_log = deque(maxlen=200)
    coordinator.scan_progress = {}
    coordinator.serial_config = {"transport": "serial", "port": client.port}
    coordinator.diag = {
        "total_reads": 0,
        "failed_reads": 0,
        "consecutive_failures": 0,
        "last_error": None,
        "last_success": None,
    }
    coordinator.hass = hass
    return coordinator


async def _body(entry, client, fn):
    """Run ``fn(coordinator, hass)`` inside a real event loop (fake hass)."""
    hass = _FakeHass(entry)
    hass.loop = asyncio.get_running_loop()
    coordinator = _coordinator(client, entry, hass)
    if ir is not None:
        hass.data[ir.DATA_REGISTRY] = ir.IssueRegistry(hass)
    return await fn(coordinator, hass)


def _run(entry, client, fn):
    return asyncio.run(_body(entry, client, fn))


async def _wait_until(check, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while not check():
        if time.monotonic() > deadline:
            return check()
        await asyncio.sleep(0.01)
    return True


@pytest.fixture()
def entry():
    return _FakeConfigEntry()


# ── 1. mid-poll unplug → adapter_lost, fail fast, no hammering ───────────


def test_poll_raises_serial_exception_enters_adapter_lost(entry):
    client = _FakeClient(read_error=SerialException("device reports readiness"))

    async def fn(coordinator, hass):
        with pytest.raises(SerialException):
            coordinator.read_register_raw(0, "holding", "uint16")
        assert coordinator._adapter_state == "adapter_lost"
        assert client.closed >= 1
        assert coordinator._adapter_lost_since is not None
        assert coordinator._adapter_reconnect_attempts == 0
        assert coordinator._adapter_next_retry_at is not None
        assert "device reports readiness" in coordinator._adapter_last_error
        # let the call_soon_threadsafe spawn run, then check the recovery task
        await asyncio.sleep(0)
        assert coordinator._adapter_recovery_task is not None
        assert not coordinator._adapter_recovery_task.done()
        coordinator._cancel_adapter_watch()

    _run(entry, client, fn)


def test_connect_with_retries_fails_fast_while_adapter_lost(entry):
    client = _FakeClient(connected=False)

    async def fn(coordinator, hass):
        coordinator._enter_adapter_lost(OSError("adapter gone"))
        started = client.connect_calls
        assert coordinator._connect_with_retries(attempts=3, base_delay=0.01) is False
        assert client.connect_calls == started  # no hammering
        with pytest.raises(UpdateFailed, match="USB adapter not found"):
            coordinator._ensure_connected()
        coordinator._cancel_adapter_watch()

    _run(entry, client, fn)


def test_adapter_gone_during_connect_hands_over_to_watch(entry):
    client = _FakeClient(connected=False, connect_error=SerialException("gone"))

    async def fn(coordinator, hass):
        assert coordinator._connect_with_retries(attempts=3, base_delay=0.01) is False
        assert coordinator._adapter_state == "adapter_lost"
        coordinator._cancel_adapter_watch()

    _run(entry, client, fn)


# ── 2. recovery loop: backoff → found on new index → reopen + resume ─────


def test_recovery_loop_recovers_on_new_tty_via_identity(entry):
    """The (re)plugged adapter reappears on a new ttyUSB index.

    The learned identity resolves it to its by-id path, the client is
    reopened there, the watch resets, and polling refresh is requested.
    """
    client = _FakeClient(connected=False)
    entry.data["adapter_identity"] = {
        "vid": "0x0403",
        "pid": "0x6015",
        "serial_number": "AB12CD34",
    }
    ports = [
        {
            "port": "/dev/ttyUSB3",
            "persistent_path": "/dev/serial/by-id/usb-FTDI_0042",
            "vid": "0x0403",
            "pid": "0x6015",
            "serial_number": "AB12CD34",
        }
    ]

    async def fn(coordinator, hass):
        with patch.object(coord_mod, "list_port_records", lambda: ports):
            coordinator._enter_adapter_lost(OSError("pulled"))
            # keep every re-discovery check immediate (fast test)
            deadline = time.monotonic() + 5
            while coordinator._adapter_state == "adapter_lost":
                assert time.monotonic() < deadline, "recovery timed out"
                coordinator._adapter_next_retry_at = time.monotonic() - 1
                await asyncio.sleep(0.01)
        assert coordinator._adapter_state == "ok"
        assert client.port == "/dev/serial/by-id/usb-FTDI_0042"
        assert coordinator._adapter_resolved_from == "identity"
        assert coordinator.reconnect_count == 1
        assert coordinator._adapter_reconnect_attempts == 0
        assert coordinator._adapter_lost_since is None
        assert hass.refresh_requested == 1
        coordinator._cancel_adapter_watch()

    _run(entry, client, fn)


def test_recovery_loop_learns_and_persists_identity_when_new(entry):
    """No stored identity → fallback to the single ttyUSB → identity
    learned and persisted to entry.data."""
    client = _FakeClient(connected=False)
    ports = [
        {
            "port": "/dev/ttyUSB2",
            "persistent_path": None,
            "vid": "0x1ABC",
            "pid": "0x4DEF",
            "serial_number": "NEW99",
        }
    ]

    async def fn(coordinator, hass):
        with patch.object(coord_mod, "list_port_records", lambda: ports):
            coordinator._enter_adapter_lost(OSError("pulled"))
            deadline = time.monotonic() + 5
            while coordinator._adapter_state == "adapter_lost":
                assert time.monotonic() < deadline, "recovery timed out"
                coordinator._adapter_next_retry_at = time.monotonic() - 1
                await asyncio.sleep(0.01)
        assert coordinator._adapter_state == "ok"
        assert coordinator._adapter_resolved_from == "fallback"
        assert coordinator._adapter_identity == {
            "vid": "0x1ABC",
            "pid": "0x4DEF",
            "serial_number": "NEW99",
            "hwid": None,
        }
        # persisted via async_update_entry onto the config entry
        assert hass.config_entries.update_calls
        assert entry.data["adapter_identity"]["serial_number"] == "NEW99"
        coordinator._cancel_adapter_watch()

    _run(entry, client, fn)


def test_recovery_loop_backoff_delays_follow_schedule(entry):
    """Each failed check bumps attempts; next retry grows 2s → 4s → 8s."""
    client = _FakeClient(connected=False)

    async def fn(coordinator, hass):
        coordinator._enter_adapter_lost(OSError("pulled"))
        # stop the auto-spawned recovery task; we drive iterations manually
        await asyncio.sleep(0)
        coordinator._adapter_recovery_task.cancel()
        await asyncio.sleep(0)
        coordinator._resolve_adapter_port = lambda configured=None: PortResolution(
            None, "none"
        )
        observed = []
        for expected_attempt in (1, 2, 3):
            # make the next check immediate, run one recovery-loop iteration
            coordinator._adapter_next_retry_at = time.monotonic() - 1
            step = asyncio.ensure_future(coordinator._adapter_recovery_loop())
            await _wait_until(
                lambda n=expected_attempt: coordinator._adapter_reconnect_attempts >= n
            )
            # the loop is now sleeping on the backoff delay
            observed.append(coordinator._adapter_next_retry_at - time.monotonic())
            step.cancel()
            await asyncio.sleep(0)
        assert coordinator._adapter_reconnect_attempts == 3
        assert coordinator._adapter_state == "adapter_lost"
        assert observed[0] == pytest.approx(2.0, abs=0.3)
        assert observed[1] == pytest.approx(4.0, abs=0.3)
        assert observed[2] == pytest.approx(8.0, abs=0.3)
        coordinator._cancel_adapter_watch()

    _run(entry, client, fn)


def test_recovery_loop_ambiguous_stays_lost_without_guessing(entry):
    client = _FakeClient(connected=False)

    async def fn(coordinator, hass):
        coordinator._enter_adapter_lost(OSError("pulled"))
        # stop the auto-spawned recovery task; drive one iteration manually
        await asyncio.sleep(0)
        coordinator._adapter_recovery_task.cancel()
        await asyncio.sleep(0)
        coordinator._resolve_adapter_port = lambda configured=None: PortResolution(
            None, "ambiguous", True, ("/dev/ttyUSB0", "/dev/ttyUSB1")
        )
        coordinator._adapter_next_retry_at = time.monotonic() - 1
        step = asyncio.ensure_future(coordinator._adapter_recovery_loop())
        await _wait_until(lambda: coordinator._adapter_reconnect_attempts >= 1)
        assert coordinator._adapter_state == "adapter_lost"
        assert client.connected is False
        assert client.connect_calls == 0
        step.cancel()
        await asyncio.sleep(0)
        coordinator._cancel_adapter_watch()

    _run(entry, client, fn)


# ── 3. repair issues ──────────────────────────────────────────────────────


@pytest.mark.skipif(ir is None, reason="issue registry unavailable")
def test_repair_issue_adapter_lost_raises_and_clears(entry):
    client = _FakeClient(connected=False)

    async def fn(coordinator, hass):
        issue_id = f"serial_adapter_lost_{entry.entry_id}"
        coordinator._enter_adapter_lost(OSError("pulled"))
        await asyncio.sleep(0)  # let the scheduled spawns run
        # accelerate the 60 s production timer
        coordinator._repair_issue_tasks["serial_adapter_lost"].cancel()
        await asyncio.sleep(0)
        coordinator._schedule_repair_issue("serial_adapter_lost", delay_s=0.01)
        assert await _wait_until(
            lambda: ir.async_get(hass).async_get_issue("modbus_usb", issue_id)
            is not None
        )
        issue = ir.async_get(hass).async_get_issue("modbus_usb", issue_id)
        assert issue.translation_key == "serial_adapter_lost"
        assert "/dev/ttyUSB0" in issue.translation_placeholders["port"]

        # recovery clears the issue
        coordinator._note_adapter_recovered(PortResolution("/dev/ttyUSB0", "identity"))
        assert await _wait_until(
            lambda: ir.async_get(hass).async_get_issue("modbus_usb", issue_id) is None
        )
        coordinator._cancel_adapter_watch()

    _run(entry, client, fn)


@pytest.mark.skipif(ir is None, reason="issue registry unavailable")
def test_repair_issue_port_busy_raises_and_clears(entry):
    client = _FakeClient(connected=False)

    async def fn(coordinator, hass):
        issue_id = f"serial_port_busy_{entry.entry_id}"
        coordinator._ensure_adapter_watch_attrs()
        coordinator._port_busy_since = time.monotonic()
        coordinator._schedule_repair_issue("serial_port_busy", delay_s=0.01)
        assert await _wait_until(
            lambda: ir.async_get(hass).async_get_issue("modbus_usb", issue_id)
            is not None
        )
        issue = ir.async_get(hass).async_get_issue("modbus_usb", issue_id)
        assert issue.translation_key == "serial_port_busy"
        assert "/dev/ttyUSB0" in issue.translation_placeholders["port"]

        coordinator._port_busy_since = None
        await asyncio.sleep(0.02)  # let any pending watch observe the clear
        coordinator._clear_repair_issue("serial_port_busy")
        assert await _wait_until(
            lambda: ir.async_get(hass).async_get_issue("modbus_usb", issue_id) is None
        )

    _run(entry, client, fn)


@pytest.mark.skipif(ir is None, reason="issue registry unavailable")
def test_repair_issue_not_raised_when_recovered_before_timer(entry):
    client = _FakeClient(connected=False)

    async def fn(coordinator, hass):
        issue_id = f"serial_adapter_lost_{entry.entry_id}"
        coordinator._enter_adapter_lost(OSError("pulled"))
        await asyncio.sleep(0)  # let the scheduled spawns run
        # recover long before the (accelerated) timer fires
        coordinator._repair_issue_tasks["serial_adapter_lost"].cancel()
        await asyncio.sleep(0)
        coordinator._schedule_repair_issue("serial_adapter_lost", delay_s=0.05)
        coordinator._note_adapter_recovered(PortResolution("/dev/ttyUSB0", "identity"))
        await asyncio.sleep(0.1)
        assert ir.async_get(hass).async_get_issue("modbus_usb", issue_id) is None
        coordinator._cancel_adapter_watch()

    _run(entry, client, fn)


# ── 4. diagnostics + identity adoption + reconfigure reset ───────────────


def test_diagnostics_serial_gains_watch_fields_with_redacted_identity(entry):
    client = _FakeClient(connected=False)

    async def fn(coordinator, hass):
        coordinator.note_adapter_identity(
            {"vid": "0x0403", "pid": "0x6015", "serial_number": "AB12CD34"}
        )
        coordinator._enter_adapter_lost(OSError("pulled"))
        serial = coordinator.get_diagnostics()["serial"]
        assert serial["state"] == "adapter_lost"
        assert serial["resolved_from"] == "configured"
        assert serial["adapter_identity"]["serial_number"] == "***CD34"
        assert serial["adapter_identity"]["vid"] == "0x0403"
        assert serial["lost_since"] is not None
        assert serial["reconnect_attempts"] == 0
        assert serial["next_retry_in_s"] is not None
        assert serial["last_error"]
        assert serial["reconnects_total"] == 0
        assert serial["port_busy"] is False
        coordinator._cancel_adapter_watch()

    _run(entry, client, fn)


def test_note_adapter_identity_adopts_entry_data(entry):
    client = _FakeClient()

    async def fn(coordinator, hass):
        coordinator.note_adapter_identity(
            {"vid": "0x0403", "pid": "0x6015", "serial_number": "SN1"}
        )
        assert coordinator._stored_adapter_identity() == {
            "vid": "0x0403",
            "pid": "0x6015",
            "serial_number": "SN1",
            "hwid": None,
        }
        # invalid records are ignored
        coordinator.note_adapter_identity({"vid": ""})
        assert coordinator._adapter_identity is None

    _run(entry, client, fn)


def test_stored_identity_falls_back_to_entry_data(entry):
    client = _FakeClient()
    entry.data["adapter_identity"] = {
        "vid": "0x0403",
        "pid": "0x6015",
        "serial_number": "SN9",
    }

    async def fn(coordinator, hass):
        assert coordinator._stored_adapter_identity()["serial_number"] == "SN9"

    _run(entry, client, fn)


def test_reconfigure_serial_resets_watch(entry):
    client = _FakeClient(connected=False)

    async def fn(coordinator, hass):
        coordinator._enter_adapter_lost(OSError("pulled"))
        await asyncio.sleep(0)  # let the recovery task spawn
        recovery_task = coordinator._adapter_recovery_task
        assert recovery_task is not None and not recovery_task.done()

        replacement = _FakeClient(connected=False)
        replacement.port = "/dev/ttyUSB5"
        with patch.object(
            coord_mod, "build_client", lambda config, hass=None: replacement
        ):
            ok = coordinator.reconfigure_serial(
                {
                    "transport": "serial",
                    "port": "/dev/ttyUSB5",
                    "baudrate": 115200,
                    "bytesize": 8,
                    "parity": "N",
                    "stopbits": 1,
                    "timeout": 0.5,
                }
            )
        assert ok is True
        await asyncio.sleep(0)  # let the cancelled task complete
        assert coordinator._adapter_state == "ok"
        assert recovery_task.done()  # cancelled
        assert coordinator.client is replacement
        assert coordinator.client.port == "/dev/ttyUSB5"
        assert replacement.connected is True
        coordinator._cancel_adapter_watch()

    _run(entry, client, fn)


def test_esphome_transport_unaffected_by_watch(entry):
    """Non-serial transports never enter the adapter watch."""
    client = _FakeClient(read_error=SerialException("boom"))
    client.port = "hub.local:6053"

    async def fn(coordinator, hass):
        coordinator.serial_config = {
            "transport": "esphome_api",
            "host": "hub.local",
            "api_port": 6053,
        }
        with pytest.raises(SerialException):
            coordinator.read_register_raw(0, "holding", "uint16")
        assert getattr(coordinator, "_adapter_state", "ok") == "ok"
        assert client.closed == 0  # the watch must not close the ESPHome client

    _run(entry, client, fn)


def test_close_cancels_watch_tasks(entry):
    client = _FakeClient(connected=False)

    async def fn(coordinator, hass):
        coordinator._enter_adapter_lost(OSError("pulled"))
        await asyncio.sleep(0)  # let the recovery task spawn
        assert coordinator._adapter_recovery_task is not None
        coordinator.close()
        await asyncio.sleep(0)
        assert coordinator._repair_issue_tasks == {}
        assert coordinator._adapter_recovery_task.done()

    _run(entry, client, fn)
