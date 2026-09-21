"""Interactive Template Designer: live validation against a connected board.

The designer takes a draft template YAML, validates its structure, and then
test-reads every entity's registers from the real bus. Each response is
decoded into all supported data types so users can confirm their mapping
before saving a new custom template. Declared template fingerprints are
probed as well, so users see per-entry match/no-match results for the same
read-only checks the RS-485 scanner uses for template suggestions.

Structural errors are located in the draft (since v2.7.1): YAML syntax
errors carry the parser's problem mark, and validation errors are mapped
back to the offending entity/key through the YAML node tree, so the
designer can highlight the failing line in its editor gutter.

The synchronous core accepts a plain ``read_words`` callable so it stays
trivially unit-testable without Home Assistant.
"""

from __future__ import annotations

import functools
import logging
import re
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
from .validation import validate_entity, validate_template

_LOGGER = logging.getLogger(__name__)

MAX_DESIGNER_ENTITIES = 64

ReadWordsCallable = Callable[[int, str, int, int], list[int]]

# Template-level keys a structural error message may refer to, longest
# first so ``device_controls`` wins over ``id`` and similar substrings.
_TEMPLATE_KEYS = (
    "default_slave_id",
    "device_controls",
    "fingerprint",
    "entities",
    "name",
    "id",
)


class TemplateDraftError(ValueError):
    """A structural template error located in the draft YAML.

    ``line``/``column`` are 1-based positions in the draft (None when the
    error could not be located) and ``path`` is a dotted key path such as
    ``entities[2].entity_type`` (None for YAML syntax errors).
    """

    def __init__(
        self,
        message: str,
        *,
        line: int | None = None,
        column: int | None = None,
        path: str | None = None,
    ) -> None:
        super().__init__(message)
        self.line = line
        self.column = column
        self.path = path

    def as_dict(self) -> dict[str, Any]:
        """Location fields in the shape the designer WebSocket reply uses."""
        return {
            "error_line": self.line,
            "error_column": self.column,
            "error_path": self.path,
        }


def _yaml_error_message(err: yaml.YAMLError) -> str:
    """One-line description of a PyYAML error (marks are reported apart)."""
    problem = getattr(err, "problem", None)
    if not problem:
        return " ".join(str(err).split())
    context = getattr(err, "context", None)
    return f"{problem} ({context})" if context else str(problem)


def _yaml_error_position(
    err: yaml.YAMLError, yaml_content: str
) -> tuple[int | None, int | None]:
    """Return the 1-based ``(line, column)`` a PyYAML error points at.

    The problem mark wins, except when it sits past the last non-blank line
    (an unterminated construct reported at the stream end): the context
    mark — where the construct started — is the line worth highlighting.
    """
    problem_mark = getattr(err, "problem_mark", None)
    context_mark = getattr(err, "context_mark", None)
    lines = yaml_content.split("\n")
    last_content_index = max(
        (index for index, text in enumerate(lines) if text.strip()), default=0
    )
    mark = problem_mark
    if (
        context_mark is not None
        and problem_mark is not None
        and problem_mark.line > last_content_index
    ):
        mark = context_mark
    if mark is None:
        mark = context_mark
    if mark is None:
        return None, None
    return mark.line + 1, mark.column + 1


def _compose_yaml(yaml_content: str) -> yaml.Node | None:
    """Return the YAML node tree of the draft (None when it cannot parse)."""
    try:
        return yaml.compose(yaml_content, Loader=yaml.SafeLoader)
    except yaml.YAMLError:
        return None


def _mapping_entry(node: yaml.Node | None, key: str) -> tuple[Any, Any]:
    """Return ``(key_node, value_node)`` of ``key`` in a mapping node."""
    if isinstance(node, yaml.MappingNode):
        for key_node, value_node in node.value:
            if getattr(key_node, "value", None) == key:
                return key_node, value_node
    return None, None


def _node_position(node: yaml.Node | None) -> tuple[int | None, int | None]:
    mark = getattr(node, "start_mark", None)
    if mark is None:
        return None, None
    return mark.line + 1, mark.column + 1


def _message_mentions(message: str, key: str) -> bool:
    """True when ``key`` (or its spaced form, e.g. ``entity type``) is named."""
    lowered = message.lower()
    for variant in (key, key.replace("_", " ")):
        if re.search(rf"\b{re.escape(variant)}\b", lowered):
            return True
    return False


def _is_template_level_error(raw: Any, message: str) -> bool:
    """True when ``message`` comes from the template checks, not an entity.

    ``validate_template`` checks name/id and ``default_slave_id`` before the
    entities and ``device_controls``/``fingerprint`` after them, so the same
    draft validated with an empty entity list reproduces exactly the
    template-level errors: an identical message means the error is not an
    entity error even if some entity is also invalid.
    """
    if not isinstance(raw, dict):
        return True
    try:
        validate_template({**raw, "entities": []})
    except ValueError as err:
        return str(err) == message
    return False


def _find_failing_entity(raw: Any) -> int | None:
    """Return the index of the first entity that fails validation, if any."""
    if not isinstance(raw, dict) or not isinstance(raw.get("entities"), list):
        return None
    for index, entity in enumerate(raw["entities"]):
        try:
            validate_entity(entity)
        except ValueError:
            return index
    return None


def locate_template_error(
    yaml_content: str, raw: Any, message: str
) -> tuple[int | None, int | None, str | None]:
    """Map a structural validation error back to ``(line, column, path)``.

    Entity errors are attributed to the first entity that fails validation,
    and inside it to the key the message names (``entity_type`` for
    "Unsupported entity type", ``address`` for "address must be…"), falling
    back to the entity's first line. Template-level errors point at the
    named root key (``default_slave_id``, ``entities``, …) and otherwise at
    line 1. Never raises.
    """
    root = _compose_yaml(yaml_content)
    if root is None:
        return None, None, None
    index = (
        None if _is_template_level_error(raw, message) else _find_failing_entity(raw)
    )
    if index is not None:
        _, entities_node = _mapping_entry(root, "entities")
        entity_node = (
            entities_node.value[index]
            if isinstance(entities_node, yaml.SequenceNode)
            and index < len(entities_node.value)
            else None
        )
        path = f"entities[{index}]"
        if isinstance(entity_node, yaml.MappingNode):
            keys = sorted(
                (
                    str(key_node.value)
                    for key_node, _ in entity_node.value
                    if isinstance(getattr(key_node, "value", None), str)
                ),
                key=len,
                reverse=True,
            )
            for key in keys:
                if _message_mentions(message, key):
                    _, value_node = _mapping_entry(entity_node, key)
                    line, column = _node_position(value_node)
                    return line, column, f"{path}.{key}"
        line, column = _node_position(entity_node)
        if line is None:
            line, column = _node_position(entities_node)
        return line or 1, column or 1, path
    for key in _TEMPLATE_KEYS:
        if _message_mentions(message, key):
            key_node, value_node = _mapping_entry(root, key)
            line, column = _node_position(value_node or key_node)
            if line is not None:
                return line, column, key
    line, column = _node_position(root)
    return line or 1, column or 1, None


def load_template_draft(yaml_content: str) -> dict[str, Any]:
    """Parse and structurally validate a draft template.

    Raises ``TemplateDraftError`` (a ValueError) with a user-readable message
    on malformed YAML or on any structural/entity validation problem; the
    error carries the draft line/column (and key path) of the problem when
    it can be located, so the designer can highlight it in the editor.
    """
    if not isinstance(yaml_content, str) or not yaml_content.strip():
        raise TemplateDraftError("Template YAML must not be empty")
    try:
        raw = yaml.safe_load(yaml_content)
    except yaml.YAMLError as err:
        line, column = _yaml_error_position(err, yaml_content)
        raise TemplateDraftError(
            f"Invalid YAML syntax: {_yaml_error_message(err)}",
            line=line,
            column=column,
        ) from err
    try:
        return validate_template(raw)
    except ValueError as err:
        line, column, path = locate_template_error(yaml_content, raw, str(err))
        raise TemplateDraftError(str(err), line=line, column=column, path=path) from err


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

    # HA's async_add_executor_job(target, *args) forwards positional args
    # only — keyword arguments raise TypeError, so bind them with functools.partial.
    return await hass.async_add_executor_job(
        functools.partial(
            evaluate_template_design,
            _read_words,
            template,
            slave_id=slave_id,
            test_reads=test_reads,
        )
    )
