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
        from src.config import AppConfig

        settings = MagicMock()
        settings.config_path = "config/config.yaml"

        config = AppConfig(settings)
        assert hasattr(config, "memory_management")
        assert isinstance(config.memory_management, MemoryConfig)
