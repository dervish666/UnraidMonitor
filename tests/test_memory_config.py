"""Tests for memory management configuration."""

from src.config import MemoryConfig


class TestMemoryConfig:
    def test_from_dict_with_all_fields(self):
        data = {
            "enabled": True,
            "warning_threshold": 90,
            "critical_threshold": 95,
            "safe_threshold": 80,
            "kill_delay_seconds": 60,
            "stabilization_wait": 180,
            "priority_containers": ["plex", "mariadb"],
            "killable_containers": ["bitmagnet", "obsidian"],
        }
        config = MemoryConfig.from_dict(data)

        assert config.enabled is True
        assert config.warning_threshold == 90
        assert config.critical_threshold == 95
        assert config.safe_threshold == 80
        assert config.kill_delay_seconds == 60
        assert config.stabilization_wait == 180
        assert config.priority_containers == ["plex", "mariadb"]
        assert config.killable_containers == ["bitmagnet", "obsidian"]

    def test_from_dict_with_defaults(self):
        config = MemoryConfig.from_dict({})

        assert config.enabled is False
        assert config.warning_threshold == 90
        assert config.critical_threshold == 95
        assert config.safe_threshold == 80
        assert config.kill_delay_seconds == 60
        assert config.stabilization_wait == 180
        assert config.priority_containers == []
        assert config.killable_containers == []

    def test_from_dict_disabled(self):
        config = MemoryConfig.from_dict({"enabled": False})
        assert config.enabled is False


class TestAppConfigMemory:
    def test_app_config_has_memory_management(self):
        from unittest.mock import MagicMock
        from src.config import AppConfig, MemoryConfig as MC

        settings = MagicMock()
        settings.config_path = "config/config.yaml"

        config = AppConfig(settings)
        assert hasattr(config, "memory_management")
        # Use class name comparison to avoid pytest module isolation issues
        assert type(config.memory_management).__name__ == "MemoryConfig"
        assert isinstance(config.memory_management, MC)


class TestRestartContainers:
    def test_from_dict_parses_restart_containers(self):
        config = MemoryConfig.from_dict({"restart_containers": ["plex"]})
        assert config.restart_containers == ["plex"]

    def test_from_dict_defaults_to_empty(self):
        config = MemoryConfig.from_dict({})
        assert config.restart_containers == []

    def test_set_restart_containers_persists_and_preserves_section(self, tmp_path):
        import yaml
        from src.config import load_yaml_config

        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(
            yaml.dump({"memory_management": {"enabled": True, "killable_containers": ["sab"]}}),
            encoding="utf-8",
        )

        config = MemoryConfig.from_dict({"enabled": True})
        config.config_path = str(cfg_path)
        config.set_restart_containers(["plex"])

        assert config.restart_containers == ["plex"]
        written = load_yaml_config(str(cfg_path))
        assert written["memory_management"]["restart_containers"] == ["plex"]
        # Other keys in the section are untouched
        assert written["memory_management"]["enabled"] is True
        assert written["memory_management"]["killable_containers"] == ["sab"]

    def test_set_restart_containers_filters_invalid_names(self, tmp_path):
        import yaml

        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(yaml.dump({"memory_management": {}}), encoding="utf-8")

        config = MemoryConfig.from_dict({})
        config.config_path = str(cfg_path)
        config.set_restart_containers(["plex", "bad name!", ""])

        assert config.restart_containers == ["plex"]

    def test_set_restart_containers_no_path_no_crash(self):
        config = MemoryConfig.from_dict({})
        config.set_restart_containers(["plex"])
        assert config.restart_containers == ["plex"]
