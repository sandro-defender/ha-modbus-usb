"""Tests for the interactive Template Designer live validator."""

from __future__ import annotations

import struct
from types import SimpleNamespace

import pytest

from custom_components.modbus_usb.designer import (
    MAX_DESIGNER_ENTITIES,
    async_validate_template_design,
    evaluate_template_design,
    load_template_draft,
)
from custom_components.modbus_usb.validation import validate_template

pytestmark = pytest.mark.fast

VALID_YAML = """
name: Test Meter
id: test_meter
default_slave_id: 2
entities:
  - name: Voltage
    entity_type: sensor
    register_type: input
    address: 0
    data_type: float32
    unit_of_measurement: "V"
  - name: Temperature
    entity_type: sensor
    register_type: holding
    address: 1
    data_type: int16
    scale: 0.1
  - name: Relay
    entity_type: switch
    register_type: coil
    address: 5
"""


def test_load_template_draft_validates_structure() -> None:
    template = load_template_draft(VALID_YAML)
    assert template["name"] == "Test Meter"
    assert template["default_slave_id"] == 2
    assert len(template["entities"]) == 3


def test_load_template_draft_rejects_bad_input() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        load_template_draft("   ")
    with pytest.raises(ValueError, match="YAML"):
        load_template_draft("name: [unclosed")
    with pytest.raises(ValueError, match="entity"):
        load_template_draft("name: X\nentities:\n  - name: A\n    entity_type: bogus\n")
    with pytest.raises(ValueError):
        load_template_draft("- just\n- a\n- list\n")


def _float_words(value: float) -> list[int]:
    raw = struct.pack(">f", value)
    return list(struct.unpack(">HH", raw))


def test_evaluate_decodes_float32_into_all_types() -> None:
    calls = []

    def reader(address, register_type, count, slave):
        calls.append((address, register_type, count, slave))
        return _float_words(230.4)

    result = evaluate_template_design(
        reader,
        validate_template(
            {
                "name": "M",
                "entities": [
                    {
                        "name": "Voltage",
                        "entity_type": "sensor",
                        "register_type": "input",
                        "address": 0,
                        "data_type": "float32",
                    },
                ],
            }
        ),
    )
    assert calls == [(0, "input", 2, 1)]
    assert result["passed"] == 1
    assert result["failed"] == 0
    assert result["all_passed"] is True
    entity = result["entities"][0]
    assert entity["status"] == "pass"
    assert entity["word_count"] == 2
    assert entity["raw_words"] == _float_words(230.4)
    assert entity["decodings"]["float32"] == pytest.approx(230.4)
    assert "uint32" in entity["decodings"]
    assert "int32" in entity["decodings"]
    # A single word cannot decode as a 32-bit type from one register only.
    one_word = evaluate_template_design(
        lambda *args: [0x1234],
        validate_template(
            {
                "name": "M",
                "entities": [
                    {
                        "name": "V",
                        "entity_type": "sensor",
                        "register_type": "input",
                        "address": 0,
                        "data_type": "uint16",
                    },
                ],
            }
        ),
    )
    assert "uint32" not in one_word["entities"][0]["decodings"]
    assert entity["value"] == pytest.approx(230.4)


def test_evaluate_applies_scale_and_template_slave() -> None:
    def reader(address, register_type, count, slave):
        assert slave == 7
        return [242]

    template = validate_template(
        {
            "name": "M",
            "default_slave_id": 7,
            "entities": [
                {
                    "name": "Temp",
                    "entity_type": "sensor",
                    "register_type": "holding",
                    "address": 1,
                    "data_type": "int16",
                    "scale": 0.1,
                },
            ],
        }
    )
    result = evaluate_template_design(reader, template)
    entity = result["entities"][0]
    assert result["slave_id"] == 7
    assert entity["value"] == pytest.approx(24.2)
    assert entity["scaled"] is True
    assert entity["decodings"]["int16"] == 242


def test_evaluate_explicit_slave_override_wins() -> None:
    seen = {}

    def reader(address, register_type, count, slave):
        seen["slave"] = slave
        return [1]

    template = validate_template(
        {
            "name": "M",
            "default_slave_id": 3,
            "entities": [
                {
                    "name": "Relay",
                    "entity_type": "switch",
                    "register_type": "coil",
                    "address": 5,
                },
            ],
        }
    )
    result = evaluate_template_design(reader, template, slave_id=9)
    assert seen["slave"] == 9
    assert result["slave_id"] == 9


def test_evaluate_coil_and_discrete_reads_return_bool() -> None:
    def reader(address, register_type, count, slave):
        assert count == 1
        return [1] if register_type == "coil" else [0]

    template = validate_template(
        {
            "name": "M",
            "entities": [
                {
                    "name": "Relay",
                    "entity_type": "switch",
                    "register_type": "coil",
                    "address": 5,
                },
                {
                    "name": "Input",
                    "entity_type": "binary_sensor",
                    "register_type": "discrete",
                    "address": 2,
                },
            ],
        }
    )
    result = evaluate_template_design(reader, template)
    assert result["passed"] == 2
    assert result["entities"][0]["value"] is True
    assert result["entities"][0]["decodings"] == {"bool": True}
    assert result["entities"][1]["value"] is False


def test_evaluate_records_per_entity_failures_without_aborting() -> None:
    def reader(address, register_type, count, slave):
        if address == 1:
            raise TimeoutError("no response received from slave")
        return [42]

    template = validate_template(
        {
            "name": "M",
            "entities": [
                {
                    "name": "Good",
                    "entity_type": "sensor",
                    "register_type": "holding",
                    "address": 0,
                    "data_type": "uint16",
                },
                {
                    "name": "Bad",
                    "entity_type": "sensor",
                    "register_type": "holding",
                    "address": 1,
                    "data_type": "uint16",
                },
            ],
        }
    )
    result = evaluate_template_design(reader, template)
    assert result["passed"] == 1
    assert result["failed"] == 1
    assert result["all_passed"] is False
    bad = result["entities"][1]
    assert bad["status"] == "fail"
    assert "no response" in bad["error"]


def test_evaluate_skip_reads_mode() -> None:
    def reader(*args):
        raise AssertionError("must not read when test_reads is False")

    template = validate_template(
        {
            "name": "M",
            "entities": [
                {
                    "name": "V",
                    "entity_type": "sensor",
                    "register_type": "input",
                    "address": 0,
                    "data_type": "uint16",
                },
            ],
        }
    )
    result = evaluate_template_design(reader, template, test_reads=False)
    assert result["test_reads"] is False
    assert result["skipped"] == 1
    assert result["entities"][0]["status"] == "skipped"


def test_evaluate_truncates_huge_templates() -> None:
    template = validate_template(
        {
            "name": "M",
            "entities": [
                {
                    "name": f"E{i}",
                    "entity_type": "sensor",
                    "register_type": "holding",
                    "address": i,
                    "data_type": "uint16",
                }
                for i in range(MAX_DESIGNER_ENTITIES + 5)
            ],
        }
    )
    result = evaluate_template_design(lambda *args: [0], template)
    assert result["truncated"] is True
    assert result["entity_count"] == MAX_DESIGNER_ENTITIES + 5
    assert len(result["entities"]) == MAX_DESIGNER_ENTITIES


def test_evaluate_rejects_bad_slave() -> None:
    template = validate_template({"name": "M", "entities": []})
    with pytest.raises(ValueError, match="Slave ID"):
        evaluate_template_design(lambda *args: [0], template, slave_id=999)


async def _run_in_executor(func, *args, **kwargs):
    return func(*args, **kwargs)


async def test_async_validate_template_design_uses_coordinator_reads() -> None:
    calls = []

    class FakeCoordinator:
        def read_raw_words(self, address, register_type, count, slave):
            calls.append((address, register_type, count, slave))
            return [42]

    hass = SimpleNamespace(async_add_executor_job=_run_in_executor)
    result = await async_validate_template_design(
        hass, FakeCoordinator(), VALID_YAML, slave_id=4
    )
    assert result["valid"] is True
    assert result["slave_id"] == 4
    assert result["passed"] == 3
    assert calls[0][3] == 4
    assert calls[2][:3] == (5, "coil", 1)


async def test_async_validate_template_design_rejects_invalid_yaml() -> None:
    hass = SimpleNamespace(async_add_executor_job=_run_in_executor)
    with pytest.raises(ValueError):
        await async_validate_template_design(hass, SimpleNamespace(), "name: [broken")
