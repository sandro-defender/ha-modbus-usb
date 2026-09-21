"""The Modbus USB Controller integration."""

from __future__ import annotations

import logging
import os

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .api import async_register_api
from .const import (
    CONF_ADAPTER_IDENTITY,
    CONF_DEVICE_ID,
    CONF_DEVICES,
    CONF_ENTITIES,
    CONF_MANUFACTURER,
    CONF_MODEL,
    CONF_NAME,
    CONF_SCAN_INTERVAL,
    CONF_SLAVE_ID,
    CONF_TRANSPORT,
    DATA_PRESERVE_SERIAL_RELOAD,
    DATA_SKIP_DEVICE_RELOAD,
    DATA_SKIP_SERIAL_RELOAD,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    TRANSPORT_SERIAL,
    integration_version,
)
from .coordinator import ModbusUsbCoordinator
from .services import async_register_services, async_unregister_services
from .templates import ensure_templates_dir
from .transport import (
    TRANSPORT_LABELS,
    build_client,
    connection_config,
    connection_error_message,
    endpoint_of,
    transport_of,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "switch", "binary_sensor", "number"]

_PANEL_REGISTERED = f"{DOMAIN}_panel_registered"


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Global (YAML) setup hook — register panel, API, and templates once."""
    await _async_register_panel(hass)
    await async_register_api(hass)
    await hass.async_add_executor_job(ensure_templates_dir, hass)
    return True


async def _async_register_panel(hass: HomeAssistant) -> None:
    """Register the Modbus USB custom panel and serve the www/ directory."""
    if hass.data.get(_PANEL_REGISTERED):
        return

    # Serve static files from the www/ sub-directory next to this file.
    # Prefer the modern async API; only fall back to the deprecated,
    # blocking `register_static_path` on older HA cores that don't have
    # `async_register_static_paths` yet. Checking hasattr() for the OLD
    # method first (as before) meant current installs — which still keep
    # the deprecated method around for back-compat — would always take
    # the blocking path instead of the async one.
    www_path = os.path.join(os.path.dirname(__file__), "www")
    static_key = f"{DOMAIN}_static_registered"
    if not hass.data.get(static_key):
        if hasattr(hass.http, "async_register_static_paths"):
            from homeassistant.components.http import StaticPathConfig

            await hass.http.async_register_static_paths(
                [StaticPathConfig("/modbus_usb_panel", www_path, cache_headers=False)]
            )
        else:
            hass.http.register_static_path(
                "/modbus_usb_panel", www_path, cache_headers=False
            )
        hass.data[static_key] = True

    # Register the sidebar panel
    try:
        from homeassistant.components import frontend

        frontend.async_register_built_in_panel(
            hass,
            component_name="iframe",
            sidebar_title="Modbus USB",
            sidebar_icon="mdi:serial-port",
            frontend_url_path="modbus-usb",
            # The manifest version cache-busts the panel: releasing a new
            # version prevents an already-open browser from retaining old HTML.
            config={
                "url": f"/modbus_usb_panel/modbus-panel.html?v={integration_version()}"
            },
            require_admin=False,
        )
        hass.data[_PANEL_REGISTERED] = True
        _LOGGER.debug("Modbus USB sidebar panel registered")
    except Exception:
        _LOGGER.warning("Failed to register sidebar panel", exc_info=True)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Modbus USB Controller from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    coordinator = hass.data[DOMAIN].get(entry.entry_id)
    if coordinator is None:
        connection = connection_config(entry.data)

        client = await hass.async_add_executor_job(
            lambda: build_client(connection, hass=hass)
        )
        connected = await hass.async_add_executor_job(client.connect)
        if not connected:
            _LOGGER.warning(
                "%s (%s) on initial connect; will keep retrying",
                connection_error_message(connection),
                endpoint_of(connection),
            )

        scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        coordinator = ModbusUsbCoordinator(
            hass=hass,
            client=client,
            slave_id=entry.data[CONF_SLAVE_ID],
            scan_interval=scan_interval,
            entry_id=entry.entry_id,
            serial_config=connection,
        )

        # Adopt any previously learned USB adapter identity (v2.9.0 hot-plug
        # watch) so a re-plugged adapter on a new ttyUSB index can be found.
        coordinator.note_adapter_identity(entry.data.get(CONF_ADAPTER_IDENTITY))
        if connected:
            # Learn the identity of the adapter that just opened, on a
            # worker thread (it scans /sys), so hot-plug re-discovery has
            # it stored before the adapter ever disappears.
            await hass.async_add_executor_job(coordinator.learn_current_identity)

        # An offline RS-485 board must not block Home Assistant setup. The normal
        # coordinator interval starts after entity setup and retries in background.
        coordinator.async_set_updated_data({})
        hass.data[DOMAIN][entry.entry_id] = coordinator

    # Register hub and child devices in HA Device Registry
    from homeassistant.helpers import device_registry as dr

    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer="Modbus USB",
        model=hub_model_name(entry.data),
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


def hub_model_name(data: dict) -> str:
    """Device-registry model string for the hub device."""
    transport = transport_of(data)
    if transport == "serial":
        return "Modbus Serial Hub"
    return f"Modbus Hub via {TRANSPORT_LABELS[transport]}"


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate config entries created before the hub transport existed.

    v1 entries only ever described a local serial adapter; v2 stores the
    transport explicitly so the rest of the integration never needs to guess.
    """
    if entry.version > 2:
        return False  # created by a newer release; do not downgrade
    if entry.version == 1:
        new_data = {**entry.data}
        new_data.setdefault(CONF_TRANSPORT, TRANSPORT_SERIAL)
        hass.config_entries.async_update_entry(entry, data=new_data, version=2)
        _LOGGER.info(
            "Migrated Modbus USB entry %s to version 2 (transport=%s)",
            entry.entry_id,
            new_data[CONF_TRANSPORT],
        )
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the integration when options (e.g. entity list) change."""
    skip_reloads = hass.data.get(DOMAIN, {}).get(DATA_SKIP_DEVICE_RELOAD, set())
    if entry.entry_id in skip_reloads:
        skip_reloads.discard(entry.entry_id)
        coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
        if coordinator is not None:
            # Device enablement changes availability and polling filters only;
            # do not close/reopen the shared serial adapter for that change.
            coordinator.async_set_updated_data(dict(coordinator.data or {}))
        return
    serial_reloads = hass.data.get(DOMAIN, {}).get(DATA_SKIP_SERIAL_RELOAD, set())
    if entry.entry_id in serial_reloads:
        serial_reloads.discard(entry.entry_id)
        coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
        if coordinator is not None:
            # The coordinator already replaced and reopened its serial client.
            # Avoid unloading it a second time while the OS releases the port.
            coordinator.async_set_updated_data(dict(coordinator.data or {}))
        return
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False
    preserve_reloads = hass.data.get(DOMAIN, {}).get(DATA_PRESERVE_SERIAL_RELOAD, set())
    if entry.entry_id in preserve_reloads:
        # Board create/delete reloads platform entities only. Reusing this
        # coordinator avoids a close/open race on USB serial adapters.
        preserve_reloads.discard(entry.entry_id)
        return True
    coordinator: ModbusUsbCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
    await hass.async_add_executor_job(coordinator.close)

    # Unregister services only when no Modbus USB entry remains. The DOMAIN
    # dict also holds reload-guard helper sets, so its truthiness alone can
    # never be used to detect "last entry removed".
    remaining = [
        item
        for item in hass.data.get(DOMAIN, {}).values()
        if isinstance(item, ModbusUsbCoordinator)
    ]
    if not remaining:
        await async_unregister_services(hass)
        domain_data = hass.data.get(DOMAIN, {})
        for helper_key in (
            DATA_SKIP_DEVICE_RELOAD,
            DATA_PRESERVE_SERIAL_RELOAD,
            DATA_SKIP_SERIAL_RELOAD,
        ):
            domain_data.pop(helper_key, None)
        if not domain_data:
            hass.data.pop(DOMAIN, None)

    return unload_ok


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: ConfigEntry, device_entry
) -> bool:
    """Remove one child Modbus device from Home Assistant's device page.

    The hub itself belongs to the config entry and is removed only by removing
    the integration. Child devices use a hub-prefixed identifier, so they can
    be safely removed from the HA device UI one at a time.
    """
    prefix = f"{config_entry.entry_id}_"
    child_identifier = next(
        (
            identifier
            for identifier in device_entry.identifiers
            if identifier[0] == DOMAIN and identifier[1].startswith(prefix)
        ),
        None,
    )
    if child_identifier is None:
        return False

    child_id = child_identifier[1][len(prefix) :]
    new_options = dict(config_entry.options or {})
    new_options[CONF_DEVICES] = [
        device
        for device in new_options.get(CONF_DEVICES, [])
        if str(device.get("id")) != child_id
    ]
    new_options[CONF_ENTITIES] = [
        entity
        for entity in new_options.get(CONF_ENTITIES, [])
        if str(entity.get(CONF_DEVICE_ID)) != child_id
    ]

    from homeassistant.helpers import entity_registry as er

    entity_registry = er.async_get(hass)
    for registry_entity in er.async_entries_for_config_entry(
        entity_registry, config_entry.entry_id
    ):
        if registry_entity.device_id == device_entry.id:
            entity_registry.async_remove(registry_entity.entity_id)

    hass.config_entries.async_update_entry(config_entry, options=new_options)
    return True
