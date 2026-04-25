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


def test_set_threshold_cpu():
    """Test setting a per-container CPU threshold."""
    from src.config import ResourceConfig

    config = ResourceConfig()
    config.set_threshold("plex", "cpu", 95)

    cpu, mem = config.get_thresholds("plex")
    assert cpu == 95
    assert mem == 85  # Unchanged


def test_set_threshold_memory():
    """Test setting a per-container memory threshold."""
    from src.config import ResourceConfig

    config = ResourceConfig()
    config.set_threshold("plex", "memory", 90)

    cpu, mem = config.get_thresholds("plex")
    assert cpu == 80  # Unchanged
    assert mem == 90


def test_set_threshold_reset():
    """Test resetting a per-container threshold to default."""
    from src.config import ResourceConfig

    config = ResourceConfig()
    config.set_threshold("plex", "cpu", 95)
    assert config.get_thresholds("plex") == (95, 85)

    config.set_threshold("plex", "cpu", 0)
    assert config.get_thresholds("plex") == (80, 85)
    assert "plex" not in config.container_overrides


def test_set_threshold_reset_partial():
    """Test resetting one metric keeps the other override."""
    from src.config import ResourceConfig

    config = ResourceConfig()
    config.set_threshold("plex", "cpu", 95)
    config.set_threshold("plex", "memory", 90)
    assert config.get_thresholds("plex") == (95, 90)

    config.set_threshold("plex", "cpu", 0)
    assert config.get_thresholds("plex") == (80, 90)
    assert config.container_overrides == {"plex": {"memory_percent": 90}}


def test_set_threshold_clamps_memory():
    """Test that memory threshold is clamped to 1-100."""
    from src.config import ResourceConfig

    config = ResourceConfig()
    config.set_threshold("plex", "memory", 150)
    assert config.get_thresholds("plex") == (80, 100)

    config.set_threshold("plex", "memory", -5)
    assert config.get_thresholds("plex") == (80, 1)


def test_set_threshold_cpu_allows_over_100():
    """Test that CPU threshold allows values over 100% (per-core reporting)."""
    from src.config import ResourceConfig

    config = ResourceConfig()
    config.set_threshold("plex", "cpu", 200)
    assert config.get_thresholds("plex") == (200, 85)

    config.set_threshold("plex", "cpu", 400)
    assert config.get_thresholds("plex") == (400, 85)


def test_set_threshold_persists(tmp_path):
    """Test that set_threshold writes to config.yaml."""
    import yaml
    from src.config import ResourceConfig

    config_file = tmp_path / "config.yaml"
    config_file.write_text("log_watching:\n  containers: []\n")

    config = ResourceConfig(config_path=str(config_file))
    config.set_threshold("plex", "cpu", 95)

    saved = yaml.safe_load(config_file.read_text())
    assert saved["resource_monitoring"]["containers"] == {"plex": {"cpu_percent": 95}}
    # Verify other config sections are preserved
    assert "log_watching" in saved


def test_set_threshold_persist_reset_removes_section(tmp_path):
    """Test that resetting all overrides removes the containers section."""
    import yaml
    from src.config import ResourceConfig

    config_file = tmp_path / "config.yaml"
    config_file.write_text("resource_monitoring:\n  containers:\n    plex:\n      cpu_percent: 95\n")

    config = ResourceConfig(
        config_path=str(config_file),
        container_overrides={"plex": {"cpu_percent": 95}},
    )
    config.set_threshold("plex", "cpu", 0)

    saved = yaml.safe_load(config_file.read_text())
    assert "containers" not in saved.get("resource_monitoring", {})


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
