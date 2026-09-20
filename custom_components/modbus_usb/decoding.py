"""Pure decoding and normalization helpers.

These functions intentionally avoid Home Assistant imports so they stay
trivially unit-testable and reusable from platforms, the coordinator, and
the WebSocket API.
"""

from __future__ import annotations

import logging
import struct
from enum import Enum
from typing import Any

from .const import (
    DATA_TYPE_FLOAT32,
    DATA_TYPE_INT16,
    DATA_TYPE_INT32,
    DATA_TYPE_UINT16,
    DATA_TYPE_UINT32,
)

_LOGGER = logging.getLogger(__name__)


def normalize_enum(value: Any, enum_cls: type[Enum], entity_name: str) -> Any:
    """Return a valid enum member, treating "none"/empty/invalid values as None.

    The sidebar and options flows store the literal string ``"none"`` for
    "no class selected". Home Assistant rejects unknown device/state class
    strings while adding the entity, which previously removed the whole
    platform from setup, so unsupported values fall back to None with a
    warning instead of breaking every entity of that platform.
    """
    if value in (None, "", "none"):
        return None
    try:
        return enum_cls(value)
    except ValueError:
        _LOGGER.warning(
            "Unsupported %s value %r for entity %s; using no class instead",
            enum_cls.__name__,
            value,
            entity_name,
        )
        return None


def as_float(value: Any, default: float) -> float:
    """Return ``value`` as float, falling back to ``default`` for None/"" /garbage.

    Sidebar edits can persist explicit ``null`` values for optional numeric
    settings; ``float(None)`` raised TypeError and removed the platform.
    """
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def decode_words(words: list[int], data_type: str) -> float | int:
    """Decode a list of 16-bit register words into a number."""
    if data_type == DATA_TYPE_UINT16:
        return words[0]
    if data_type == DATA_TYPE_INT16:
        val = words[0]
        return val - 0x10000 if val >= 0x8000 else val
    # 32-bit types: big-endian word order (high word first)
    raw = struct.pack(">HH", words[0], words[1])
    if data_type == DATA_TYPE_UINT32:
        return struct.unpack(">I", raw)[0]
    if data_type == DATA_TYPE_INT32:
        return struct.unpack(">i", raw)[0]
    if data_type == DATA_TYPE_FLOAT32:
        return struct.unpack(">f", raw)[0]
    return words[0]
