"""Serial-transport mechanics shared by coordinator polling and bus scans."""

from __future__ import annotations

from typing import Any


def call_modbus_on_client(
    client: Any, method_name: str, *args: Any, slave: int, **kwargs: Any
) -> Any:
    """Call Pymodbus using its current or legacy unit-ID keyword.

    Pymodbus 3.8+ renamed ``slave`` to ``device_id``.  Supporting both
    keeps the integration working with the manifest's older supported
    versions as well as current Home Assistant installations.
    """
    method = getattr(client, method_name)
    try:
        return method(*args, device_id=slave, **kwargs)
    except TypeError as err:
        if "device_id" not in str(err):
            raise
        return method(*args, slave=slave, **kwargs)
