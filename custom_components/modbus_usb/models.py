"""Typed configuration models for the Modbus USB Controller integration.

These TypedDicts document the shape of the hub/device/entity dictionaries
stored in config entry data and options. Every key is optional (total=False)
because historical saves and sidebar edits may omit fields; readers must use
``.get()`` with sensible defaults instead of direct key access.
"""

from __future__ import annotations

from typing import Any, TypedDict


class HubData(TypedDict, total=False):
    """Serial connection settings stored in config entry data."""

    port: str
    baudrate: int
    bytesize: int
    parity: str
    stopbits: int
    slave_id: int


class DeviceConfig(TypedDict, total=False):
    """A logical board on the hub's RS-485 bus (entry options)."""

    id: str
    name: str
    manufacturer: str
    model: str
    description: str
    image: str
    info_url: str
    slave_id: int
    enabled: bool
    m0_short: bool
    assumed_state: bool
    device_controls: dict[str, Any]


class EntityConfig(TypedDict, total=False):
    """A sensor/switch/number/binary_sensor mapping (entry options)."""

    id: str
    entity_type: str
    name: str
    device_id: str
    register_type: str
    address: int
    addresses: list[int]
    data_type: str
    scale: float
    unit_of_measurement: str
    device_class: str
    state_class: str
    on_value: int
    off_value: int
    state_on_value: int
    min_value: float
    max_value: float
    step: float
    mode: str
    slave_id: int
