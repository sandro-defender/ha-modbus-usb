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
    # Prefer the keyword exposed by the client signature. Some legacy clients
    # accept arbitrary kwargs but reject ``device_id`` only when called, so
    # retry solely for that exact unsupported-keyword error. Other TypeErrors
    # can originate inside a write and must never be retried.
    parameters = inspect.signature(method).parameters
    unit_keyword = "slave" if "slave" in parameters else "device_id"
    try:
        return method(*args, **{unit_keyword: slave}, **kwargs)
    except TypeError as err:
        if (
            unit_keyword != "device_id"
            or "unexpected keyword argument 'device_id'" not in str(err)
        ):
            raise
        return method(*args, slave=slave, **kwargs)
