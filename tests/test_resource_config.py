import pytest


def test_resource_config_defaults():
    """Test ResourceConfig has sensible defaults."""
    from src.config import ResourceConfig

    config = ResourceConfig()

    assert config.enabled is True
    assert config.poll_interval_seconds == 60
    assert config.sustained_threshold_seconds == 120
    assert config.default_cpu_percent == 80
    assert config.default_memory_percent == 85
    assert config.container_overrides == {}


def test_resource_config_from_dict():
    """Test ResourceConfig can be created from YAML dict."""
    from src.config import ResourceConfig

    yaml_dict = {
        "enabled": True,
        "poll_interval_seconds": 30,
        "sustained_threshold_seconds": 60,
        "defaults": {
            "cpu_percent": 70,
            "memory_percent": 80,
        },
        "containers": {
            "plex": {"cpu_percent": 95},
            "radarr": {"memory_percent": 90},
        },
    }

    config = ResourceConfig.from_dict(yaml_dict)

    assert config.enabled is True
    assert config.poll_interval_seconds == 30
    assert config.sustained_threshold_seconds == 60
    assert config.default_cpu_percent == 70
    assert config.default_memory_percent == 80
    assert config.container_overrides == {
        "plex": {"cpu_percent": 95},
        "radarr": {"memory_percent": 90},
    }


def test_resource_config_get_thresholds():
    """Test getting thresholds for specific containers."""
    from src.config import ResourceConfig

    config = ResourceConfig(
        default_cpu_percent=80,
        default_memory_percent=85,
        container_overrides={
            "plex": {"cpu_percent": 95, "memory_percent": 90},
            "radarr": {"cpu_percent": 70},
        },
    )

    # Container with full overrides
    cpu, mem = config.get_thresholds("plex")
    assert cpu == 95
    assert mem == 90

    # Container with partial override
    cpu, mem = config.get_thresholds("radarr")
    assert cpu == 70
    assert mem == 85  # Falls back to default

    # Container without override
    cpu, mem = config.get_thresholds("sonarr")
    assert cpu == 80
    assert mem == 85


def test_resource_config_disabled():
    """Test ResourceConfig when disabled."""
    from src.config import ResourceConfig

    config = ResourceConfig.from_dict({"enabled": False})

    assert config.enabled is False


def test_resource_config_empty_dict():
    """Test ResourceConfig with empty dict uses defaults."""
    from src.config import ResourceConfig

    config = ResourceConfig.from_dict({})

    assert config.enabled is True
    assert config.poll_interval_seconds == 60


def test_app_config_resource_monitoring_property():
    """Test AppConfig exposes resource_monitoring config."""
    from unittest.mock import MagicMock
    from src.config import AppConfig, ResourceConfig

    mock_settings = MagicMock()
    mock_settings.config_path = "/nonexistent/path"

    config = AppConfig(mock_settings)

    # Should return default ResourceConfig when not in YAML
    assert isinstance(config.resource_monitoring, ResourceConfig)
    assert config.resource_monitoring.enabled is True


def test_app_config_resource_monitoring_from_yaml(tmp_path):
    """Test AppConfig loads resource_monitoring from YAML."""
    from unittest.mock import MagicMock
    from src.config import AppConfig, ResourceConfig

    # Create a temp config file
    config_file = tmp_path / "config.yaml"
    config_file.write_text("""
resource_monitoring:
  enabled: true
  poll_interval_seconds: 30
  defaults:
    cpu_percent: 70
  containers:
    plex:
      cpu_percent: 95
""")

    mock_settings = MagicMock()
    mock_settings.config_path = str(config_file)

    config = AppConfig(mock_settings)

    assert isinstance(config.resource_monitoring, ResourceConfig)
    assert config.resource_monitoring.poll_interval_seconds == 30
    assert config.resource_monitoring.default_cpu_percent == 70
    assert config.resource_monitoring.container_overrides == {"plex": {"cpu_percent": 95}}
