"""Tested eletechsup R413E16 register map: detection and command values."""

from __future__ import annotations

from ..const import CONF_REGISTER_TYPE, REGISTER_TYPE_HOLDING
from ..models import EntityConfig

# Verified FC06 holding-register commands (registers 1-16).
R413E16_ON_VALUE = 0x0100
R413E16_OFF_VALUE = 0x0200
# FC03 read-back values that report a channel as ON.
R413E16_STATE_ON_VALUES = frozenset({1, 0x0100})


def is_r413e16_switch_config(entity: EntityConfig) -> bool:
    """Return whether a config uses the tested R413E16 register map.

    Older panel versions could save numeric settings as JSON strings. Normalize
    them here so existing configured boards do not need to be recreated.
    """
    try:
        return (
            entity.get(CONF_REGISTER_TYPE) == REGISTER_TYPE_HOLDING
            and int(entity.get("on_value", 0)) == R413E16_ON_VALUE
            and int(entity.get("off_value", 0)) == R413E16_OFF_VALUE
        )
    except (TypeError, ValueError):
        return False
