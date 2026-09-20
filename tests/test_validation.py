"""Regression coverage for template input validation."""

import pytest

from custom_components.modbus_usb.validation import validate_entity, validate_template


def test_template_validation_normalizes_and_preserves_extensions() -> None:
    template = validate_template({
        "name": "Device",
        "entities": [{
            "name": "Switch", "entity_type": "switch", "register_type": "holding",
            "address": "3", "addresses": ["3", 4, 3], "slave_id": "2",
            "on_value": "256", "assumed_state": True,
        }],
    })

    entity = template["entities"][0]
    assert entity["address"] == 3
    assert entity["addresses"] == [3, 4]
    assert entity["slave_id"] == 2
    assert entity["on_value"] == 256
    assert entity["assumed_state"] is True


@pytest.mark.parametrize("entity", [
    {"name": "Bad", "entity_type": "sensor", "register_type": "holding", "address": -1},
    {"name": "Bad", "entity_type": "number", "register_type": "holding", "address": 0, "scale": 0},
    {"name": "Bad", "entity_type": "switch", "register_type": "coil", "address": 0, "addresses": []},
])
def test_invalid_entities_are_rejected(entity: dict) -> None:
    with pytest.raises(ValueError):
        validate_entity(entity)
