"""Structural validation for every bundled device template.

These tests catch template typos (bad entity types, out-of-range addresses,
unknown register/data types) before they reach users. They intentionally avoid
Home Assistant imports so they run fast and standalone.
"""

from __future__ import annotations

import importlib.util
import os
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.fast


INTEGRATION_DIR = os.path.join(
    os.path.dirname(__file__), "..", "custom_components", "modbus_usb"
)
TEMPLATES_DIR = os.path.join(INTEGRATION_DIR, "templates")


def _load_const():
    """Load const.py directly so tests run without Home Assistant installed."""
    path = os.path.join(INTEGRATION_DIR, "const.py")
    spec = importlib.util.spec_from_file_location("modbus_usb_const", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_const = _load_const()
DATA_TYPES = _const.DATA_TYPES
ENTITY_TYPES = _const.ENTITY_TYPES

# Register types each entity type may legally use. Binary sensors accept
# holding registers because several eletechsup input maps are holding-based.
ALLOWED_REGISTER_TYPES = {
    "sensor": {"holding", "input"},
    "switch": {"coil", "holding"},
    "binary_sensor": {"coil", "discrete", "holding"},
    "number": {"holding"},
}

KNOWN_PROTOCOLS = {"eletechsup_r413e16", "eletechsup_r4d6f20"}
KNOWN_STATUS = {"tested", "testing", "untested"}


def _template_files() -> list[str]:
    return sorted(f for f in os.listdir(TEMPLATES_DIR) if f.endswith((".yaml", ".yml")))


def _load_template(filename: str) -> dict[str, Any]:
    with open(os.path.join(TEMPLATES_DIR, filename), encoding="utf-8") as f:
        data = yaml.safe_load(f)
    assert isinstance(data, dict), f"{filename}: root must be a mapping"
    return data


def _assert_address(filename: str, label: str, address: Any) -> None:
    assert isinstance(address, int) and not isinstance(address, bool), (
        f"{filename}: {label} address must be an integer, got {address!r}"
    )
    assert 0 <= address <= 65535, (
        f"{filename}: {label} address {address} out of range 0-65535"
    )


def test_bundled_templates_exist() -> None:
    files = _template_files()
    assert files, "No bundled templates found"


def test_template_ids_unique_and_match_filenames() -> None:
    seen: set[str] = set()
    for filename in _template_files():
        data = _load_template(filename)
        expected = os.path.splitext(filename)[0]
        assert data.get("id") == expected, (
            f"{filename}: id {data.get('id')!r} must match filename stem"
        )
        assert data.get("id") not in seen, f"Duplicate template id {expected}"
        seen.add(data["id"])


@pytest.mark.parametrize("filename", _template_files())
def test_template_metadata(filename: str) -> None:
    data = _load_template(filename)
    assert data.get("name"), f"{filename}: missing name"
    slave_id = data.get("default_slave_id", 1)
    assert isinstance(slave_id, int) and 1 <= slave_id <= 247, (
        f"{filename}: default_slave_id {slave_id!r} out of range 1-247"
    )
    if "status" in data:
        assert data["status"] in KNOWN_STATUS, (
            f"{filename}: unknown status {data['status']!r}"
        )
    if data.get("tested"):
        assert data.get("status", "tested") == "tested", (
            f"{filename}: tested template must have status 'tested'"
        )
    protocol = (data.get("device_controls") or {}).get("protocol")
    if protocol is not None:
        assert protocol in KNOWN_PROTOCOLS, (
            f"{filename}: unknown device_controls protocol {protocol!r}"
        )


@pytest.mark.parametrize("filename", _template_files())
def test_template_entities(filename: str) -> None:
    data = _load_template(filename)
    entities = data.get("entities")
    assert isinstance(entities, list) and entities, (
        f"{filename}: must define a non-empty entities list"
    )
    for index, entity in enumerate(entities):
        label = f"{filename} entity #{index} ({entity.get('name', '?')})"
        assert isinstance(entity, dict), f"{label}: must be a mapping"
        assert entity.get("name"), f"{label}: missing name"
        entity_type = entity.get("entity_type")
        assert entity_type in ENTITY_TYPES, (
            f"{label}: unknown entity_type {entity_type!r}"
        )
        register_type = entity.get("register_type")
        assert register_type in ALLOWED_REGISTER_TYPES[entity_type], (
            f"{label}: register_type {register_type!r} not allowed for {entity_type}"
        )
        addresses = entity.get("addresses")
        if addresses is not None:
            assert isinstance(addresses, list) and addresses, (
                f"{label}: addresses must be a non-empty list"
            )
            for address in addresses:
                _assert_address(filename, label, address)
        else:
            assert "address" in entity, f"{label}: missing address"
            _assert_address(filename, label, entity["address"])
        data_type = entity.get("data_type")
        if data_type is not None and entity_type in ("sensor", "number"):
            assert data_type in DATA_TYPES, f"{label}: unknown data_type {data_type!r}"


@pytest.mark.parametrize("filename", _template_files())
def test_template_fingerprints(filename: str) -> None:
    data = _load_template(filename)
    for index, check in enumerate(data.get("fingerprint") or []):
        label = f"{filename} fingerprint #{index}"
        assert isinstance(check, dict), f"{label}: must be a mapping"
        assert check.get("register_type") in ("holding", "input"), (
            f"{label}: bad register_type {check.get('register_type')!r}"
        )
        _assert_address(filename, label, check.get("address"))
        assert check.get("data_type") in DATA_TYPES, (
            f"{label}: bad data_type {check.get('data_type')!r}"
        )
