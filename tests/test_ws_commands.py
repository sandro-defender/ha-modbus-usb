"""Tests for the v2.6.0 WebSocket commands.

Covers the live Traffic Inspector subscription (modbus_usb/subscribe_traffic)
and the Template Designer one-step save & apply
(modbus_usb/save_and_apply_template), including the shared apply helper
extracted from ws_apply_template.
"""

from __future__ import annotations

from collections import deque
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.modbus_usb.api import diagnostics as api_diagnostics
from custom_components.modbus_usb.api import templates as api_templates
from custom_components.modbus_usb.const import (
    DATA_PRESERVE_SERIAL_RELOAD,
    DOMAIN,
)
from custom_components.modbus_usb.coordinator import traffic_signal
from custom_components.modbus_usb.diagnostics import modbus_crc16
from custom_components.modbus_usb.templates import template_filename_from_draft

pytestmark = pytest.mark.fast


def _unwrap(func):
    """Return the raw handler under websocket_api/require_admin decorators."""
    while hasattr(func, "__wrapped__"):
        func = func.__wrapped__
    return func


def _frame(payload_hex: str) -> str:
    payload = bytes.fromhex(payload_hex.replace(" ", ""))
    crc = modbus_crc16(payload)
    frame = payload + bytes((crc & 0xFF, crc >> 8))
    return " ".join(f"{byte:02X}" for byte in frame)


def _connection() -> SimpleNamespace:
    return SimpleNamespace(
        send_result=Mock(),
        send_error=Mock(),
        send_message=Mock(),
        subscriptions={},
    )


class _FakeEntry:
    def __init__(self, entry_id: str = "e1", options: dict | None = None) -> None:
        self.entry_id = entry_id
        self.options = (
            options if options is not None else {"devices": [], "entities": []}
        )


def _hass(entry: _FakeEntry | None) -> SimpleNamespace:
    return SimpleNamespace(
        data={DOMAIN: {}},
        config_entries=SimpleNamespace(
            async_get_entry=lambda entry_id: (
                entry if entry and entry.entry_id == entry_id else None
            ),
            async_update_entry=Mock(),
        ),
    )


SAVED_TEMPLATE = {
    "id": "fingerprint_meter",
    "filename": "fingerprint_meter.yaml",
    "name": "Fingerprint Meter",
    "manufacturer": "Generic",
    "model": "Modbus Device",
    "default_slave_id": 2,
    "description": "",
    "image": "",
    "info_url": "",
    "device_controls": {},
    "fingerprint": [],
    "entities": [
        {
            "name": "Voltage",
            "entity_type": "sensor",
            "register_type": "input",
            "address": 0,
            "data_type": "float32",
        },
        {
            "name": "Relay",
            "entity_type": "switch",
            "register_type": "coil",
            "address": 5,
        },
    ],
    "raw_yaml": "",
}

DRAFT_YAML = """
name: Fingerprint Meter
default_slave_id: 2
entities:
  - name: Voltage
    entity_type: sensor
    register_type: input
    address: 0
    data_type: float32
  - name: Relay
    entity_type: switch
    register_type: coil
    address: 5
"""


# ───────────────────────── modbus_usb/subscribe_traffic ─────────────────────────


def test_traffic_signal_is_entry_scoped() -> None:
    assert traffic_signal("abc123") == f"{DOMAIN}_abc123_traffic"


async def test_ws_subscribe_traffic_streams_analyzed_transactions() -> None:
    coordinator = SimpleNamespace(entry_id="e1", capture_hook="trace_packet")
    hass = SimpleNamespace(data={DOMAIN: {"e1": coordinator}})
    connection = _connection()
    captured: dict = {}

    def fake_connect(hass_obj, signal, callback):
        captured["signal"] = signal
        captured["callback"] = callback

        def unsubscribe() -> None:
            captured["disconnected"] = True

        return unsubscribe

    with patch.object(api_diagnostics, "async_dispatcher_connect", fake_connect):
        await _unwrap(api_diagnostics.ws_subscribe_traffic)(
            hass,
            connection,
            {"id": 7, "type": "modbus_usb/subscribe_traffic", "entry_id": "e1"},
        )

    assert captured["signal"] == traffic_signal("e1")
    result = connection.send_result.call_args[0][1]
    assert result["subscribed"] is True
    assert result["capture"] == {"hook": "trace_packet", "supported": True}
    assert 7 in connection.subscriptions

    # The coordinator announces one recorded transaction on the signal.
    captured["callback"](
        {
            "timestamp": "2026-09-20T12:00:00+00:00",
            "operation": "read_holding",
            "slave": 1,
            "address": 7,
            "count": 1,
            "status": "ok",
            "function_code": "0x03",
            "request_hex": _frame("010300070001"),
            "request_captured": True,
            "response_hex": _frame("010302002A"),
            "duration_ms": 12.5,
            "latency": {"request_ms": 12.3},
        }
    )
    message = connection.send_message.call_args[0][0]
    assert message["type"] == "event"
    assert message["id"] == 7
    analyzed = message["event"]["transaction"]
    assert analyzed["frame"]["frame_kind"] == "read_request"
    assert analyzed["response_frame"]["frame_kind"] == "read_response"
    assert analyzed["response_frame"]["crc_valid"] is True
    assert analyzed["transaction"]["request_captured"] is True

    # HA removes the subscription when the WebSocket connection closes.
    connection.subscriptions[7]()
    assert captured["disconnected"] is True


async def test_ws_subscribe_traffic_streams_multi_frame_responses() -> None:
    """v2.7.0: the live stream decodes the full RX stream per transaction."""
    coordinator = SimpleNamespace(entry_id="e1", capture_hook="trace_packet")
    hass = SimpleNamespace(data={DOMAIN: {"e1": coordinator}})
    connection = _connection()
    captured: dict = {}

    def fake_connect(hass_obj, signal, callback):
        captured["callback"] = callback

        def unsubscribe() -> None:
            captured["disconnected"] = True

        return unsubscribe

    with patch.object(api_diagnostics, "async_dispatcher_connect", fake_connect):
        await _unwrap(api_diagnostics.ws_subscribe_traffic)(
            hass,
            connection,
            {"id": 11, "type": "modbus_usb/subscribe_traffic", "entry_id": "e1"},
        )

    first = _frame("02040400010002")
    second = _frame("020302002A")
    captured["callback"](
        {
            "timestamp": "2026-09-20T12:00:01+00:00",
            "operation": "read_input",
            "slave": 2,
            "address": 0,
            "count": 4,
            "status": "ok",
            "function_code": "0x04",
            "request_hex": _frame("020400000004"),
            "request_captured": True,
            "response_hex": second,
            "response_frames": [first, second],
            "duration_ms": 41.0,
        }
    )
    message = connection.send_message.call_args[0][0]
    analyzed = message["event"]["transaction"]
    # The last frame stays under the legacy key; the full stream is a list.
    assert analyzed["response_frame"]["frame_kind"] == "read_response"
    assert analyzed["transaction"]["response_frames"] == [first, second]
    assert [frame["raw_hex"] for frame in analyzed["response_frames"]] == [
        first,
        second,
    ]
    assert analyzed["response_frames"][0]["values"] == [0x1, 0x2]


async def test_ws_traffic_inspector_returns_multi_frame_responses() -> None:
    """v2.7.0: the inspector view carries the decoded RX frame list."""
    from custom_components.modbus_usb.coordinator import ModbusUsbCoordinator

    async def run_in_executor(func, *args):
        return func(*args)

    coordinator = ModbusUsbCoordinator.__new__(ModbusUsbCoordinator)
    coordinator.entry_id = "e1"
    coordinator.slave_id = 1
    coordinator.client = SimpleNamespace(connected=True)
    first = _frame("02040400010002")
    second = _frame("020302002A")
    coordinator.transaction_log = deque(
        [
            {
                "timestamp": "2026-09-20T12:00:01+00:00",
                "operation": "read_input",
                "slave": 2,
                "address": 0,
                "count": 4,
                "status": "ok",
                "request_hex": _frame("020400000004"),
                "request_captured": True,
                "response_hex": second,
                "response_frames": [first, second],
                "duration_ms": 41.0,
            }
        ]
    )
    coordinator.capture_hook = "trace_packet"
    hass = SimpleNamespace(
        data={DOMAIN: {"e1": coordinator}},
        async_add_executor_job=run_in_executor,
    )
    connection = _connection()
    await _unwrap(api_diagnostics.ws_traffic_inspector)(
        hass,
        connection,
        {
            "id": 12,
            "type": "modbus_usb/traffic_inspector",
            "entry_id": "e1",
            "limit": 100,
        },
    )
    view = connection.send_result.call_args[0][1]
    analyzed = view["transactions"][0]
    assert analyzed["transaction"]["response_frames"] == [first, second]
    assert len(analyzed["response_frames"]) == 2
    assert view["stats"]["responses"] == 1


async def test_ws_subscribe_traffic_unknown_entry() -> None:
    hass = SimpleNamespace(data={DOMAIN: {}})
    connection = _connection()
    await _unwrap(api_diagnostics.ws_subscribe_traffic)(
        hass,
        connection,
        {"id": 1, "type": "modbus_usb/subscribe_traffic", "entry_id": "nope"},
    )
    connection.send_error.assert_called_once()
    assert connection.send_error.call_args[0][1] == "not_found"
    assert connection.subscriptions == {}


# ───────────────────── modbus_usb/save_and_apply_template ─────────────────────


async def test_ws_save_and_apply_creates_device_and_entities() -> None:
    entry = _FakeEntry()
    hass = _hass(entry)
    connection = _connection()
    save_mock = AsyncMock(return_value=SAVED_TEMPLATE)

    with patch.object(api_templates, "async_save_template", save_mock):
        await _unwrap(api_templates.ws_save_and_apply_template)(
            hass,
            connection,
            {
                "id": 3,
                "type": "modbus_usb/save_and_apply_template",
                "entry_id": "e1",
                "content": DRAFT_YAML,
                "filename": "fingerprint_meter.yaml",
            },
        )

    save_mock.assert_awaited_once_with(hass, "fingerprint_meter.yaml", DRAFT_YAML)
    result = connection.send_result.call_args[0][1]
    assert result["success"] is True
    assert result["filename"] == "fingerprint_meter.yaml"
    assert result["template_id"] == "fingerprint_meter"
    assert result["added_count"] == 2
    assert result["device_id"].startswith("dev_")

    updated = hass.config_entries.async_update_entry.call_args
    new_options = updated.kwargs["options"]
    assert len(new_options["devices"]) == 1
    device = new_options["devices"][0]
    assert device["name"] == "Fingerprint Meter"
    assert device["slave_id"] == 2  # template default_slave_id
    assert len(new_options["entities"]) == 2
    assert all(ent["device_id"] == device["id"] for ent in new_options["entities"])
    assert entry.entry_id in hass.data[DOMAIN][DATA_PRESERVE_SERIAL_RELOAD]


async def test_ws_save_and_apply_derives_filename_from_name() -> None:
    entry = _FakeEntry()
    hass = _hass(entry)
    connection = _connection()
    save_mock = AsyncMock(return_value=SAVED_TEMPLATE)

    with patch.object(api_templates, "async_save_template", save_mock):
        await _unwrap(api_templates.ws_save_and_apply_template)(
            hass,
            connection,
            {
                "id": 4,
                "type": "modbus_usb/save_and_apply_template",
                "entry_id": "e1",
                "content": DRAFT_YAML,
            },
        )

    assert save_mock.await_args.args[1] == "fingerprint_meter.yaml"


async def test_ws_save_and_apply_targets_existing_device() -> None:
    entry = _FakeEntry(
        options={
            "devices": [{"id": "dev_existing", "name": "Bench", "slave_id": 9}],
            "entities": [],
        }
    )
    hass = _hass(entry)
    connection = _connection()

    with patch.object(
        api_templates, "async_save_template", AsyncMock(return_value=SAVED_TEMPLATE)
    ):
        await _unwrap(api_templates.ws_save_and_apply_template)(
            hass,
            connection,
            {
                "id": 5,
                "type": "modbus_usb/save_and_apply_template",
                "entry_id": "e1",
                "content": DRAFT_YAML,
                "device_id": "dev_existing",
                "selected_entities": ["Voltage"],
                "address_offset": 10,
            },
        )

    result = connection.send_result.call_args[0][1]
    assert result["device_id"] == "dev_existing"
    assert result["added_count"] == 1
    new_options = hass.config_entries.async_update_entry.call_args.kwargs["options"]
    assert len(new_options["devices"]) == 1  # no new device created
    added = new_options["entities"][0]
    assert added["name"] == "Voltage"
    assert added["slave_id"] == 9  # inherited from the existing device
    assert added["address"] == 10  # 0 + address_offset


async def test_ws_save_and_apply_rejects_invalid_content() -> None:
    entry = _FakeEntry()
    hass = _hass(entry)
    connection = _connection()
    save_mock = AsyncMock(return_value=SAVED_TEMPLATE)

    with patch.object(api_templates, "async_save_template", save_mock):
        await _unwrap(api_templates.ws_save_and_apply_template)(
            hass,
            connection,
            {
                "id": 6,
                "type": "modbus_usb/save_and_apply_template",
                "entry_id": "e1",
                "content": "name: [unclosed",
            },
        )

    connection.send_error.assert_called_once()
    assert connection.send_error.call_args[0][1] == "invalid_template"
    save_mock.assert_not_awaited()
    hass.config_entries.async_update_entry.assert_not_called()


async def test_ws_save_and_apply_unknown_entry() -> None:
    hass = _hass(None)
    connection = _connection()
    await _unwrap(api_templates.ws_save_and_apply_template)(
        hass,
        connection,
        {
            "id": 7,
            "type": "modbus_usb/save_and_apply_template",
            "entry_id": "missing",
            "content": DRAFT_YAML,
        },
    )
    connection.send_error.assert_called_once()
    assert "not found" in connection.send_error.call_args[0][2]


async def test_ws_save_and_apply_reports_save_failures() -> None:
    entry = _FakeEntry()
    hass = _hass(entry)
    connection = _connection()

    with patch.object(
        api_templates,
        "async_save_template",
        AsyncMock(side_effect=OSError("read-only filesystem")),
    ):
        await _unwrap(api_templates.ws_save_and_apply_template)(
            hass,
            connection,
            {
                "id": 8,
                "type": "modbus_usb/save_and_apply_template",
                "entry_id": "e1",
                "content": DRAFT_YAML,
            },
        )

    connection.send_error.assert_called_once()
    assert connection.send_error.call_args[0][1] == "error"
    assert "read-only" in connection.send_error.call_args[0][2]


# ───────────────────── shared apply helper / regressions ─────────────────────


def test_template_filename_from_draft() -> None:
    assert template_filename_from_draft({"name": "Eastron SDM230!"}) == (
        "eastron_sdm230.yaml"
    )
    assert template_filename_from_draft({"id": "xy_md02"}) == "xy_md02.yaml"
    assert template_filename_from_draft({}) == "custom_template.yaml"
    assert template_filename_from_draft({"name": "###"}) == "custom_template.yaml"


async def test_apply_helper_rejects_unknown_template() -> None:
    entry = _FakeEntry()
    hass = _hass(entry)
    with pytest.raises(ValueError, match="not found"):
        await api_templates.async_apply_template_to_entry(
            hass, entry, templates=[SAVED_TEMPLATE], template_filename="other.yaml"
        )


async def test_ws_apply_template_still_works_after_refactor() -> None:
    entry = _FakeEntry()
    hass = _hass(entry)
    connection = _connection()

    with patch.object(
        api_templates, "async_load_templates", AsyncMock(return_value=[SAVED_TEMPLATE])
    ):
        await _unwrap(api_templates.ws_apply_template)(
            hass,
            connection,
            {
                "id": 9,
                "type": "modbus_usb/apply_template",
                "entry_id": "e1",
                "template_id": "fingerprint_meter",
                "m0_short": True,
            },
        )

    result = connection.send_result.call_args[0][1]
    assert result["success"] is True
    assert result["added_count"] == 2
    new_options = hass.config_entries.async_update_entry.call_args.kwargs["options"]
    assert new_options["devices"][0]["m0_short"] is True
    assert new_options["devices"][0]["slave_id"] == 2


# ───────────── v2.7.1: designer_validate reports the error location ─────────────


async def _run_in_executor(func, *args, **kwargs):
    return func(*args, **kwargs)


async def test_designer_validate_reply_includes_error_location() -> None:
    connection = _connection()
    hass = SimpleNamespace(
        data={DOMAIN: {"e1": SimpleNamespace()}},
        async_add_executor_job=_run_in_executor,
    )
    await _unwrap(api_templates.ws_designer_validate)(
        hass,
        connection,
        {
            "id": 7,
            "type": "modbus_usb/designer_validate",
            "entry_id": "e1",
            "content": (
                "name: X\n"
                "entities:\n"
                "  - name: A\n"
                "    entity_type: bogus\n"
                "    address: 1\n"
            ),
            "test_reads": False,
        },
    )
    connection.send_error.assert_not_called()
    msg_id, result = connection.send_result.call_args[0]
    assert msg_id == 7
    assert result["valid"] is False
    assert result["entities"] == []
    assert "Unsupported entity type" in result["error"]
    assert result["error_line"] == 4
    assert result["error_column"] == 18
    assert result["error_path"] == "entities[0].entity_type"


async def test_designer_validate_reply_yaml_syntax_error_location() -> None:
    connection = _connection()
    hass = SimpleNamespace(
        data={DOMAIN: {"e1": SimpleNamespace()}},
        async_add_executor_job=_run_in_executor,
    )
    await _unwrap(api_templates.ws_designer_validate)(
        hass,
        connection,
        {
            "id": 8,
            "type": "modbus_usb/designer_validate",
            "entry_id": "e1",
            "content": "name: X\nentities:\n  - name: [A\n",
            "test_reads": False,
        },
    )
    _, result = connection.send_result.call_args[0]
    assert result["valid"] is False
    assert result["error"].startswith("Invalid YAML syntax:")
    assert result["error_line"] == 3
    assert result["error_column"] == 11
    assert result["error_path"] is None


async def test_designer_validate_plain_value_error_has_no_location_keys() -> None:
    connection = _connection()
    hass = SimpleNamespace(
        data={DOMAIN: {"e1": SimpleNamespace()}},
        async_add_executor_job=_run_in_executor,
    )
    with patch.object(
        api_templates,
        "async_validate_template_design",
        AsyncMock(side_effect=ValueError("Slave ID must be between 1 and 247")),
    ):
        await _unwrap(api_templates.ws_designer_validate)(
            hass,
            connection,
            {
                "id": 9,
                "type": "modbus_usb/designer_validate",
                "entry_id": "e1",
                "content": "name: X\nentities: []\n",
                "slave_id": 999,
            },
        )
    _, result = connection.send_result.call_args[0]
    assert result == {
        "valid": False,
        "error": "Slave ID must be between 1 and 247",
        "entities": [],
    }
