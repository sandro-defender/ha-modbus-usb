"""Validate user-supplied configuration before it reaches the serial bus."""

from __future__ import annotations

import math
from typing import Any

from .const import DATA_TYPE_WORD_COUNT, DATA_TYPES

_REGISTER_TYPES = {
    "sensor": {"holding", "input"},
    "switch": {"coil", "holding"},
    "binary_sensor": {"coil", "discrete", "holding", "input"},
    "number": {"holding"},
}


def bounded_int(value: Any, name: str, minimum: int, maximum: int) -> int:
    """Accept integers and integer form strings, without silently truncating."""
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError) as err:
        raise ValueError(f"{name} must be an integer") from err
    if isinstance(value, bool) or (not isinstance(value, str) and result != value):
        raise ValueError(f"{name} must be an integer")
    if not minimum <= result <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return result


def validate_entity(entity: dict[str, Any]) -> dict[str, Any]:
    """Return a normalized copy, retaining board-specific extension fields."""
    if not isinstance(entity, dict):
        raise ValueError("Each entity must be a mapping")
    entity = dict(entity)
    entity_type = entity.get("entity_type")
    if not isinstance(entity_type, str) or entity_type not in _REGISTER_TYPES:
        raise ValueError(f"Unsupported entity type: {entity_type!r}")
    if not isinstance(entity.get("name"), str) or not entity["name"].strip():
        raise ValueError("Entity name must be a non-empty string")
    register_type = entity.get("register_type")
    if (
        not isinstance(register_type, str)
        or register_type not in _REGISTER_TYPES[entity_type]
    ):
        raise ValueError(f"A {entity_type} cannot use register type {register_type!r}")
    entity["address"] = bounded_int(entity.get("address"), "address", 0, 65535)
    data_type = entity.get("data_type", "uint16")
    if data_type not in DATA_TYPES and not (
        register_type in {"coil", "discrete"} and data_type == "bool"
    ):
        raise ValueError(f"Unsupported data type: {data_type!r}")
    count = (
        DATA_TYPE_WORD_COUNT[data_type] if register_type in {"holding", "input"} else 1
    )
    if entity["address"] + count > 65536:
        raise ValueError("Register span exceeds address 65535")

    for key in ("id", "device_id"):
        if key in entity and (
            not isinstance(entity[key], str) or not entity[key].strip()
        ):
            raise ValueError(f"{key} must be a non-empty string")
    for key in ("slave_id", "on_value", "off_value", "state_on_value"):
        if entity.get(key) in (None, ""):
            entity.pop(key, None)
        elif key == "slave_id":
            entity[key] = bounded_int(entity[key], key, 1, 247)
        else:
            entity[key] = bounded_int(entity[key], key, 0, 65535)
    if entity.get("addresses") not in (None, ""):
        if (
            entity_type != "switch"
            or not isinstance(entity["addresses"], list)
            or not entity["addresses"]
        ):
            raise ValueError("Group switch addresses must be a non-empty list")
        entity["addresses"] = list(
            dict.fromkeys(
                bounded_int(address, "group address", 0, 65535)
                for address in entity["addresses"]
            )
        )
    else:
        entity.pop("addresses", None)
    for key in ("scale", "min_value", "max_value", "step", "scan_interval"):
        if entity.get(key) in (None, ""):
            entity.pop(key, None)
            continue
        try:
            entity[key] = float(entity[key])
        except (TypeError, ValueError, OverflowError) as err:
            raise ValueError(f"{key} must be a finite number") from err
        if not math.isfinite(entity[key]):
            raise ValueError(f"{key} must be a finite number")
    if "scan_interval" in entity and entity["scan_interval"] <= 0:
        raise ValueError("scan_interval must be greater than zero")
    if entity_type == "number":
        if entity.get("scale", 1) == 0:
            raise ValueError("Number scale must not be zero")
        if entity.get("step", 1) <= 0:
            raise ValueError("Number step must be positive")
        if entity.get("min_value", 0) > entity.get("max_value", 65535):
            raise ValueError("Number minimum must not exceed its maximum")
        if entity.get("mode", "slider") not in ("slider", "box"):
            raise ValueError("Number mode must be slider or box")
    return entity


def validate_template(data: Any) -> dict[str, Any]:
    """Validate template structure and all entity definitions before saving."""
    if not isinstance(data, dict):
        raise ValueError(
            "Template YAML must define a mapping/dictionary at the root level"
        )
    data = dict(data)
    if not data.get("name") and not data.get("id"):
        raise ValueError("Template must contain at least 'name' or 'id'")
    for key in ("name", "id"):
        if key in data and (not isinstance(data[key], str) or not data[key].strip()):
            raise ValueError(f"Template {key} must be a non-empty string")
    data["default_slave_id"] = bounded_int(
        data.get("default_slave_id", 1), "default_slave_id", 1, 247
    )
    if not isinstance(data.get("entities", []), list):
        raise ValueError("Template entities must be a list")
    data["entities"] = [validate_entity(entity) for entity in data.get("entities", [])]
    if not isinstance(data.get("device_controls", {}), dict):
        raise ValueError("Template device_controls must be a mapping")
    if not isinstance(data.get("fingerprint", []), list):
        raise ValueError("Template fingerprint must be a list")
    return data
