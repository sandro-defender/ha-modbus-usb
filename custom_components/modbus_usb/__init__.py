"""The Modbus USB Controller integration."""
from __future__ import annotations

import logging
import os

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .api import async_register_api
from .const import (
    CONF_BAUDRATE,
    CONF_BYTESIZE,
    CONF_DEVICES,
    CONF_MANUFACTURER,
    CONF_MODEL,
    CONF_NAME,
    CONF_PARITY,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_SLAVE_ID,
    CONF_STOPBITS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .coordinator import ModbusUsbCoordinator
from .services import async_register_services, async_unregister_services
from .templates import ensure_templates_dir

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "switch", "binary_sensor", "number"]

_PANEL_REGISTERED = False  # module-level guard so we only register once


async def async_setup(hass: HomeAssistant, config: dict) -> bool:  # noqa: ARG001
    """Global (YAML) setup hook — register panel, API, and templates once."""
    await _async_register_panel(hass)
    await async_register_api(hass)
    await hass.async_add_executor_job(ensure_templates_dir, hass)
    return True


async def _async_register_panel(hass: HomeAssistant) -> None:
    """Register the Modbus USB custom panel and serve the www/ directory."""
    global _PANEL_REGISTERED  # noqa: PLW0603
    if _PANEL_REGISTERED:
        return
    _PANEL_REGISTERED = True

    # Serve static files from the www/ sub-directory next to this file.
    # Prefer the modern async API; only fall back to the deprecated,
    # blocking `register_static_path` on older HA cores that don't have
    # `async_register_static_paths` yet. Checking hasattr() for the OLD
    # method first (as before) meant current installs — which still keep
    # the deprecated method around for back-compat — would always take
    # the blocking path instead of the async one.
    www_path = os.path.join(os.path.dirname(__file__), "www")
    if hasattr(hass.http, "async_register_static_paths"):
        from homeassistant.components.http import StaticPathConfig
        await hass.http.async_register_static_paths([
            StaticPathConfig("/modbus_usb_panel", www_path, cache_headers=False)
        ])
    else:
        hass.http.register_static_path(
            "/modbus_usb_panel",
            www_path,
            cache_headers=False,
        )

    # Register the sidebar panel
    try:
        from homeassistant.components import frontend
        frontend.async_register_built_in_panel(
            hass,
            component_name="iframe",
            sidebar_title="Modbus USB",
            sidebar_icon="mdi:usb",
            frontend_url_path="modbus-usb",
            config={"url": "/modbus_usb_panel/modbus-panel.html"},
            require_admin=False,
        )
        _LOGGER.debug("Modbus USB sidebar panel registered")
    except Exception:
        _LOGGER.warning("Failed to register sidebar panel", exc_info=True)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Modbus USB Controller from a config entry."""
    from pymodbus.client import ModbusSerialClient

    def _build_client() -> ModbusSerialClient:
        return ModbusSerialClient(
            port=entry.data[CONF_PORT],
            baudrate=entry.data[CONF_BAUDRATE],
            bytesize=entry.data[CONF_BYTESIZE],
            parity=entry.data[CONF_PARITY],
            stopbits=entry.data[CONF_STOPBITS],
            timeout=3,
        )

    client = await hass.async_add_executor_job(_build_client)
    connected = await hass.async_add_executor_job(client.connect)
    if not connected:
        _LOGGER.warning(
            "Could not open serial port %s on initial connect; will keep retrying",
            entry.data[CONF_PORT],
        )

    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    coordinator = ModbusUsbCoordinator(
        hass=hass,
        client=client,
        slave_id=entry.data[CONF_SLAVE_ID],
        scan_interval=scan_interval,
        entry_id=entry.entry_id,
    )

    # If the first refresh fails (e.g. raises ConfigEntryNotReady because the
    # device isn't reachable yet), HA will retry async_setup_entry later.
    # Without closing the client here first, the already-opened serial port
    # is leaked and a new client/port is opened on every retry.
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        await hass.async_add_executor_job(client.close)
        raise

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Register hub and child devices in HA Device Registry
    from homeassistant.helpers import device_registry as dr

    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer="Modbus USB",
        model="Modbus Serial Hub",
    )

    for dev in entry.options.get(CONF_DEVICES, []):
        dev_id = dev.get("id")
        if dev_id:
            device_registry.async_get_or_create(
                config_entry_id=entry.entry_id,
                identifiers={(DOMAIN, f"{entry.entry_id}_{dev_id}")},
                name=dev.get(CONF_NAME, f"Device {dev_id}"),
                manufacturer=dev.get(CONF_MANUFACTURER, "Modbus USB"),
                model=dev.get(CONF_MODEL, "Modbus Device"),
                via_device=(DOMAIN, entry.entry_id),
            )

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register services and API (idempotent)
    await async_register_services(hass)
    await async_register_api(hass)
    await hass.async_add_executor_job(ensure_templates_dir, hass)

    # Ensure panel is registered (idempotent)
    await _async_register_panel(hass)

    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the integration when options (e.g. entity list) change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator: ModbusUsbCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await hass.async_add_executor_job(coordinator.client.close)

    # If no more entries, unregister services
    if not hass.data.get(DOMAIN):
        await async_unregister_services(hass)

    return unload_ok