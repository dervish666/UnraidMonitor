import pytest


def test_unraid_config_from_dict():
    """Test UnraidConfig parses from YAML dict."""
    from src.config import UnraidConfig

    data = {
        "enabled": True,
        "host": "192.168.1.100",
        "port": 443,
        "polling": {
            "system": 30,
            "array": 300,
            "ups": 60,
        },
        "thresholds": {
            "cpu_temp": 80,
            "cpu_usage": 95,
            "memory_usage": 90,
        },
    }

    config = UnraidConfig.from_dict(data)

    assert config.enabled is True
    assert config.host == "192.168.1.100"
    assert config.port == 443
    assert config.poll_system_seconds == 30
    assert config.poll_array_seconds == 300
    assert config.cpu_temp_threshold == 80
    assert config.cpu_usage_threshold == 95
    assert config.memory_usage_threshold == 90


def test_unraid_config_defaults():
    """Test UnraidConfig has sensible defaults."""
    from src.config import UnraidConfig

    config = UnraidConfig.from_dict({})

    assert config.enabled is False
    assert config.host == ""
    assert config.port == 80  # HTTP default
    assert config.use_ssl is False
    assert config.poll_system_seconds == 30
    assert config.cpu_temp_threshold == 80
    assert config.memory_usage_threshold == 90


def test_unraid_config_disabled():
    """Test UnraidConfig when explicitly disabled."""
    from src.config import UnraidConfig

    config = UnraidConfig.from_dict({"enabled": False})

    assert config.enabled is False


def test_settings_has_unraid_api_key(tmp_path):
    """Test Settings reads UNRAID_API_KEY from environment."""
    import os
    from unittest.mock import patch

    config_file = tmp_path / "config.yaml"
    config_file.write_text("log_watching:\n  containers: []\n")

    with patch.dict(os.environ, {
        "TELEGRAM_BOT_TOKEN": "test",
        "TELEGRAM_ALLOWED_USERS": "123",
        "UNRAID_API_KEY": "my-secret-key",
    }):
        from src.config import Settings

        settings = Settings(config_path=str(config_file))
        assert settings.unraid_api_key == "my-secret-key"


def test_settings_unraid_api_key_optional(tmp_path):
    """Test UNRAID_API_KEY is optional."""
    import os
    from unittest.mock import patch

    config_file = tmp_path / "config.yaml"
    config_file.write_text("log_watching:\n  containers: []\n")

    with patch.dict(os.environ, {
        "TELEGRAM_BOT_TOKEN": "test",
        "TELEGRAM_ALLOWED_USERS": "123",
    }, clear=True):
        from src.config import Settings

        settings = Settings(config_path=str(config_file))
        assert settings.unraid_api_key is None


def test_app_config_unraid_property(tmp_path):
    """Test AppConfig has unraid property."""
    import os
    from unittest.mock import patch

    config_file = tmp_path / "config.yaml"
    config_file.write_text('''
unraid:
  enabled: true
  host: "192.168.1.100"
  thresholds:
    cpu_temp: 75
''')

    with patch.dict(os.environ, {
        "TELEGRAM_BOT_TOKEN": "test",
        "TELEGRAM_ALLOWED_USERS": "123",
    }):
        from src.config import Settings, AppConfig

        settings = Settings(config_path=str(config_file))
        config = AppConfig(settings)

        assert config.unraid.enabled is True
        assert config.unraid.host == "192.168.1.100"
        assert config.unraid.cpu_temp_threshold == 75
