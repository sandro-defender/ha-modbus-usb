"""Home Assistant device-registry helpers for entities."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity import DeviceInfo

from .const import (
    CONF_DEVICE_ID,
    CONF_DEVICES,
    CONF_IMAGE,
    CONF_MANUFACTURER,
    CONF_MODEL,
    CONF_NAME,
    DOMAIN,
)
from .models import EntityConfig


def get_device_info(entry: ConfigEntry, ent: EntityConfig) -> DeviceInfo:
    """Return DeviceInfo for an entity, linking it to a separated device or the hub."""
    device_id = ent.get(CONF_DEVICE_ID)
    if device_id:
        devices = entry.options.get(CONF_DEVICES, [])
        device = next((d for d in devices if str(d.get("id")) == str(device_id)), None)
        if device:
            return DeviceInfo(
                identifiers={(DOMAIN, f"{entry.entry_id}_{device_id}")},
                name=device.get(CONF_NAME, f"Device {device_id}"),
                manufacturer=device.get(CONF_MANUFACTURER, "Modbus USB"),
                model=device.get(CONF_MODEL, "Modbus Device"),
                via_device=(DOMAIN, entry.entry_id),
            )
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer="Modbus USB",
        model="Modbus Serial Hub",
    )


def get_entity_picture(entry: ConfigEntry, ent: EntityConfig) -> str | None:
    """Return a device/template image URL for HA entity_picture, if set."""
    picture = ent.get(CONF_IMAGE)
    device_id = ent.get(CONF_DEVICE_ID)
    if device_id:
        devices = entry.options.get(CONF_DEVICES, [])
        device = next((d for d in devices if str(d.get("id")) == str(device_id)), None)
        if device and device.get(CONF_IMAGE):
            picture = device.get(CONF_IMAGE)
    return picture or None
