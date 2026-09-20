"""Serial-transport mechanics shared by coordinator polling and bus scans."""

from __future__ import annotations

import inspect
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
    # Do not retry a write after TypeError: older clients may silently accept
    # arbitrary keyword arguments, leaving the request addressed to slave 0.
    parameters = inspect.signature(method).parameters
    unit_keyword = "slave" if "slave" in parameters else "device_id"
    return method(*args, **{unit_keyword: slave}, **kwargs)
