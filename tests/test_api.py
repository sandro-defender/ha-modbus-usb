"""API authorization and invalid configuration must fail before mutation."""
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from aiohttp import web
from homeassistant.exceptions import Unauthorized

from custom_components.modbus_usb import api


class Request(dict):
    def __init__(self, *, admin=True, body=None, query=None):
        super().__init__(hass_user=SimpleNamespace(is_admin=admin))
        self.app = {"hass": Mock()}
        self.query = query or {}
        self.json = AsyncMock(return_value=body)


@pytest.mark.parametrize("method", ["post", "delete"])
async def test_template_mutation_requires_admin(method):
    request = Request(admin=False)
    with patch.object(api, "async_save_template", new_callable=AsyncMock) as save, patch.object(
        api, "async_delete_template", new_callable=AsyncMock
    ) as delete:
        with pytest.raises(web.HTTPForbidden):
            await getattr(api.ModbusUsbTemplatesView(), method)(request)
        save.assert_not_awaited()
        delete.assert_not_awaited()
        request.json.assert_not_awaited()


@pytest.mark.parametrize("body", [None, [], "text", 12, {}, {"filename": 1, "content": "name: Test"}, {"filename": "test", "content": {}}])
async def test_bad_template_request_returns_400(body):
    with patch.object(api, "async_save_template", new_callable=AsyncMock) as save:
        response = await api.ModbusUsbTemplatesView().post(Request(body=body))
        assert response.status == 400
        save.assert_not_awaited()


async def test_invalid_json_returns_400():
    request = Request()
    request.json.side_effect = ValueError("Invalid JSON")
    response = await api.ModbusUsbTemplatesView().post(request)
    assert response.status == 400


async def test_admin_can_save_template():
    with patch.object(api, "async_save_template", new_callable=AsyncMock, return_value={"id": "test"}) as save:
        request = Request(body={"filename": "test", "content": "name: Test"})
        response = await api.ModbusUsbTemplatesView().post(request)
        assert response.status == 200
        save.assert_awaited_once_with(request.app["hass"], "test", "name: Test")


async def test_delete_invalid_path_returns_400():
    request = Request(query={"filename": "../other.yaml"})
    with patch.object(api, "async_delete_template", new_callable=AsyncMock, side_effect=ValueError("Invalid filename")):
        response = await api.ModbusUsbTemplatesView().delete(request)
    assert response.status == 400


def test_switch_write_requires_admin():
    hass = Mock()
    connection = Mock(user=SimpleNamespace(is_admin=False))
    with pytest.raises(Unauthorized):
        api.ws_write_entity(hass, connection, {"id": 1})
    hass.async_create_background_task.assert_not_called()


@pytest.mark.parametrize("domain", [None, "other_integration"])
def test_entry_lookup_rejects_unrelated_entries(domain):
    entry = None if domain is None else SimpleNamespace(domain=domain)
    hass = Mock()
    hass.config_entries.async_get_entry.return_value = entry
    with pytest.raises(ValueError, match="not found"):
        api._get_entry(hass, "foreign-id")


def entry_hass():
    entry = SimpleNamespace(entry_id="hub", domain="modbus_usb", title="Hub", options={}, data={
        "port": "/dev/ttyUSB0", "baudrate": 9600, "bytesize": 8,
        "parity": "N", "stopbits": 1, "slave_id": 1,
    })
    hass = Mock(data={})
    hass.config_entries.async_get_entry.return_value = entry
    return entry, hass


@pytest.mark.parametrize("hub", [
    {"scan_interval": 0}, {"slave_id": 0}, {"slave_id": 248},
    {"bytesize": 9}, {"stopbits": 3}, {"parity": "bad"}, {"baudrate": 123},
    {"port": "  "},
])
async def test_invalid_hub_settings_are_not_saved(hub):
    _, hass = entry_hass()
    connection = Mock()
    await inspect.unwrap(api.ws_save_hub)(hass, connection, {"id": 1, "entry_id": "hub", "hub": hub})
    connection.send_error.assert_called_once()
    hass.config_entries.async_update_entry.assert_not_called()


async def test_valid_hub_partial_update_keeps_other_options():
    entry, hass = entry_hass()
    entry.options = {"entities": [{"id": "old"}]}
    connection = Mock()
    await inspect.unwrap(api.ws_save_hub)(hass, connection, {"id": 1, "entry_id": "hub", "hub": {"baudrate": 19200}})
    saved = hass.config_entries.async_update_entry.call_args.kwargs
    assert saved["data"]["baudrate"] == 19200
    assert saved["data"]["port"] == "/dev/ttyUSB0"
    assert saved["options"]["entities"] == [{"id": "old"}]
    connection.send_error.assert_not_called()


async def test_partial_entity_update_preserves_template_fields():
    entry, hass = entry_hass()
    entry.options = {"entities": [{"id": "switch", "name": "Old", "entity_type": "switch",
                                  "register_type": "holding", "address": 1, "on_value": 256,
                                  "assumed_state": True}]}
    connection = Mock()
    await inspect.unwrap(api.ws_save_entity)(hass, connection, {
        "id": 1, "entry_id": "hub", "entity": {"id": "switch", "name": "New"},
    })
    saved = hass.config_entries.async_update_entry.call_args.kwargs["options"]["entities"][0]
    assert saved["name"] == "New"
    assert saved["on_value"] == 256
    assert saved["assumed_state"] is True
    assert entry.options["entities"][0]["name"] == "Old"


@pytest.mark.parametrize("offset", [-1, 65536])
async def test_template_offsets_are_validated_before_saving(offset):
    _, hass = entry_hass()
    connection = Mock()
    template = {"id": "test", "entities": [{"name": "Value", "entity_type": "sensor", "register_type": "holding", "address": 0}]}
    with patch.object(api, "async_load_templates", new_callable=AsyncMock, return_value=[template]):
        await inspect.unwrap(api.ws_apply_template)(hass, connection, {
            "id": 1, "entry_id": "hub", "template_id": "test", "address_offset": offset,
        })
    connection.send_error.assert_called_once()
    hass.config_entries.async_update_entry.assert_not_called()


async def test_api_registration_is_per_instance_and_idempotent():
    instances = [Mock(data={}), Mock(data={})]
    with patch.object(api.websocket_api, "async_register_command") as register:
        for hass in instances:
            await api.async_register_api(hass)
            await api.async_register_api(hass)
            assert hass.http.register_view.call_count == 2
        assert register.call_count == 2 * len({call.args[1] for call in register.call_args_list})
