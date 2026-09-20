"""WebSocket commands for release update checks and installation."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..const import (
    integration_version,
)

_LOGGER = logging.getLogger(__name__)


_GITHUB_REPOSITORY = "sandro-defender/ha-modbus-usb"


def _local_version() -> str:
    """Return the version packaged with this installed custom component."""
    return integration_version()


def _version_key(version: str) -> tuple[int, ...]:
    """Convert a simple release version to comparable integer components."""
    clean = version.strip().lstrip("vV").split("-", maxsplit=1)[0]
    parts = [int(part) for part in clean.split(".")]
    # Treat equivalent short semantic versions consistently: 2.1 is 2.1.0.
    return tuple((parts + [0, 0, 0])[:3])


def _find_hacs_update_entity(hass: HomeAssistant) -> str | None:
    """Return this integration's HACS update entity when Home Assistant has one."""
    for state in hass.states.async_all("update"):
        attributes = state.attributes
        searchable = " ".join(
            str(value)
            for value in (
                state.entity_id,
                attributes.get("friendly_name", ""),
                attributes.get("repository", ""),
                attributes.get("release_url", ""),
                attributes.get("url", ""),
            )
        ).lower()
        # An update entity reports "on" only after HACS has discovered a
        # package update. GitHub can publish a release a few minutes earlier,
        # so do not ask HACS to install while it still reports no update.
        if state.state == "on" and (
            "ha-modbus-usb" in searchable or "modbus usb controller" in searchable
        ):
            return state.entity_id
    return None


async def _async_update_status(hass: HomeAssistant) -> dict[str, Any]:
    """Read the latest GitHub release and compare it to the installed version."""
    current_version = _local_version()
    session = async_get_clientsession(hass)
    release_url = f"https://github.com/{_GITHUB_REPOSITORY}/releases/latest"
    async with session.get(
        f"https://api.github.com/repos/{_GITHUB_REPOSITORY}/releases/latest",
        headers={"Accept": "application/vnd.github+json"},
        timeout=10,
    ) as response:
        if response.status != 200:
            raise ValueError(f"GitHub release check failed (HTTP {response.status})")
        release = await response.json()
    latest_version = str(release.get("tag_name", "")).lstrip("vV")
    if not latest_version:
        raise ValueError("GitHub's latest release has no version tag")
    try:
        update_available = _version_key(latest_version) > _version_key(current_version)
    except ValueError as err:
        raise ValueError(
            "GitHub release version is not a supported numeric version"
        ) from err
    return {
        "current_version": current_version,
        "latest_version": latest_version,
        "update_available": update_available,
        "release_url": release.get("html_url") or release_url,
        "update_entity_id": _find_hacs_update_entity(hass),
    }


@websocket_api.websocket_command(
    {
        vol.Required("type"): "modbus_usb/check_update",
    }
)
@websocket_api.async_response
async def ws_check_update(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Check GitHub for a newer release without changing the installation."""
    try:
        connection.send_result(msg["id"], await _async_update_status(hass))
    except Exception as err:
        _LOGGER.debug("GitHub update check failed: %s", err)
        connection.send_error(msg["id"], "update_check_failed", str(err))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "modbus_usb/install_update",
    }
)
@websocket_api.async_response
async def ws_install_update(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Start the HACS update when available, otherwise return the release page."""
    try:
        if not connection.user.is_admin:
            connection.send_error(
                msg["id"], "unauthorized", "Administrator access is required"
            )
            return
        status = await _async_update_status(hass)
        if not status["update_available"]:
            connection.send_result(msg["id"], {"started": False, **status})
            return
        update_entity_id = status.get("update_entity_id")
        if update_entity_id:
            try:
                await hass.services.async_call(
                    "update", "install", {"entity_id": update_entity_id}, blocking=True
                )
                connection.send_result(
                    msg["id"], {"started": True, "method": "hacs", **status}
                )
                return
            except Exception as err:
                # HACS can finish its refresh between the check and install.
                # Offer the verified release page rather than showing a failed
                # update action to the user.
                _LOGGER.debug("HACS update could not start: %s", err)
        connection.send_result(
            msg["id"], {"started": False, "method": "release_page", **status}
        )
    except Exception as err:
        _LOGGER.debug("Integration update failed: %s", err)
        connection.send_error(msg["id"], "update_failed", str(err))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "modbus_usb/restart_home_assistant",
    }
)
@websocket_api.async_response
async def ws_restart_home_assistant(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Schedule a Home Assistant restart after an in-panel update."""
    if not connection.user.is_admin:
        connection.send_error(
            msg["id"], "unauthorized", "Administrator access is required"
        )
        return

    async def _restart() -> None:
        try:
            await hass.services.async_call("homeassistant", "restart", blocking=True)
        except Exception as err:
            _LOGGER.error(
                "Home Assistant restart requested by Modbus USB panel failed: %s", err
            )

    # Reply before scheduling the restart so the panel can give the user clear
    # feedback instead of losing its WebSocket connection without an answer.
    connection.send_result(msg["id"], {"restarting": True})
    hass.async_create_task(_restart())
