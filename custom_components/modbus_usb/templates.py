"""Template management for Modbus USB devices.

Loads, saves, and parses per-device YAML template files.
Templates are stored in `<config_dir>/modbus_usb_templates/*.yaml`.
"""
from __future__ import annotations

import logging
import os
import shutil
from typing import Any

import yaml
from homeassistant.core import HomeAssistant

from .const import TEMPLATES_DIR_NAME

_LOGGER = logging.getLogger(__name__)

BUNDLED_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")


def get_user_templates_dir(hass: HomeAssistant) -> str:
    """Return the absolute path to the user's modbus_usb_templates directory."""
    return hass.config.path(TEMPLATES_DIR_NAME)


def ensure_templates_dir(hass: HomeAssistant) -> str:
    """Ensure the user templates directory exists, seeding it with starter templates if empty."""
    user_dir = get_user_templates_dir(hass)
    if not os.path.exists(user_dir):
        try:
            os.makedirs(user_dir, exist_ok=True)
            _LOGGER.info("Created Modbus USB templates directory at %s", user_dir)
        except Exception as err:
            _LOGGER.warning("Could not create templates directory %s: %s", user_dir, err)
            return user_dir

    # If empty, copy bundled templates
    try:
        existing = [f for f in os.listdir(user_dir) if f.endswith((".yaml", ".yml"))]
        if not existing and os.path.isdir(BUNDLED_TEMPLATES_DIR):
            for fname in os.listdir(BUNDLED_TEMPLATES_DIR):
                if fname.endswith((".yaml", ".yml")):
                    src = os.path.join(BUNDLED_TEMPLATES_DIR, fname)
                    dst = os.path.join(user_dir, fname)
                    shutil.copy2(src, dst)
            _LOGGER.info("Seeded initial Modbus USB templates into %s", user_dir)
    except Exception as err:
        _LOGGER.warning("Error seeding templates into %s: %s", user_dir, err)

    return user_dir


def _parse_template_file(filepath: str, filename: str) -> dict[str, Any] | None:
    """Parse a single YAML template file."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            raw_content = f.read()
        data = yaml.safe_load(raw_content) or {}
        if not isinstance(data, dict):
            return None

        template_id = data.get("id") or os.path.splitext(filename)[0]
        return {
            "id": template_id,
            "filename": filename,
            "name": data.get("name", template_id),
            "manufacturer": data.get("manufacturer", "Generic"),
            "model": data.get("model", "Modbus Device"),
            "default_slave_id": int(data.get("default_slave_id", 1)),
            "description": data.get("description", ""),
            "image": data.get("image") or data.get("picture") or "",
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
                tpl = _parse_template_file(fpath, fname)
                if tpl:
                    templates.append(tpl)
                    seen_ids.add(tpl["id"])

    # Fallback to bundled if not in user dir
    if os.path.isdir(BUNDLED_TEMPLATES_DIR):
        for fname in sorted(os.listdir(BUNDLED_TEMPLATES_DIR)):
            if fname.endswith((".yaml", ".yml")):
                tid = os.path.splitext(fname)[0]
                if tid not in seen_ids:
                    fpath = os.path.join(BUNDLED_TEMPLATES_DIR, fname)
                    tpl = _parse_template_file(fpath, fname)
                    if tpl:
                        templates.append(tpl)
                        seen_ids.add(tpl["id"])

    return templates


async def async_load_templates(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Asynchronously load all templates."""
    return await hass.async_add_executor_job(load_templates_sync, hass)


def save_template_sync(hass: HomeAssistant, filename: str, content: str) -> dict[str, Any]:
    """Synchronously validate and save a template file."""
    # Ensure filename ends with .yaml
    if not filename.endswith((".yaml", ".yml")):
        filename = f"{filename}.yaml"

    # Sanitize filename (no directory traversal)
    filename = os.path.basename(filename)
    if not filename or filename in (".", ".."):
        raise ValueError("Invalid filename")

    # Validate YAML parsing
    data = yaml.safe_load(content)
    if not isinstance(data, dict):
        raise ValueError("Template YAML must define a mapping/dictionary at the root level")
    if "name" not in data and "id" not in data:
        raise ValueError("Template must contain at least 'name' or 'id'")

    user_dir = ensure_templates_dir(hass)
    filepath = os.path.join(user_dir, filename)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    tpl = _parse_template_file(filepath, filename)
    if not tpl:
        raise ValueError("Failed to load saved template")
    return tpl


async def async_save_template(hass: HomeAssistant, filename: str, content: str) -> dict[str, Any]:
    """Asynchronously validate and save a template file."""
    return await hass.async_add_executor_job(save_template_sync, hass, filename, content)


def delete_template_sync(hass: HomeAssistant, filename: str) -> bool:
    """Synchronously delete a template YAML file."""
    filename = os.path.basename(filename)
    user_dir = get_user_templates_dir(hass)
    filepath = os.path.join(user_dir, filename)
    if os.path.exists(filepath):
        os.remove(filepath)
        return True
    return False


async def async_delete_template(hass: HomeAssistant, filename: str) -> bool:
    """Asynchronously delete a template file."""
    return await hass.async_add_executor_job(delete_template_sync, hass, filename)

# Changelog:
# 2026-09-06 — Parse optional template image/picture URL for sidebar device photos.
# Date modified: 2026-09-06
