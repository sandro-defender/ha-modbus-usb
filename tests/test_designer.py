"""Tests for the interactive Template Designer live validator."""

from __future__ import annotations

import struct
from types import SimpleNamespace

import pytest

from custom_components.modbus_usb.designer import (
    MAX_DESIGNER_ENTITIES,
    TemplateDraftError,
    async_validate_template_design,
    evaluate_template_design,
    evaluate_template_fingerprint,
    load_template_draft,
    locate_template_error,
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


# ───────────────── v2.6.0: fingerprint probes in designer_validate ─────────────────

FINGERPRINT_YAML = """
name: Fingerprint Meter
id: fingerprint_meter
default_slave_id: 1
fingerprint:
  - register_type: input
    address: 0
    data_type: float32
    min_value: 80
    max_value: 300
  - register_type: holding
    address: 4
    data_type: uint16
    min_value: 1
    max_value: 10
entities:
  - name: Voltage
    entity_type: sensor
    register_type: input
    address: 0
    data_type: float32
"""


def _fingerprint_template(fingerprint=None):
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
    if fingerprint is not None:
        template["fingerprint"] = fingerprint
    return template


def test_evaluate_fingerprint_all_match() -> None:
    def reader(address, register_type, count, slave):
        assert slave == 1
        if register_type == "input":
            return _float_words(230.4)
        return [5]

    template = _fingerprint_template(
        [
            {
                "register_type": "input",
                "address": 0,
                "data_type": "float32",
                "min_value": 80,
                "max_value": 300,
            },
            {
                "register_type": "holding",
                "address": 4,
                "data_type": "uint16",
                "min_value": 1,
                "max_value": 10,
            },
        ]
    )
    results = evaluate_template_fingerprint(reader, template, 1)
    assert [item["status"] for item in results] == ["match", "match"]
    assert results[0]["value"] == pytest.approx(230.4)
    assert results[1]["value"] == 5
    assert results[0]["index"] == 0
    assert results[1]["address"] == 4


def test_evaluate_fingerprint_mismatch_reports_range() -> None:
    template = _fingerprint_template(
        [
            {
                "register_type": "input",
                "address": 0,
                "data_type": "uint16",
                "min_value": 80,
                "max_value": 300,
            },
        ]
    )
    results = evaluate_template_fingerprint(lambda *args: [7], template, 1)
    assert results[0]["status"] == "mismatch"
    assert results[0]["value"] == 7
    assert "outside the expected range" in results[0]["error"]


def test_evaluate_fingerprint_read_errors_are_isolated() -> None:
    def reader(address, register_type, count, slave):
        if address == 0:
            raise TimeoutError("no response received from slave")
        return [3]

    template = _fingerprint_template(
        [
            {
                "register_type": "input",
                "address": 0,
                "data_type": "uint16",
                "min_value": 1,
                "max_value": 10,
            },
            {
                "register_type": "holding",
                "address": 4,
                "data_type": "uint16",
                "min_value": 1,
                "max_value": 10,
            },
        ]
    )
    results = evaluate_template_fingerprint(reader, template, 1)
    assert results[0]["status"] == "error"
    assert "no response" in results[0]["error"]
    assert results[1]["status"] == "match"


def test_evaluate_fingerprint_rejects_malformed_probes() -> None:
    template = _fingerprint_template(
        [
            "not-a-mapping",
            {"register_type": "coil", "address": 0, "min_value": 0, "max_value": 1},
            {"register_type": "holding", "address": 0, "data_type": "bogus"},
            {"register_type": "holding", "address": 0},  # missing min/max
            {
                "register_type": "holding",
                "address": "x",
                "min_value": 0,
                "max_value": 1,
            },
            {
                "register_type": "holding",
                "address": 70000,
                "min_value": 0,
                "max_value": 1,
            },
        ]
    )
    results = evaluate_template_fingerprint(lambda *args: [1], template, 1)
    assert [item["status"] for item in results] == ["error"] * 6
    assert "must be a mapping" in results[0]["error"]
    assert "holding or input" in results[1]["error"]
    assert "Unsupported fingerprint data type" in results[2]["error"]
    assert results[3]["error"]  # missing min_value raises KeyError text
    assert results[4]["error"]  # non-integer address
    assert "between 0 and 65535" in results[5]["error"]


def test_evaluate_design_includes_fingerprint_summary() -> None:
    def reader(address, register_type, count, slave):
        if register_type == "input":
            return _float_words(230.4)  # entity read + fingerprint probe
        return [5]  # holding fingerprint probe, inside 1-10

    template = load_template_draft(FINGERPRINT_YAML)
    result = evaluate_template_design(reader, template)
    assert result["fingerprint_total"] == 2
    assert result["fingerprint_matched"] == 2
    assert result["fingerprint_all_matched"] is True
    assert result["passed"] == 1
    assert result["all_passed"] is True


def test_evaluate_design_fingerprint_mismatch_keeps_entity_results() -> None:
    def reader(address, register_type, count, slave):
        if register_type == "holding":
            return [9999]  # outside 1-10 fingerprint range
        return _float_words(230.4)

    result = evaluate_template_design(reader, load_template_draft(FINGERPRINT_YAML))
    assert result["passed"] == 1
    assert result["failed"] == 0
    assert result["fingerprint_matched"] == 1
    assert result["fingerprint_total"] == 2
    assert result["fingerprint_all_matched"] is False
    mismatch = result["fingerprint"][1]
    assert mismatch["status"] == "mismatch"


def test_evaluate_design_without_fingerprint_reports_empty() -> None:
    result = evaluate_template_design(lambda *args: [1], _fingerprint_template())
    assert result["fingerprint"] == []
    assert result["fingerprint_total"] == 0
    assert result["fingerprint_matched"] == 0
    assert result["fingerprint_all_matched"] is False


def test_evaluate_design_skips_fingerprint_without_test_reads() -> None:
    def reader(*args):
        raise AssertionError("must not read when test_reads is False")

    result = evaluate_template_design(
        reader, load_template_draft(FINGERPRINT_YAML), test_reads=False
    )
    assert result["fingerprint"] == []
    assert result["fingerprint_total"] == 0


async def test_async_validate_reports_fingerprint_results() -> None:
    class FakeCoordinator:
        def read_raw_words(self, address, register_type, count, slave):
            if register_type == "input":
                return _float_words(230.4)
            return [5]

    hass = SimpleNamespace(async_add_executor_job=_run_in_executor)
    result = await async_validate_template_design(
        hass, FakeCoordinator(), FINGERPRINT_YAML
    )
    assert result["valid"] is True
    assert result["fingerprint_total"] == 2
    assert result["fingerprint_all_matched"] is True


# ───────────── v2.7.1: located structural errors (line / column / path) ─────────────


def _draft_error(content: str) -> TemplateDraftError:
    with pytest.raises(TemplateDraftError) as excinfo:
        load_template_draft(content)
    return excinfo.value


def test_template_draft_error_is_a_value_error_with_location_dict() -> None:
    err = TemplateDraftError("boom", line=3, column=7, path="entities[0].address")
    assert isinstance(err, ValueError)
    assert str(err) == "boom"
    assert err.as_dict() == {
        "error_line": 3,
        "error_column": 7,
        "error_path": "entities[0].address",
    }
    assert TemplateDraftError("x").as_dict() == {
        "error_line": None,
        "error_column": None,
        "error_path": None,
    }


def test_load_template_draft_locates_entity_type_error() -> None:
    err = _draft_error(
        "name: X\n"
        "entities:\n"
        "  - name: A\n"
        "    entity_type: sensor\n"
        "    register_type: holding\n"
        "    address: 1\n"
        "  - name: B\n"
        "    entity_type: bogus\n"
        "    address: 2\n"
    )
    assert "Unsupported entity type" in str(err)
    assert (err.line, err.column) == (8, 18)
    assert err.path == "entities[1].entity_type"


def test_load_template_draft_locates_address_error_inside_entity() -> None:
    err = _draft_error(
        "name: X\n"
        "entities:\n"
        "  - name: A\n"
        "    entity_type: sensor\n"
        "    register_type: holding\n"
        "    address: 70000\n"
    )
    assert "address" in str(err).lower()
    assert (err.line, err.column) == (6, 14)
    assert err.path == "entities[0].address"

    err = _draft_error(
        "name: X\n"
        "entities:\n"
        "  - name: A\n"
        "    entity_type: sensor\n"
        "    register_type: coil\n"
        "    address: 1\n"
    )
    assert "register type" in str(err)
    assert (err.line, err.column) == (5, 20)
    assert err.path == "entities[0].register_type"


def test_load_template_draft_falls_back_to_entity_first_line() -> None:
    # "Entity name must be a non-empty string" names the ``name`` key, which
    # the draft omits — the error is attributed to the entity's first line.
    err = _draft_error(
        "name: X\n"
        "entities:\n"
        "  - name: A\n"
        "    entity_type: sensor\n"
        "    register_type: holding\n"
        "    address: 1\n"
        "  - entity_type: sensor\n"
        "    register_type: holding\n"
        "    address: 2\n"
    )
    assert "Entity name" in str(err)
    assert (err.line, err.column) == (7, 5)
    assert err.path == "entities[1]"


def test_load_template_draft_locates_template_level_keys() -> None:
    err = _draft_error("name: X\ndefault_slave_id: 999\nentities: []\n")
    assert "default_slave_id" in str(err)
    assert (err.line, err.column) == (2, 19)
    assert err.path == "default_slave_id"

    err = _draft_error("name: X\nentities:\n  key: value\n")
    assert "entities must be a list" in str(err)
    assert (err.line, err.path) == (3, "entities")

    err = _draft_error("name: X\nentities: []\nfingerprint: nope\n")
    assert (err.line, err.path) == (3, "fingerprint")


def test_load_template_draft_template_error_wins_over_entity_error() -> None:
    # The template-level check fires first in validate_template; the
    # locator must not misattribute it to the (also invalid) entity.
    err = _draft_error(
        "name: X\n"
        "default_slave_id: 0\n"
        "entities:\n"
        "  - name: A\n"
        "    entity_type: bogus\n"
        "    address: 1\n"
    )
    assert "default_slave_id" in str(err)
    assert err.line == 2
    assert err.path == "default_slave_id"


def test_load_template_draft_unlocatable_errors_point_at_line_one() -> None:
    err = _draft_error("- just\n- a\n- list\n")
    assert err.line == 1
    assert err.path is None

    err = _draft_error("entities: []\n")  # neither name nor id
    assert "name" in str(err)
    assert err.line == 1


def test_load_template_draft_yaml_syntax_error_has_position() -> None:
    err = _draft_error("name: X\nentities:\n  - name: A\n    entity_type: [sensor\n")
    message = str(err)
    assert message.startswith("Invalid YAML syntax:")
    assert "\n" not in message  # single line, suitable for a toast
    assert err.path is None
    # An unterminated flow sequence is reported at its opening bracket.
    assert (err.line, err.column) == (4, 18)


def test_load_template_draft_yaml_indentation_error_points_at_offending_line() -> None:
    err = _draft_error("name: X\nentities:\n  - name: A\n   bad: indent\n")
    assert str(err).startswith("Invalid YAML syntax:")
    assert err.line == 4


def test_load_template_draft_empty_draft_has_no_location() -> None:
    err = _draft_error("   \n")
    assert "must not be empty" in str(err)
    assert err.as_dict() == {
        "error_line": None,
        "error_column": None,
        "error_path": None,
    }


def test_locate_template_error_never_raises_on_unparseable_input() -> None:
    assert locate_template_error("name: [", None, "anything") == (None, None, None)
    assert locate_template_error("name: X\n", None, "weird") == (1, 1, None)


async def test_async_validate_template_design_propagates_location() -> None:
    hass = SimpleNamespace(async_add_executor_job=_run_in_executor)
    with pytest.raises(TemplateDraftError) as excinfo:
        await async_validate_template_design(
            hass,
            SimpleNamespace(),
            "name: X\nentities:\n  - name: A\n    entity_type: bogus\n    address: 1\n",
        )
    assert excinfo.value.line == 4
    assert excinfo.value.path == "entities[0].entity_type"
    assert excinfo.value.as_dict()["error_column"] == 18
