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
    assert config.port == 443
    assert config.poll_system_seconds == 30
    assert config.cpu_temp_threshold == 80
    assert config.memory_usage_threshold == 90


def test_unraid_config_disabled():
    """Test UnraidConfig when explicitly disabled."""
    from src.config import UnraidConfig

    config = UnraidConfig.from_dict({"enabled": False})

    assert config.enabled is False
