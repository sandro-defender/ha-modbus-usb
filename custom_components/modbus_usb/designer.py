"""Interactive Template Designer: live validation against a connected board.

The designer takes a draft template YAML, validates its structure, and then
test-reads every entity's registers from the real bus. Each response is
decoded into all supported data types so users can confirm their mapping
before saving a new custom template.

The synchronous core accepts a plain ``read_words`` callable so it stays
trivially unit-testable without Home Assistant.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

import yaml

from .const import (
    DATA_TYPE_WORD_COUNT,
    DATA_TYPES,
    REGISTER_TYPE_COIL,
    REGISTER_TYPE_DISCRETE,
)
from .decoding import as_float, decode_words
from .validation import validate_template

_LOGGER = logging.getLogger(__name__)

MAX_DESIGNER_ENTITIES = 64

ReadWordsCallable = Callable[[int, str, int, int], list[int]]


def load_template_draft(yaml_content: str) -> dict[str, Any]:
    """Parse and structurally validate a draft template.

    Raises ValueError with a user-readable message on malformed YAML or on
    any structural/entity validation problem.
    """
    if not isinstance(yaml_content, str) or not yaml_content.strip():
        raise ValueError("Template YAML must not be empty")
    try:
        raw = yaml.safe_load(yaml_content)
    except yaml.YAMLError as err:
        raise ValueError(f"Invalid YAML syntax: {err}") from err
    return validate_template(raw)


def _decode_all_types(words: list[int], count: int) -> dict[str, Any]:
    """Decode one register response into every compatible data type."""
    decodings: dict[str, Any] = {}
    for data_type in DATA_TYPES:
        if DATA_TYPE_WORD_COUNT[data_type] > len(words):
            continue
        try:
            decodings[data_type] = decode_words(words[:count], data_type)
        except ValueError:
            continue
    return decodings


def evaluate_template_design(
    read_words: ReadWordsCallable,
    template: dict[str, Any],
    *,
    slave_id: int | None = None,
    test_reads: bool = True,
) -> dict[str, Any]:
    """Test-read every template entity and report decoded values.

    ``read_words(address, register_type, count, slave_id)`` must return the
    raw register words (or coil bits as 0/1) or raise on failure.
    """
    started = time.monotonic()
    effective_slave = int(slave_id or template.get("default_slave_id", 1))
    if not 1 <= effective_slave <= 247:
        raise ValueError("Slave ID must be between 1 and 247")

    entities = list(template.get("entities", []))
    truncated = len(entities) > MAX_DESIGNER_ENTITIES
    entity_results: list[dict[str, Any]] = []

    for entity in entities[:MAX_DESIGNER_ENTITIES]:
        name = str(entity.get("name", "Unnamed"))
        register_type = entity.get("register_type")
        address = int(entity.get("address", 0))
        data_type = entity.get("data_type", "uint16")
        if register_type in (REGISTER_TYPE_COIL, REGISTER_TYPE_DISCRETE):
            count = 1
        else:
            count = DATA_TYPE_WORD_COUNT.get(data_type, 1)

        item: dict[str, Any] = {
            "name": name,
            "address": address,
            "register_type": register_type,
            "data_type": data_type,
            "word_count": count,
        }

        if not test_reads:
            item["status"] = "skipped"
            entity_results.append(item)
            continue

        try:
            words = [
                int(word)
                for word in read_words(address, register_type, count, effective_slave)
            ]
            item["raw_words"] = words
            if register_type in (REGISTER_TYPE_COIL, REGISTER_TYPE_DISCRETE):
                value = bool(words[0]) if words else None
                item["value"] = value
                item["decodings"] = {"bool": value}
            else:
                decodings = _decode_all_types(words, count)
                item["decodings"] = decodings
                declared = decodings.get(data_type)
                scale = as_float(entity.get("scale"), 1)
                item["value"] = None if declared is None else declared * scale
                item["scaled"] = scale != 1
            item["success"] = True
            item["status"] = "pass"
        except Exception as err:
            item["success"] = False
            item["status"] = "fail"
            item["error"] = str(err)
            _LOGGER.debug(
                "Designer test read failed for %s @%s: %s", name, address, err
            )
        entity_results.append(item)

    passed = sum(1 for item in entity_results if item["status"] == "pass")
    failed = sum(1 for item in entity_results if item["status"] == "fail")
    skipped = sum(1 for item in entity_results if item["status"] == "skipped")

    return {
        "valid": True,
        "template": {
            "name": template.get("name"),
            "id": template.get("id"),
            "default_slave_id": template.get("default_slave_id"),
        },
        "slave_id": effective_slave,
        "test_reads": bool(test_reads),
        "entity_count": len(entities),
        "tested": passed + failed,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "truncated": truncated,
        "all_passed": failed == 0 and passed > 0 if test_reads else skipped >= 0,
        "entities": entity_results,
        "duration_ms": round((time.monotonic() - started) * 1000, 1),
    }


async def async_validate_template_design(
    hass: Any,
    coordinator: Any,
    yaml_content: str,
    *,
    slave_id: int | None = None,
    test_reads: bool = True,
) -> dict[str, Any]:
    """Validate a draft template and test-read it against the live bus."""
    template = load_template_draft(yaml_content)

    def _read_words(address: int, register_type: str, count: int, slave: int):
        return coordinator.read_raw_words(address, register_type, count, slave)

    return await hass.async_add_executor_job(
        evaluate_template_design,
        _read_words,
        template,
        slave_id=slave_id,
        test_reads=test_reads,
    )
