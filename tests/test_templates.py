"""Template validation, isolation, and crash-safe persistence."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml

from custom_components.modbus_usb import templates
from custom_components.modbus_usb.validation import validate_entity, validate_template

VALID_YAML = "id: test\nname: Test\nentities: []\n"
VALID_ENTITY = {"name": "Value", "entity_type": "sensor", "register_type": "holding", "address": 0}


@pytest.fixture
def template_hass(tmp_path):
    return SimpleNamespace(config=SimpleNamespace(path=lambda name: str(tmp_path / name)))


@pytest.mark.parametrize("updates", [
    {"address": -1}, {"address": 65536}, {"address": 1.5}, {"address": True},
    {"address": None}, {"address": 65535, "data_type": "float32"},
    {"slave_id": 0}, {"slave_id": 248}, {"slave_id": 1.5},
    {"data_type": "bad"}, {"name": " "}, {"register_type": "coil"},
    {"entity_type": "bad"}, {"entity_type": []}, {"register_type": {}},
    {"scale": "nan"}, {"scale": "inf"}, {"id": []},
    {"entity_type": "number", "scale": 0},
    {"entity_type": "number", "step": -1},
    {"entity_type": "number", "min_value": 5, "max_value": 1},
    {"entity_type": "number", "mode": "invalid"},
    {"entity_type": "switch", "addresses": []},
    {"entity_type": "switch", "addresses": [65536]},
    {"entity_type": "switch", "on_value": -1},
])
def test_invalid_entities_are_rejected(updates):
    with pytest.raises(ValueError):
        validate_entity({**VALID_ENTITY, **updates})


def test_entity_normalization_keeps_extension_fields():
    entity = {**VALID_ENTITY, "entity_type": "switch", "address": "3", "addresses": ["3", 4, 3],
              "slave_id": "2", "on_value": "256", "scale": None, "assumed_state": True}
    result = validate_entity(entity)
    assert result["addresses"] == [3, 4]
    assert result["address"] == 3
    assert result["slave_id"] == 2
    assert result["on_value"] == 256
    assert result["assumed_state"] is True
    assert "scale" not in result
    assert entity["address"] == "3"


@pytest.mark.parametrize("filename", ["../test.yaml", "/tmp/test.yaml", r"..\test.yaml", "", ".", "..", "a\0.yaml"])
def test_reject_paths(template_hass, filename):
    with pytest.raises(ValueError):
        templates.save_template_sync(template_hass, filename, VALID_YAML)
    with pytest.raises(ValueError):
        templates.delete_template_sync(template_hass, filename)


@pytest.mark.parametrize("content", ["[]", "id: []", "name: Test\nentities: {}", "name: Test\ndefault_slave_id: 0", "name: Test\nentities: [null]"])
def test_invalid_save_preserves_previous_file(template_hass, content):
    templates.save_template_sync(template_hass, "test", VALID_YAML)
    with pytest.raises(ValueError):
        templates.save_template_sync(template_hass, "test", content)
    path = Path(templates.get_user_templates_dir(template_hass)) / "test.yaml"
    assert path.read_text() == VALID_YAML


def test_failed_replace_preserves_previous_file_and_cleans_temp(template_hass):
    templates.save_template_sync(template_hass, "test", VALID_YAML)
    with patch.object(templates.os, "replace", side_effect=OSError("disk error")):
        with pytest.raises(OSError):
            templates.save_template_sync(template_hass, "test", "name: New")
    directory = Path(templates.get_user_templates_dir(template_hass))
    assert list(directory.iterdir()) == [directory / "test.yaml"]
    assert (directory / "test.yaml").read_text() == VALID_YAML


def test_save_replaces_symlink_without_writing_target(template_hass, tmp_path):
    directory = Path(templates.ensure_templates_dir(template_hass))
    outside = tmp_path / "outside.yaml"
    outside.write_text("private: data")
    (directory / "test.yaml").symlink_to(outside)
    assert not any(t["filename"] == "test.yaml" for t in templates.load_templates_sync(template_hass))
    templates.save_template_sync(template_hass, "test", VALID_YAML)
    assert outside.read_text() == "private: data"
    assert not (directory / "test.yaml").is_symlink()


def test_delete_is_idempotent_and_yaml_only(template_hass):
    templates.save_template_sync(template_hass, "test.yml", VALID_YAML)
    assert templates.delete_template_sync(template_hass, "test.yml")
    assert not templates.delete_template_sync(template_hass, "test.yml")
    with pytest.raises(ValueError):
        templates.delete_template_sync(template_hass, "secrets.json")


def test_user_override_uses_declared_id_not_bundled_filename(template_hass, tmp_path, monkeypatch):
    bundled = tmp_path / "bundled"
    bundled.mkdir()
    (bundled / "different-filename.yaml").write_text(VALID_YAML)
    monkeypatch.setattr(templates, "BUNDLED_TEMPLATES_DIR", str(bundled))
    templates.save_template_sync(template_hass, "custom", VALID_YAML)
    result = templates.load_templates_sync(template_hass)
    assert len(result) == 1
    assert result[0]["source"] == "user"


@pytest.mark.parametrize("path", sorted(Path(templates.BUNDLED_TEMPLATES_DIR).glob("*.yaml")), ids=lambda p: p.name)
def test_all_bundled_templates_are_valid(path):
    validate_template(yaml.safe_load(path.read_text()))
