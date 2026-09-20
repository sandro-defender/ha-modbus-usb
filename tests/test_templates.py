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


# Explicit test fixtures matching vendor response byte dumps for certified templates:
# Eastron SDM120 / SDM230 / SDM630, eletechsup R4D6F20, and XY-MD02.
def test_certified_eastron_templates_byte_dumps() -> None:
    """Verify IEEE 754 float32 register decoding (>f) against vendor frame byte dumps."""
    from custom_components.modbus_usb.decoding import decode_words

    # Vendor byte dump for Voltage = 230.5 V (IEEE 754: 0x43668000)
    # Registers: [0x4366, 0x8000] -> [17254, 32768]
    voltage_words = [0x4366, 0x8000]
    assert round(decode_words(voltage_words, "float32"), 1) == 230.5

    # Vendor byte dump for Current = 1.25 A (IEEE 754: 0x3FA00000)
    # Registers: [0x3FA0, 0x0000] -> [16288, 0]
    current_words = [0x3FA0, 0x0000]
    assert round(decode_words(current_words, "float32"), 2) == 1.25

    # Vendor byte dump for Active Power = 288.125 W (IEEE 754: 0x43901000)
    power_words = [0x4390, 0x1000]
    assert round(decode_words(power_words, "float32"), 3) == 288.125

    # Vendor byte dump for Total Active Energy = 1234.5 kWh (IEEE 754: 0x449A5000)
    energy_words = [0x449A, 0x5000]
    assert round(decode_words(energy_words, "float32"), 1) == 1234.5


def test_certified_xy_md02_byte_dumps() -> None:
    """Verify XY-MD02 temperature (int16) and humidity (uint16) with tenths-of-degree scaling (0.1)."""
    from custom_components.modbus_usb.decoding import decode_words

    # Positive temperature: 25.4 °C -> register value 254 (0x00FE)
    temp_pos_word = [0x00FE]
    assert decode_words(temp_pos_word, "int16") * 0.1 == pytest.approx(25.4)

    # Negative temperature: -12.5 °C -> register value -125 in two's complement = 0xFF83
    temp_neg_word = [0xFF83]
    assert decode_words(temp_neg_word, "int16") * 0.1 == pytest.approx(-12.5)

    # Humidity: 60.5 % -> register value 605 (0x025D)
    hum_word = [0x025D]
    assert decode_words(hum_word, "uint16") * 0.1 == pytest.approx(60.5)


def test_certified_r4d6f20_command1_and_command2_fixtures() -> None:
    """Verify eletechsup R4D6F20 Command 1 and Command 2 block response decoding."""
    from unittest.mock import Mock

    from custom_components.modbus_usb.boards.r4d6f20 import (
        read_command1_blocks,
        read_command2_blocks,
    )

    coordinator = Mock()
    coordinator._serial_lock = Mock()
    coordinator._serial_lock.__enter__ = Mock(return_value=None)
    coordinator._serial_lock.__exit__ = Mock(return_value=None)
    coordinator._ensure_connected = Mock()

    # Command 1 (M0 open):
    # Relays: 20 holding registers (address 0-19). Suppose CH-01 is ON (1), CH-02 is OFF (0).
    # Digital inputs: 2 holding registers (address 128-129). Suppose DI-01 is active (1), DI-02 is inactive (0).
    # Analog inputs: 2 holding registers (address 160-161). Current = 1200 (12.00 mA), Voltage = 500 (5.00 V).
    def mock_call_modbus_c1(method, start, count=1, slave=1):
        res = Mock()
        res.isError.return_value = False
        if start == 0:
            res.registers = [1, 0] + [0] * 18
        elif start == 128:
            res.registers = [1, 0]
        elif start == 160:
            res.registers = [1200, 500]
        return res

    coordinator._call_modbus.side_effect = mock_call_modbus_c1
    template = _load_template("eletechsup-R4D6F20.yaml")
    entities = template["entities"]
    for e in entities:
        if "id" not in e:
            e["id"] = e["name"]

    data, handled = read_command1_blocks(coordinator, entities, slave=1)
    assert data["CH-01"] == 1
    assert data["CH-02"] == 0
    assert data["DI-01"] == 1
    assert data["DI-02"] == 0
    assert data["Current Input"] == pytest.approx(12.00)
    assert data["Voltage Input"] == pytest.approx(5.00)

    # Command 2 (M0 shorted):
    # Relays: 20 coils (address 0-19).
    # Digital inputs: 2 discrete inputs (address 0-1).
    # Analog inputs: 2 input registers (address 0-1).
    def mock_call_modbus_c2(method, start, count=1, slave=1):
        res = Mock()
        res.isError.return_value = False
        if method == "read_coils":
            res.bits = [True, False] + [False] * 18
        elif method == "read_discrete_inputs":
            res.bits = [True, False]
        elif method == "read_input_registers":
            res.registers = [1200, 500]
        return res

    coordinator._call_modbus.side_effect = mock_call_modbus_c2
    c2_entities = [
        {"id": "ch1", "register_type": "coil", "address": 0},
        {"id": "ch2", "register_type": "coil", "address": 1},
        {"id": "di1", "register_type": "discrete", "address": 0},
        {"id": "di2", "register_type": "discrete", "address": 1},
        {"id": "curr", "register_type": "input", "address": 0, "scale": 0.01},
        {"id": "volt", "register_type": "input", "address": 1, "scale": 0.01},
    ]
    data2, handled2 = read_command2_blocks(coordinator, c2_entities, slave=1)
    assert data2["ch1"] is True
    assert data2["ch2"] is False
    assert data2["di1"] is True
    assert data2["di2"] is False
    assert data2["curr"] == pytest.approx(12.00)
    assert data2["volt"] == pytest.approx(5.00)
