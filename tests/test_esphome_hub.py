"""v2.8.0 — ESPHome hub: config flow, migration, WS commands, panel, YAMLs."""

from __future__ import annotations

import json
import os
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
import yaml
from homeassistant.data_entry_flow import AbortFlow

from custom_components.modbus_usb import async_migrate_entry
from custom_components.modbus_usb.api import helpers as api_helpers
from custom_components.modbus_usb.api import hub as api_hub
from custom_components.modbus_usb.config_flow import ModbusUsbConfigFlow
from custom_components.modbus_usb.const import (
    CONF_API_ENCRYPTION_KEY,
    CONF_HOST,
    CONF_PORT,
    CONF_TCP_PORT,
    CONF_TRANSPORT,
    DOMAIN,
    TRANSPORT_ESPHOME_API,
    TRANSPORT_ESPHOME_TCP,
    TRANSPORT_SERIAL,
)

pytestmark = pytest.mark.fast

COMPONENT_DIR = os.path.join(
    os.path.dirname(__file__), "..", "custom_components", "modbus_usb"
)


def _unwrap(func):
    while hasattr(func, "__wrapped__"):
        func = func.__wrapped__
    return func


def _flow(existing=()):
    flow = ModbusUsbConfigFlow()
    flow.hass = SimpleNamespace(
        async_add_executor_job=AsyncMock(side_effect=lambda func, *args: func(*args))
    )
    flow._async_current_entries = Mock(
        return_value=[SimpleNamespace(data=data, options={}) for data in existing]
    )
    flow.async_set_unique_id = AsyncMock(return_value=None)
    flow._abort_if_unique_id_configured = Mock()
    return flow


# ───────────────────────────── config flow ─────────────────────────────


async def test_user_step_shows_transport_picker_and_routes() -> None:
    flow = _flow()
    result = await flow.async_step_user()
    assert result["type"] == "form" and result["step_id"] == "user"
    schema_keys = {str(key) for key in result["data_schema"].schema}
    assert schema_keys == {CONF_TRANSPORT}

    result = await flow.async_step_user({CONF_TRANSPORT: TRANSPORT_ESPHOME_TCP})
    assert result["step_id"] == "esphome_tcp"
    result = await flow.async_step_user({CONF_TRANSPORT: TRANSPORT_ESPHOME_API})
    assert result["step_id"] == "esphome_api"
    result = await flow.async_step_user({CONF_TRANSPORT: TRANSPORT_SERIAL})
    assert result["step_id"] == "serial"


async def test_serial_step_stores_explicit_transport() -> None:
    flow = _flow()
    result = await flow.async_step_serial(
        {
            "name": "USB",
            CONF_PORT: "/dev/ttyUSB0",
            "baudrate": 9600,
            "bytesize": 8,
            "parity": "N",
            "stopbits": 1,
            "slave_id": 1,
            "scan_interval": 30,
        }
    )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_TRANSPORT] == TRANSPORT_SERIAL
    assert result["data"][CONF_PORT] == "/dev/ttyUSB0"


async def test_esphome_tcp_step_probes_and_creates_entry() -> None:
    flow = _flow()
    probe = {"reachable": True, "latency_ms": 12, "error": None, "esphome": None}
    with patch(
        "custom_components.modbus_usb.config_flow.probe_connection",
        return_value=probe,
    ) as probe_mock:
        result = await flow.async_step_esphome_tcp(
            {
                "name": "Bridge",
                CONF_HOST: " modbus-bridge.local ",
                CONF_TCP_PORT: 8899,
                "baudrate": 9600,
                "bytesize": 8,
                "parity": "N",
                "stopbits": 1,
                "slave_id": 1,
                "scan_interval": 30,
            }
        )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_TRANSPORT] == TRANSPORT_ESPHOME_TCP
    assert result["data"][CONF_HOST] == "modbus-bridge.local"
    assert result["data"][CONF_TCP_PORT] == 8899
    assert "name" not in result["data"] and "scan_interval" not in result["data"]
    assert result["options"]["scan_interval"] == 30
    flow.async_set_unique_id.assert_awaited_once_with(
        "esphome_tcp:modbus-bridge.local:8899"
    )
    probed = probe_mock.call_args.args[0]
    assert probed[CONF_HOST] == "modbus-bridge.local"


async def test_esphome_tcp_step_reports_probe_failure_with_detail() -> None:
    flow = _flow()
    probe = {"reachable": False, "error": "refused", "error_key": "cannot_connect"}
    with patch(
        "custom_components.modbus_usb.config_flow.probe_connection",
        return_value=probe,
    ):
        result = await flow.async_step_esphome_tcp(
            {
                "name": "Bridge",
                CONF_HOST: "10.0.0.9",
                CONF_TCP_PORT: 8899,
                "baudrate": 9600,
                "bytesize": 8,
                "parity": "N",
                "stopbits": 1,
                "slave_id": 1,
                "scan_interval": 30,
            }
        )
    assert result["type"] == "form"
    assert result["errors"] == {"base": "cannot_connect"}
    assert result["description_placeholders"]["detail"] == "refused"


async def test_esphome_api_step_drops_blank_secrets_and_appends_device_name():
    flow = _flow()
    probe = {"reachable": True, "esphome": {"name": "modbus-bridge"}, "error": None}
    with patch(
        "custom_components.modbus_usb.config_flow.probe_connection",
        return_value=probe,
    ):
        result = await flow.async_step_esphome_api(
            {
                "name": "Garage",
                CONF_HOST: "10.0.0.7",
                "api_port": 6053,
                CONF_API_ENCRYPTION_KEY: "   ",
                "api_password": "",
                "baudrate": 9600,
                "bytesize": 8,
                "parity": "N",
                "stopbits": 1,
                "slave_id": 1,
                "scan_interval": 30,
            }
        )
    assert result["type"] == "create_entry"
    assert result["title"] == "Garage (modbus-bridge)"
    assert CONF_API_ENCRYPTION_KEY not in result["data"]
    assert "api_password" not in result["data"]
    assert result["data"][CONF_TRANSPORT] == TRANSPORT_ESPHOME_API


async def test_esphome_api_step_maps_auth_error() -> None:
    flow = _flow()
    probe = {"reachable": False, "error": "bad key", "error_key": "invalid_auth"}
    with patch(
        "custom_components.modbus_usb.config_flow.probe_connection",
        return_value=probe,
    ):
        result = await flow.async_step_esphome_api(
            {
                "name": "X",
                CONF_HOST: "10.0.0.7",
                "api_port": 6053,
                CONF_API_ENCRYPTION_KEY: "wrong",
                "baudrate": 9600,
                "bytesize": 8,
                "parity": "N",
                "stopbits": 1,
                "slave_id": 1,
                "scan_interval": 30,
            }
        )
    assert result["errors"] == {"base": "invalid_auth"}


async def test_esphome_step_rejects_blank_host_without_probing() -> None:
    flow = _flow()
    with patch(
        "custom_components.modbus_usb.config_flow.probe_connection"
    ) as probe_mock:
        result = await flow.async_step_esphome_tcp(
            {
                "name": "X",
                CONF_HOST: "  ",
                CONF_TCP_PORT: 8899,
                "baudrate": 9600,
                "bytesize": 8,
                "parity": "N",
                "stopbits": 1,
                "slave_id": 1,
                "scan_interval": 30,
            }
        )
    assert result["errors"] == {CONF_HOST: "invalid_host"}
    probe_mock.assert_not_called()


async def test_same_esphome_host_cannot_be_added_twice() -> None:
    flow = _flow(
        existing=[{CONF_TRANSPORT: TRANSPORT_ESPHOME_TCP, CONF_HOST: "10.0.0.9"}]
    )
    with pytest.raises(AbortFlow) as error:
        await flow.async_step_esphome_tcp(
            {
                "name": "X",
                CONF_HOST: "10.0.0.9",
                CONF_TCP_PORT: 8899,
                "baudrate": 9600,
                "bytesize": 8,
                "parity": "N",
                "stopbits": 1,
                "slave_id": 5,
                "scan_interval": 30,
            }
        )
    assert error.value.reason == "already_configured"


def test_config_flow_version_is_2() -> None:
    assert ModbusUsbConfigFlow.VERSION == 2


# ───────────────────────────── migration ─────────────────────────────


async def test_migrate_v1_entry_adds_serial_transport() -> None:
    updates = []
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(
            async_update_entry=lambda entry, **kw: updates.append(kw)
        )
    )
    entry = SimpleNamespace(
        entry_id="old", version=1, data={CONF_PORT: "/dev/ttyUSB0", "slave_id": 1}
    )
    assert await async_migrate_entry(hass, entry) is True
    assert updates == [
        {
            "data": {
                CONF_PORT: "/dev/ttyUSB0",
                "slave_id": 1,
                CONF_TRANSPORT: TRANSPORT_SERIAL,
            },
            "version": 2,
        }
    ]


async def test_migrate_leaves_v2_alone_and_refuses_newer() -> None:
    hass = SimpleNamespace(config_entries=SimpleNamespace(async_update_entry=Mock()))
    v2 = SimpleNamespace(entry_id="e", version=2, data={})
    assert await async_migrate_entry(hass, v2) is True
    hass.config_entries.async_update_entry.assert_not_called()
    v3 = SimpleNamespace(entry_id="e", version=3, data={})
    assert await async_migrate_entry(hass, v3) is False


# ───────────────────────────── WS commands ─────────────────────────────


class _Entry:
    def __init__(self, data: dict, entry_id: str = "e1") -> None:
        self.entry_id = entry_id
        self.data = data
        self.options = {"entities": []}


def _hass(entry: _Entry, coordinator=None):
    updates: list[dict] = []

    def get_entry(entry_id):
        return entry if entry_id == entry.entry_id else None

    hass = SimpleNamespace(
        data={DOMAIN: {entry.entry_id: coordinator} if coordinator else {}},
        config_entries=SimpleNamespace(
            async_get_entry=get_entry,
            async_update_entry=lambda e, **kw: updates.append(kw),
        ),
        async_add_executor_job=AsyncMock(side_effect=lambda func, *args: func(*args)),
    )
    hass.updates = updates
    return hass


def _conn():
    return SimpleNamespace(send_result=Mock(), send_error=Mock())


async def test_ws_save_hub_switches_serial_entry_to_esphome_tcp() -> None:
    entry = _Entry({CONF_TRANSPORT: TRANSPORT_SERIAL, CONF_PORT: "/dev/ttyUSB0"})
    hass = _hass(entry)
    conn = _conn()
    await _unwrap(api_hub.ws_save_hub)(
        hass,
        conn,
        {
            "id": 1,
            "entry_id": "e1",
            "hub": {
                CONF_TRANSPORT: TRANSPORT_ESPHOME_TCP,
                CONF_HOST: " bridge.local ",
                CONF_TCP_PORT: "8899",
                "response_timeout": 99,
                "baudrate": 19200,
                "scan_interval": 15,
            },
        },
    )
    conn.send_result.assert_called_once_with(1, {"success": True})
    data = hass.updates[0]["data"]
    assert data[CONF_TRANSPORT] == TRANSPORT_ESPHOME_TCP
    assert data[CONF_HOST] == "bridge.local"
    assert data[CONF_TCP_PORT] == 8899
    assert data["response_timeout"] == 30.0
    assert data["baudrate"] == 19200
    assert data[CONF_PORT] == "/dev/ttyUSB0"  # untouched legacy value kept
    assert hass.updates[0]["options"]["scan_interval"] == 15


async def test_ws_save_hub_secret_semantics() -> None:
    entry = _Entry(
        {
            CONF_TRANSPORT: TRANSPORT_ESPHOME_API,
            CONF_HOST: "10.0.0.7",
            CONF_API_ENCRYPTION_KEY: "old",
        }
    )
    hass = _hass(entry)
    # Blank → keep.
    await _unwrap(api_hub.ws_save_hub)(
        hass, _conn(), {"id": 1, "entry_id": "e1", "hub": {CONF_API_ENCRYPTION_KEY: ""}}
    )
    assert hass.updates[-1]["data"][CONF_API_ENCRYPTION_KEY] == "old"
    # Explicit clear.
    await _unwrap(api_hub.ws_save_hub)(
        hass,
        _conn(),
        {
            "id": 2,
            "entry_id": "e1",
            "hub": {CONF_API_ENCRYPTION_KEY: "", "clear_api_encryption_key": True},
        },
    )
    assert CONF_API_ENCRYPTION_KEY not in hass.updates[-1]["data"]
    # Replace.
    await _unwrap(api_hub.ws_save_hub)(
        hass,
        _conn(),
        {"id": 3, "entry_id": "e1", "hub": {CONF_API_ENCRYPTION_KEY: "new"}},
    )
    assert hass.updates[-1]["data"][CONF_API_ENCRYPTION_KEY] == "new"


@pytest.mark.parametrize(
    "hub",
    [
        {CONF_TRANSPORT: "bogus"},
        {CONF_TRANSPORT: TRANSPORT_ESPHOME_TCP, CONF_HOST: "  "},
        {CONF_TCP_PORT: 70000},
        {CONF_TCP_PORT: "abc"},
    ],
)
async def test_ws_save_hub_rejects_invalid_transport_input(hub) -> None:
    entry = _Entry({CONF_TRANSPORT: TRANSPORT_SERIAL, CONF_PORT: "/dev/ttyUSB0"})
    hass = _hass(entry)
    conn = _conn()
    await _unwrap(api_hub.ws_save_hub)(
        hass, conn, {"id": 1, "entry_id": "e1", "hub": hub}
    )
    conn.send_error.assert_called_once()
    assert hass.updates == []


async def test_ws_test_hub_connection_probes_overrides_without_saving() -> None:
    entry = _Entry({CONF_TRANSPORT: TRANSPORT_SERIAL, CONF_PORT: "/dev/ttyUSB0"})
    hass = _hass(entry)
    conn = _conn()
    probe = {"reachable": True, "latency_ms": 4, "error": None, "error_key": None}
    with patch.object(api_hub, "probe_connection", return_value=probe) as mock:
        await _unwrap(api_hub.ws_test_hub_connection)(
            hass,
            conn,
            {
                "id": 7,
                "entry_id": "e1",
                "hub": {
                    CONF_TRANSPORT: TRANSPORT_ESPHOME_TCP,
                    CONF_HOST: "bridge.local",
                    CONF_TCP_PORT: 8899,
                },
            },
        )
    config = mock.call_args.args[0]
    assert config[CONF_TRANSPORT] == TRANSPORT_ESPHOME_TCP
    assert config[CONF_HOST] == "bridge.local"
    result = conn.send_result.call_args.args[1]
    assert result["reachable"] is True and result["live"] is False
    assert result["summary"]["endpoint"] == "bridge.local:8899"
    assert hass.updates == []


async def test_ws_test_hub_connection_short_circuits_on_live_client() -> None:
    data = {CONF_TRANSPORT: TRANSPORT_ESPHOME_API, CONF_HOST: "10.0.0.7"}
    entry = _Entry(data)
    coordinator = SimpleNamespace(
        serial_config=dict(data),
        client=SimpleNamespace(connected=True, device_info={"name": "bridge"}),
    )
    hass = _hass(entry, coordinator)
    conn = _conn()
    with patch.object(api_hub, "probe_connection") as mock:
        await _unwrap(api_hub.ws_test_hub_connection)(
            hass, conn, {"id": 1, "entry_id": "e1"}
        )
    mock.assert_not_called()
    result = conn.send_result.call_args.args[1]
    assert result["live"] is True and result["esphome"] == {"name": "bridge"}
    assert result["summary"]["transport"] == TRANSPORT_ESPHOME_API


async def test_ws_test_hub_connection_blank_secret_keeps_stored_one() -> None:
    entry = _Entry(
        {
            CONF_TRANSPORT: TRANSPORT_ESPHOME_API,
            CONF_HOST: "10.0.0.7",
            CONF_API_ENCRYPTION_KEY: "stored",
        }
    )
    hass = _hass(entry)
    conn = _conn()
    probe = {"reachable": False, "error": "x", "error_key": "cannot_connect"}
    with patch.object(api_hub, "probe_connection", return_value=probe) as mock:
        await _unwrap(api_hub.ws_test_hub_connection)(
            hass,
            conn,
            {"id": 1, "entry_id": "e1", "hub": {CONF_API_ENCRYPTION_KEY: ""}},
        )
    assert mock.call_args.args[0][CONF_API_ENCRYPTION_KEY] == "stored"
    result = conn.send_result.call_args.args[1]
    assert "stored" not in json.dumps(result)


async def test_ws_test_hub_connection_unknown_entry_sends_error() -> None:
    hass = _hass(_Entry({}))
    conn = _conn()
    await _unwrap(api_hub.ws_test_hub_connection)(
        hass, conn, {"id": 1, "entry_id": "missing"}
    )
    conn.send_error.assert_called_once()
    assert conn.send_error.call_args.args[1] == "test_failed"


def test_hub_transport_fields_never_include_secrets() -> None:
    fields = api_helpers._hub_transport_fields(
        {
            CONF_TRANSPORT: TRANSPORT_ESPHOME_API,
            CONF_HOST: "10.0.0.7",
            CONF_API_ENCRYPTION_KEY: "topsecret",
            "api_password": "pw",
        }
    )
    assert "topsecret" not in json.dumps(fields) and "pw" not in json.dumps(fields)
    assert fields["encrypted"] is True and fields["password_set"] is True
    assert fields["baudrate_fixed"] is True
    assert fields["endpoint"] == "10.0.0.7:6053"
    serial = api_helpers._hub_transport_fields({CONF_PORT: "/dev/ttyUSB0"})
    assert serial["transport"] == TRANSPORT_SERIAL
    assert serial["baudrate_fixed"] is False


# ───────────────────────────── panel wiring ─────────────────────────────


def _www(name: str) -> str:
    with open(os.path.join(COMPONENT_DIR, "www", name), encoding="utf-8") as f:
        return f.read()


def test_panel_hub_form_has_transport_fields_and_test_button() -> None:
    html = _www("modbus-panel.html")
    for element_id in (
        "hub-form-transport",
        "hub-serial-fields",
        "hub-esphome-fields",
        "hub-form-host",
        "hub-form-tcp-port",
        "hub-form-api-port",
        "hub-form-api-key",
        "hub-form-service",
        "hub-form-event",
        "hub-form-timeout",
        "btn-hub-form-test",
        "hub-form-test-result",
        "scan-fixed-baud-note",
    ):
        assert f'id="{element_id}"' in html, element_id
    for option in (TRANSPORT_SERIAL, TRANSPORT_ESPHOME_TCP, TRANSPORT_ESPHOME_API):
        assert f'value="{option}"' in html, option


def test_panel_hub_js_wires_transport_switching_and_connection_test() -> None:
    hub_js = _www(os.path.join("panel", "hub.js"))
    assert "renderTransportFields" in hub_js
    assert "collectHubForm" in hub_js
    assert "btn-test-hub-connection" in hub_js
    assert "apiCall('test_hub_connection'" in hub_js or (
        'apiCall("test_hub_connection"' in hub_js
    )
    assert "esphome_tcp" in hub_js and "esphome_api" in hub_js
    # Secrets are write-only: the panel must not prefill the key field.
    assert re.search(r"hub-form-api-key.*\.value\s*=\s*['\"]{2}", hub_js, re.S)
    diagnostics_js = _www(os.path.join("panel", "diagnostics.js"))
    assert "updateScanTransportNote" in diagnostics_js
    assert "baudrate_fixed" in diagnostics_js
    core_js = _www(os.path.join("panel", "core.js"))
    assert "test_hub_connection" in core_js


def test_translations_cover_transport_steps_and_errors() -> None:
    for name in ("strings.json", os.path.join("translations", "en.json")):
        with open(os.path.join(COMPONENT_DIR, name), encoding="utf-8") as f:
            config = json.load(f)["config"]
        for step in ("user", "serial", "esphome_tcp", "esphome_api"):
            assert step in config["step"], (name, step)
        assert set(config["step"]["user"]["data"]) == {CONF_TRANSPORT}
        assert CONF_HOST in config["step"]["esphome_tcp"]["data"]
        assert CONF_API_ENCRYPTION_KEY in config["step"]["esphome_api"]["data"]
        for error in (
            "cannot_connect",
            "invalid_auth",
            "service_missing",
            "invalid_host",
        ):
            assert error in config["error"], (name, error)


# ───────────────────────────── ESPHome examples ─────────────────────────────


class _EsphomeLoader(yaml.SafeLoader):
    pass


_EsphomeLoader.add_constructor("!secret", lambda loader, node: f"<secret:{node.value}>")
_EsphomeLoader.add_constructor("!lambda", lambda loader, node: node.value)


def _example(name: str) -> dict:
    with open(os.path.join(COMPONENT_DIR, "esphome", name), encoding="utf-8") as f:
        return yaml.load(f, Loader=_EsphomeLoader)


def test_tcp_bridge_yaml_exposes_stream_server_on_uart2() -> None:
    config = _example("modbus_bridge_esp32.yaml")
    subs = config["substitutions"]
    assert subs["uart_tx"] == "GPIO17" and subs["uart_rx"] == "GPIO16"
    assert str(subs["tcp_port"]) == "8899"
    uart = config["uart"][0] if isinstance(config["uart"], list) else config["uart"]
    assert uart["id"] == "modbus_uart"
    assert "stream_server" in config
    assert config["external_components"][0]["source"].startswith("github://oxan/")
    assert config["wifi"]["ssid"] == "<secret:wifi_ssid>"
    assert config["api"]["encryption"]["key"] == "<secret:api_encryption_key>"


def test_api_bridge_yaml_defines_service_and_event() -> None:
    config = _example("modbus_api_bridge_esp32.yaml")
    services = config["api"]["services"]
    names = {service["service"] for service in services}
    assert {"modbus_send", "set_serial"} <= names
    send = next(s for s in services if s["service"] == "modbus_send")
    assert send["variables"] == {"data": "int[]"}
    text = open(
        os.path.join(COMPONENT_DIR, "esphome", "modbus_api_bridge_esp32.yaml"),
        encoding="utf-8",
    ).read()
    assert "esphome.modbus_rx" in text
    assert '"frame"' in text and '"device"' in text
    assert "interval" in config


def test_esphome_readme_links_both_examples() -> None:
    with open(
        os.path.join(COMPONENT_DIR, "esphome", "README.md"), encoding="utf-8"
    ) as f:
        readme = f.read()
    assert "modbus_bridge_esp32.yaml" in readme
    assert "modbus_api_bridge_esp32.yaml" in readme
    assert "GPIO16" in readme and "GPIO17" in readme


def test_manifest_requires_aioesphomeapi() -> None:
    with open(os.path.join(COMPONENT_DIR, "manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    assert any(req.startswith("aioesphomeapi") for req in manifest["requirements"])
    assert manifest["version"] == "2.8.0"
    with open(
        os.path.join(os.path.dirname(__file__), "..", "requirements_test.txt"),
        encoding="utf-8",
    ) as f:
        assert "aioesphomeapi" in f.read()
