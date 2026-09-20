"""Template management for Modbus USB devices.

Loads, saves, and parses per-device YAML template files.
Templates are stored in `<config_dir>/modbus_usb_templates/*.yaml`.
"""

from __future__ import annotations

import logging
import os
import tempfile
from typing import Any

import yaml
from homeassistant.core import HomeAssistant

from .const import TEMPLATES_DIR_NAME
from .validation import validate_template

_LOGGER = logging.getLogger(__name__)

BUNDLED_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")


def get_user_templates_dir(hass: HomeAssistant) -> str:
    """Return the absolute path to the user's modbus_usb_templates directory."""
    return hass.config.path(TEMPLATES_DIR_NAME)


def ensure_templates_dir(hass: HomeAssistant) -> str:
    """Ensure the user templates directory exists without seeding files."""
    user_dir = get_user_templates_dir(hass)
    if not os.path.exists(user_dir):
        try:
            os.makedirs(user_dir, exist_ok=True)
            _LOGGER.info("Created Modbus USB templates directory at %s", user_dir)
        except Exception as err:
            _LOGGER.warning(
                "Could not create templates directory %s: %s", user_dir, err
            )
            return user_dir

    return user_dir


def _parse_template_file(
    filepath: str, filename: str, source: str = "user"
) -> dict[str, Any] | None:
    """Parse a single YAML template file."""
    try:
        with open(filepath, encoding="utf-8") as f:
            raw_content = f.read()
        data = validate_template(yaml.safe_load(raw_content))

        template_id = data.get("id") or os.path.splitext(filename)[0]
        return {
            "id": template_id,
            "filename": filename,
            "source": source,
            "name": data.get("name", template_id),
            "manufacturer": data.get("manufacturer", "Generic"),
            "model": data.get("model", "Modbus Device"),
            "tested": bool(data.get("tested", False)),
            "status": data.get(
                "status", "tested" if data.get("tested", False) else "untested"
            ),
            "default_slave_id": int(data.get("default_slave_id", 1)),
            "m0_short": bool(data.get("m0_short", False)),
            "description": data.get("description", ""),
            "image": data.get("image") or data.get("picture") or "",
            "info_url": data.get("info_url") or data.get("product_url") or "",
            "device_controls": data.get("device_controls", {}),
            "fingerprint": data.get("fingerprint", []),
            "entities": data.get("entities", []),
            "raw_yaml": raw_content,
        }
    except Exception as err:
        _LOGGER.warning("Failed to parse Modbus template file %s: %s", filepath, err)
        return {
            "id": os.path.splitext(filename)[0],
            "filename": filename,
            "name": filename,
            "error": str(err),
            "entities": [],
            "raw_yaml": "",
        }


def load_templates_sync(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Synchronously scan and load all template YAML files."""
    user_dir = ensure_templates_dir(hass)
    templates: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    # Read from user dir
    if os.path.isdir(user_dir):
        for fname in sorted(os.listdir(user_dir)):
            if fname.endswith((".yaml", ".yml")):
                fpath = os.path.join(user_dir, fname)
                if os.path.islink(fpath) or not os.path.isfile(fpath):
                    continue
                tpl = _parse_template_file(fpath, fname, "user")
                if tpl:
                    templates.append(tpl)
                    seen_ids.add(tpl["id"])

    # Add bundled templates that do not have a user version. When an older
    # user copy exists, preserve its YAML but fill only missing presentation
    # metadata (for example a newly added product link or board photo).
    if os.path.isdir(BUNDLED_TEMPLATES_DIR):
        for fname in sorted(os.listdir(BUNDLED_TEMPLATES_DIR)):
            if fname.endswith((".yaml", ".yml")):
                fpath = os.path.join(BUNDLED_TEMPLATES_DIR, fname)
                bundled_tpl = _parse_template_file(fpath, fname, "bundled")
                if not bundled_tpl:
                    continue
                tid = bundled_tpl["id"]
                if tid not in seen_ids:
                    templates.append(bundled_tpl)
                    seen_ids.add(bundled_tpl["id"])
                    continue

                user_tpl = next((tpl for tpl in templates if tpl["id"] == tid), None)
                if user_tpl:
                    for field in ("image", "info_url"):
                        if not user_tpl.get(field) and bundled_tpl.get(field):
                            user_tpl[field] = bundled_tpl[field]
                    if bundled_tpl.get("tested"):
                        user_tpl["tested"] = True

    return templates


async def async_load_templates(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Asynchronously load all templates."""
    return await hass.async_add_executor_job(load_templates_sync, hass)


def _template_filename(filename: str, *, add_extension: bool = False) -> str:
    """Accept a plain YAML filename, never a path or another file type."""
    if (
        not isinstance(filename, str)
        or not filename.strip()
        or filename.startswith(".")
        or any(char in filename for char in ("/", "\\", "\0"))
    ):
        raise ValueError("Invalid template filename")
    if add_extension and not filename.endswith((".yaml", ".yml")):
        filename = f"{filename}.yaml"
    if not filename.endswith((".yaml", ".yml")):
        raise ValueError("Template filename must end in .yaml or .yml")
    return filename


def save_template_sync(
    hass: HomeAssistant, filename: str, content: str
) -> dict[str, Any]:
    """Validate first, then atomically replace a user template on disk."""
    filename = _template_filename(filename, add_extension=True)
    if not isinstance(content, str):
        raise ValueError("Template content must be a string")
    validate_template(yaml.safe_load(content))

    user_dir = ensure_templates_dir(hass)
    filepath = os.path.join(user_dir, filename)

    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=user_dir, suffix=".tmp", delete=False
        ) as file:
            temporary_path = file.name
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_path, filepath)
    finally:
        if temporary_path and os.path.exists(temporary_path):
            os.remove(temporary_path)

    tpl = _parse_template_file(filepath, filename)
    if not tpl or tpl.get("error"):
        raise ValueError("Failed to load saved template")
    return tpl


async def async_save_template(
    hass: HomeAssistant, filename: str, content: str
) -> dict[str, Any]:
    """Asynchronously validate and save a template file."""
    return await hass.async_add_executor_job(
        save_template_sync, hass, filename, content
    )


def delete_template_sync(hass: HomeAssistant, filename: str) -> bool:
    """Synchronously delete a template YAML file."""
    filename = _template_filename(filename)
    filepath = os.path.join(get_user_templates_dir(hass), filename)
    try:
        os.remove(filepath)
    except FileNotFoundError:
        return False
    return True


async def async_delete_template(hass: HomeAssistant, filename: str) -> bool:
    """Asynchronously delete a template file."""
    return await hass.async_add_executor_job(delete_template_sync, hass, filename)


# Changelog:
# 2026-09-06 — Parse optional template image/picture URL for sidebar device photos.
# Date modified: 2026-09-06
