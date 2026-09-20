"""Board-specific protocol support.

Grouped block readers live in one module per board family. To add a new
board with grouped polling, add its reader module here and register its
template ``device_controls.protocol`` value in :data:`BLOCK_READERS` — the
coordinator's poll loop needs no other changes.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from ..const import CONF_DEVICE_CONTROLS, CONF_M0_SHORT, CONF_MODEL
from ..models import DeviceConfig
from . import r4d6f20

if TYPE_CHECKING:
    from ..coordinator import ModbusUsbCoordinator
    from ..models import EntityConfig

# Template protocol value for the Eletechsup R4D6F20 multifunction board.
PROTOCOL_R4D6F20 = "eletechsup_r4d6f20"

# protocol -> (default reader, M0-shorted reader or None).
BLOCK_READERS: dict[
    str,
    tuple[
        Callable[
            [ModbusUsbCoordinator, list[EntityConfig], int],
            tuple[dict[str, Any], set[str]],
        ],
        Callable[
            [ModbusUsbCoordinator, list[EntityConfig], int],
            tuple[dict[str, Any], set[str]],
        ]
        | None,
    ],
] = {
    PROTOCOL_R4D6F20: (r4d6f20.read_command1_blocks, r4d6f20.read_command2_blocks),
}


def select_block_reader(device: DeviceConfig):
    """Return the grouped block reader for a device, or None for default polling.

    The model-name fallback keeps older saves working: they predate the
    template ``protocol`` metadata but still carry the board model.
    """
    controls = device.get(CONF_DEVICE_CONTROLS, {})
    protocol = controls.get("protocol")
    if protocol is None and "r4d6f20" in str(device.get(CONF_MODEL, "")).lower():
        protocol = PROTOCOL_R4D6F20
    readers = BLOCK_READERS.get(str(protocol)) if protocol else None
    if readers is None:
        return None
    default_reader, m0_reader = readers
    if device.get(CONF_M0_SHORT, False) and m0_reader is not None:
        return m0_reader
    return default_reader
