"""Tests for runtime persistence of image-update and auto-heal config."""

import yaml

from src.config import AutoHealConfig, ImageUpdatesConfig, load_yaml_config


def _write_yaml(path, data):
    path.write_text(yaml.dump(data), encoding="utf-8")


def test_image_updates_set_enabled_persists(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    _write_yaml(cfg_path, {"image_updates": {"enabled": False, "poll_interval_hours": 12}})

    config = ImageUpdatesConfig.from_dict({"enabled": False, "poll_interval_hours": 12})
    config.config_path = str(cfg_path)

    config.set_enabled(True)

    assert config.enabled is True
    written = load_yaml_config(str(cfg_path))
    assert written["image_updates"]["enabled"] is True
    # Poll interval is preserved
    assert written["image_updates"]["poll_interval_hours"] == 12


def test_image_updates_set_enabled_creates_section(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    _write_yaml(cfg_path, {"unraid": {"host": "x"}})

    config = ImageUpdatesConfig()
    config.config_path = str(cfg_path)
    config.set_enabled(True)

    written = load_yaml_config(str(cfg_path))
    assert written["image_updates"]["enabled"] is True
    # Existing sections are untouched
    assert written["unraid"]["host"] == "x"


def test_image_updates_no_persist_without_path(tmp_path):
    """No config_path -> in-memory change only, no crash."""
    config = ImageUpdatesConfig()
    config.set_enabled(True)
    assert config.enabled is True


def test_auto_heal_set_containers_persists_and_enables(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    _write_yaml(cfg_path, {"auto_heal": {"enabled": False, "containers": []}})

    config = AutoHealConfig.from_dict({"enabled": False, "containers": []})
    config.config_path = str(cfg_path)

    config.set_containers(["plex", "sonarr"])

    assert config.enabled is True
    assert config.containers == ["plex", "sonarr"]
    written = load_yaml_config(str(cfg_path))
    assert written["auto_heal"]["enabled"] is True
    assert written["auto_heal"]["containers"] == ["plex", "sonarr"]


def test_auto_heal_empty_containers_disables(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    _write_yaml(cfg_path, {"auto_heal": {"enabled": True, "containers": ["plex"]}})

    config = AutoHealConfig.from_dict({"enabled": True, "containers": ["plex"]})
    config.config_path = str(cfg_path)

    config.set_containers([])

    assert config.enabled is False
    assert config.containers == []
    written = load_yaml_config(str(cfg_path))
    assert written["auto_heal"]["enabled"] is False
    assert written["auto_heal"]["containers"] == []


def test_auto_heal_set_containers_preserves_tuning(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    _write_yaml(cfg_path, {"auto_heal": {"max_restarts": 5, "window_minutes": 45}})

    config = AutoHealConfig.from_dict({"max_restarts": 5, "window_minutes": 45})
    config.config_path = str(cfg_path)

    config.set_containers(["plex"])

    written = load_yaml_config(str(cfg_path))
    assert written["auto_heal"]["max_restarts"] == 5
    assert written["auto_heal"]["window_minutes"] == 45
