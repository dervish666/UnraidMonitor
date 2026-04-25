import pytest
from unittest.mock import patch
from pydantic import ValidationError


def test_config_loads_telegram_token_from_env():
    with patch.dict("os.environ", {
        "TELEGRAM_BOT_TOKEN": "test-token-123",
        "TELEGRAM_ALLOWED_USERS": "111,222",
    }, clear=True):
        from src.config import Settings
        settings = Settings(_env_file=None)
        assert settings.telegram_bot_token == "test-token-123"


def test_config_parses_allowed_users_as_list():
    with patch.dict("os.environ", {
        "TELEGRAM_BOT_TOKEN": "test-token",
        "TELEGRAM_ALLOWED_USERS": "111,222,333",
    }, clear=True):
        from src.config import Settings
        settings = Settings(_env_file=None)
        assert settings.telegram_allowed_users == [111, 222, 333]


def test_config_raises_without_required_vars():
    with patch.dict("os.environ", {}, clear=True):
        from src.config import Settings
        with pytest.raises(ValidationError):
            Settings(_env_file=None)


def test_config_parses_single_user():
    """Test that a single user ID is parsed correctly."""
    with patch.dict("os.environ", {
        "TELEGRAM_BOT_TOKEN": "test-token",
        "TELEGRAM_ALLOWED_USERS": "123",
    }, clear=True):
        from src.config import Settings
        settings = Settings(_env_file=None)
        assert settings.telegram_allowed_users == [123]


def test_config_handles_whitespace_in_allowed_users():
    """Test that whitespace around user IDs is handled correctly."""
    with patch.dict("os.environ", {
        "TELEGRAM_BOT_TOKEN": "test-token",
        "TELEGRAM_ALLOWED_USERS": " 123 , 456 ",
    }, clear=True):
        from src.config import Settings
        settings = Settings(_env_file=None)
        assert settings.telegram_allowed_users == [123, 456]


def test_config_raises_on_empty_allowed_users():
    """Test that empty TELEGRAM_ALLOWED_USERS raises ValueError."""
    with patch.dict("os.environ", {
        "TELEGRAM_BOT_TOKEN": "test-token",
        "TELEGRAM_ALLOWED_USERS": "",
    }, clear=True):
        from src.config import Settings
        with pytest.raises(ValidationError) as exc_info:
            Settings(_env_file=None)
        assert "TELEGRAM_ALLOWED_USERS cannot be empty" in str(exc_info.value)


def test_config_raises_on_invalid_allowed_users():
    """Test that non-integer values raise ValueError with clear message."""
    with patch.dict("os.environ", {
        "TELEGRAM_BOT_TOKEN": "test-token",
        "TELEGRAM_ALLOWED_USERS": "abc,123",
    }, clear=True):
        from src.config import Settings
        with pytest.raises(ValidationError) as exc_info:
            Settings(_env_file=None)
        assert "TELEGRAM_ALLOWED_USERS must be comma-separated integers" in str(exc_info.value)


def test_log_watching_container_ignores(tmp_path):
    """Test that container_ignores is parsed from config."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text("""
log_watching:
  containers:
    - plex
  error_patterns:
    - error
  ignore_patterns:
    - DEBUG
  container_ignores:
    plex:
      - connection timed out
      - slow query
    radarr:
      - rate limit
""")

    import os
    from unittest.mock import patch

    with patch.dict(os.environ, {
        "TELEGRAM_BOT_TOKEN": "test",
        "TELEGRAM_ALLOWED_USERS": "123",
    }):
        from src.config import Settings, AppConfig

        settings = Settings(config_path=str(config_file))
        config = AppConfig(settings)

        log_watching = config.log_watching
        assert "container_ignores" in log_watching
        assert log_watching["container_ignores"]["plex"] == ["connection timed out", "slow query"]
        assert log_watching["container_ignores"]["radarr"] == ["rate limit"]


def test_log_watching_container_ignores_default_empty(tmp_path):
    """Test that container_ignores defaults to empty dict."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text("""
log_watching:
  containers:
    - plex
""")

    import os
    from unittest.mock import patch

    with patch.dict(os.environ, {
        "TELEGRAM_BOT_TOKEN": "test",
        "TELEGRAM_ALLOWED_USERS": "123",
    }):
        from src.config import Settings, AppConfig

        settings = Settings(config_path=str(config_file))
        config = AppConfig(settings)

        log_watching = config.log_watching
        # container_ignores must always be present
        assert "container_ignores" in log_watching
        assert log_watching["container_ignores"] == {}


def test_log_watching_container_ignores_default_when_no_config(tmp_path):
    """Test that container_ignores is present even when no log_watching config exists."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text("")

    import os
    from unittest.mock import patch

    with patch.dict(os.environ, {
        "TELEGRAM_BOT_TOKEN": "test",
        "TELEGRAM_ALLOWED_USERS": "123",
    }):
        from src.config import Settings, AppConfig

        settings = Settings(config_path=str(config_file))
        config = AppConfig(settings)

        log_watching = config.log_watching
        # container_ignores must be in default config
        assert "container_ignores" in log_watching
        assert log_watching["container_ignores"] == {}


def test_config_properties_return_cached_instances():
    """Config properties should return the same object on repeated access."""
    from unittest.mock import patch
    from src.config import Settings, AppConfig

    with patch.dict("os.environ", {
        "TELEGRAM_BOT_TOKEN": "test-token",
        "TELEGRAM_ALLOWED_USERS": "123",
    }, clear=True):
        settings = Settings(_env_file=None)
        config = AppConfig(settings)

        assert config.ai is config.ai
        assert config.bot is config.bot
        assert config.docker is config.docker
        assert config.resource_monitoring is config.resource_monitoring
        assert config.unraid is config.unraid
        assert config.memory_management is config.memory_management


def test_unraid_array_thresholds(tmp_path):
    """Test array threshold config loading."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text("""
unraid:
  enabled: true
  host: "192.168.0.190"
  polling:
    array: 300
  thresholds:
    disk_temp: 50
    array_usage: 85
""")

    import os
    from unittest.mock import patch

    with patch.dict(os.environ, {
        "TELEGRAM_BOT_TOKEN": "test",
        "TELEGRAM_ALLOWED_USERS": "123",
    }):
        from src.config import Settings, AppConfig

        settings = Settings(config_path=str(config_file))
        config = AppConfig(settings)

        assert config.unraid.disk_temp_threshold == 50
        assert config.unraid.array_usage_threshold == 85
        assert config.unraid.poll_array_seconds == 300


def test_generate_default_config_creates_file(tmp_path):
    """Test that generate_default_config creates a new file when none exists."""
    from src.config import generate_default_config

    config_file = tmp_path / "config" / "config.yaml"
    assert not config_file.exists()

    result = generate_default_config(str(config_file))

    assert result is True
    assert config_file.exists()


def test_generate_default_config_does_not_overwrite(tmp_path):
    """Test that generate_default_config does not overwrite existing files."""
    from src.config import generate_default_config

    config_file = tmp_path / "config.yaml"
    config_file.write_text("existing: content")

    result = generate_default_config(str(config_file))

    assert result is False
    assert config_file.read_text() == "existing: content"


def test_generated_config_is_valid_yaml(tmp_path):
    """Test that the generated config is valid YAML that can be loaded."""
    import yaml
    from src.config import generate_default_config

    config_file = tmp_path / "config.yaml"
    generate_default_config(str(config_file))

    content = config_file.read_text()
    parsed = yaml.safe_load(content)

    assert isinstance(parsed, dict)
    assert "ai" in parsed
    assert "bot" in parsed
    assert "docker" in parsed
    assert "log_watching" in parsed
    assert "resource_monitoring" in parsed
    assert "memory_management" in parsed
    assert "unraid" in parsed


class TestConfigValidation:
    """Tests for config value clamping/validation."""

    def test_resource_config_zero_poll_interval_clamped(self):
        from src.config import ResourceConfig
        cfg = ResourceConfig.from_dict({"poll_interval_seconds": 0})
        assert cfg.poll_interval_seconds >= 10

    def test_resource_config_negative_poll_interval_clamped(self):
        from src.config import ResourceConfig
        cfg = ResourceConfig.from_dict({"poll_interval_seconds": -5})
        assert cfg.poll_interval_seconds >= 10

    def test_resource_config_zero_sustained_threshold_clamped(self):
        from src.config import ResourceConfig
        cfg = ResourceConfig.from_dict({"sustained_threshold_seconds": 0})
        assert cfg.sustained_threshold_seconds >= 10

    def test_resource_config_negative_cpu_threshold_clamped(self):
        from src.config import ResourceConfig
        cfg = ResourceConfig.from_dict({"defaults": {"cpu_percent": -5}})
        assert cfg.default_cpu_percent >= 1

    def test_resource_config_high_cpu_threshold_allowed(self):
        from src.config import ResourceConfig
        cfg = ResourceConfig.from_dict({"defaults": {"cpu_percent": 200}})
        assert cfg.default_cpu_percent == 200

    def test_resource_config_negative_memory_threshold_clamped(self):
        from src.config import ResourceConfig
        cfg = ResourceConfig.from_dict({"defaults": {"memory_percent": -1}})
        assert cfg.default_memory_percent >= 1

    def test_memory_config_zero_kill_delay_clamped(self):
        from src.config import MemoryConfig
        cfg = MemoryConfig.from_dict({"kill_delay_seconds": 0})
        assert cfg.kill_delay_seconds >= 10

    def test_memory_config_warning_below_minimum_clamped(self):
        from src.config import MemoryConfig
        cfg = MemoryConfig.from_dict({"warning_threshold": 10})
        assert cfg.warning_threshold >= 50

    def test_memory_config_warning_above_maximum_clamped(self):
        from src.config import MemoryConfig
        cfg = MemoryConfig.from_dict({"warning_threshold": 100})
        assert cfg.warning_threshold <= 99

    def test_memory_config_critical_below_minimum_clamped(self):
        from src.config import MemoryConfig
        cfg = MemoryConfig.from_dict({"critical_threshold": 10})
        assert cfg.critical_threshold >= 60

    def test_memory_config_valid_values_unchanged(self):
        from src.config import ResourceConfig, MemoryConfig
        rc = ResourceConfig.from_dict({"poll_interval_seconds": 60, "defaults": {"cpu_percent": 80}})
        assert rc.poll_interval_seconds == 60
        assert rc.default_cpu_percent == 80

        mc = MemoryConfig.from_dict({"kill_delay_seconds": 60, "warning_threshold": 90, "critical_threshold": 95})
        assert mc.kill_delay_seconds == 60
        assert mc.warning_threshold == 90
        assert mc.critical_threshold == 95


def test_generated_config_loads_all_sections(tmp_path):
    """Test that all config sections load properly from generated config."""
    import os
    from unittest.mock import patch
    from src.config import generate_default_config, Settings, AppConfig

    config_file = tmp_path / "config.yaml"
    generate_default_config(str(config_file))

    with patch.dict(os.environ, {
        "TELEGRAM_BOT_TOKEN": "test",
        "TELEGRAM_ALLOWED_USERS": "123",
    }):
        settings = Settings(config_path=str(config_file))
        config = AppConfig(settings)

        # Verify all sections load without errors
        ai = config.ai
        assert ai.pattern_analyzer_model == "claude-haiku-4-5-20251001"
        assert ai.nl_processor_model == "claude-sonnet-4-5-20250929"

        bot = config.bot
        assert bot.log_max_lines == 100
        assert bot.confirmation_timeout_seconds == 60

        docker = config.docker
        assert docker.socket_path == "unix:///var/run/docker.sock"

        resource = config.resource_monitoring
        assert resource.enabled is True
        assert resource.default_cpu_percent == 80
        assert resource.default_memory_percent == 85

        memory = config.memory_management
        assert memory.enabled is False
        assert memory.critical_threshold == 95

        unraid = config.unraid
        assert unraid.enabled is False
        assert unraid.cpu_temp_threshold == 80
