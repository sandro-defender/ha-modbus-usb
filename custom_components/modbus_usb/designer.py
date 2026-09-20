"""Interactive Template Designer: live validation against a connected board.

The designer takes a draft template YAML, validates its structure, and then
test-reads every entity's registers from the real bus. Each response is
decoded into all supported data types so users can confirm their mapping
before saving a new custom template. Declared template fingerprints are
probed as well, so users see per-entry match/no-match results for the same
read-only checks the RS-485 scanner uses for template suggestions.

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
    REGISTER_TYPE_HOLDING,
    REGISTER_TYPE_INPUT,
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


def evaluate_template_fingerprint(
    read_words: ReadWordsCallable,
    template: dict[str, Any],
    slave_id: int,
) -> list[dict[str, Any]]:
    """Probe every declared fingerprint entry and report match/no-match.

    Mirrors the RS-485 scanner's template matching: each probe reads one
    holding/input register span, decodes it with the declared data type, and
    compares against the expected ``min_value``/``max_value`` range. Malformed
    probes and failed reads are reported per entry instead of aborting the
    whole validation run.
    """
    results: list[dict[str, Any]] = []
    for index, probe in enumerate(template.get("fingerprint") or []):
        item: dict[str, Any] = {"index": index}
        if not isinstance(probe, dict):
            item.update(
                {"status": "error", "error": "Fingerprint entry must be a mapping"}
            )
            results.append(item)
            continue
        register_type = probe.get("register_type")
        data_type = probe.get("data_type", "uint16")
        item.update(
            {
                "register_type": register_type,
                "address": probe.get("address"),
                "data_type": data_type,
                "min_value": probe.get("min_value"),
                "max_value": probe.get("max_value"),
            }
        )
        try:
            if register_type not in (REGISTER_TYPE_HOLDING, REGISTER_TYPE_INPUT):
                raise ValueError(
                    "Fingerprint register type must be holding or input, got "
                    f"{register_type!r}"
                )
            if data_type not in DATA_TYPES:
                raise ValueError(f"Unsupported fingerprint data type: {data_type!r}")
            address = int(probe.get("address"))
            if not 0 <= address <= 65535:
                raise ValueError("Fingerprint address must be between 0 and 65535")
            min_value = float(probe["min_value"])
            max_value = float(probe["max_value"])
            count = DATA_TYPE_WORD_COUNT.get(data_type, 1)
            words = [
                int(word)
                for word in read_words(address, register_type, count, slave_id)
            ]
            value = decode_words(words[:count], data_type)
            item.update({"address": address, "value": value})
            if min_value <= value <= max_value:
                item["status"] = "match"
            else:
                item["status"] = "mismatch"
                item["error"] = (
                    f"Value {value} is outside the expected range "
                    f"{min_value:g}–{max_value:g}"
                )
        except Exception as err:
            item["status"] = "error"
            item["error"] = str(err)
            _LOGGER.debug("Designer fingerprint probe %s failed: %s", index, err)
        results.append(item)
    return results


def evaluate_template_design(
    read_words: ReadWordsCallable,
    template: dict[str, Any],
    *,
    slave_id: int | None = None,
    test_reads: bool = True,
) -> dict[str, Any]:
    """Test-read every template entity and report decoded values.

    ``read_words(address, register_type, count, slave_id)`` must return the
    raw register words (or coil bits as 0/1) or raise on failure. Declared
    ``fingerprint`` probes are read and range-checked too (when test reads
    are enabled), reporting match/no-match per entry.
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

    # Fingerprint probes are real bus reads as well, so they follow the
    # test_reads switch together with the entity reads.
    fingerprint_results: list[dict[str, Any]] = []
    if test_reads:
        fingerprint_results = evaluate_template_fingerprint(
            read_words, template, effective_slave
        )
    fingerprint_matched = sum(
        1 for item in fingerprint_results if item["status"] == "match"
    )

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
        "fingerprint": fingerprint_results,
        "fingerprint_total": len(fingerprint_results),
        "fingerprint_matched": fingerprint_matched,
        "fingerprint_all_matched": bool(fingerprint_results)
        and fingerprint_matched == len(fingerprint_results),
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
