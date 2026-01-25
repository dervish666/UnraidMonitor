import pytest
from unittest.mock import patch, mock_open
import yaml
import sys


def test_config_loads_yaml_settings():
    """Test that YAML config is loaded and merged with env settings."""
    yaml_content = """
ignored_containers:
  - Kometa
  - test-container

log_watching:
  containers:
    - plex
    - radarr
  error_patterns:
    - "error"
    - "fatal"
  ignore_patterns:
    - "DEBUG"
  cooldown_seconds: 900
"""
    # Remove cached module to force reimport
    if "src.config" in sys.modules:
        del sys.modules["src.config"]

    with patch.dict("os.environ", {
        "TELEGRAM_BOT_TOKEN": "test-token",
        "TELEGRAM_ALLOWED_USERS": "123",
    }, clear=True):
        with patch("src.config.open", mock_open(read_data=yaml_content)):
            with patch("os.path.exists", return_value=True):
                from src.config import load_yaml_config

                yaml_config = load_yaml_config("config/config.yaml")

                assert yaml_config["ignored_containers"] == ["Kometa", "test-container"]
                assert yaml_config["log_watching"]["containers"] == ["plex", "radarr"]
                assert yaml_config["log_watching"]["cooldown_seconds"] == 900


def test_config_uses_defaults_when_no_yaml():
    """Test that sensible defaults are used when YAML is missing."""
    # Remove cached module to force reimport
    if "src.config" in sys.modules:
        del sys.modules["src.config"]

    with patch.dict("os.environ", {
        "TELEGRAM_BOT_TOKEN": "test-token",
        "TELEGRAM_ALLOWED_USERS": "123",
    }, clear=True):
        with patch("os.path.exists", return_value=False):
            from src.config import load_yaml_config

            yaml_config = load_yaml_config("config/config.yaml")

            assert yaml_config.get("ignored_containers", []) == []
            assert yaml_config.get("log_watching", {}) == {}


def test_default_log_watching_constants():
    """Test that default constants are properly defined."""
    # Remove cached module to force reimport
    if "src.config" in sys.modules:
        del sys.modules["src.config"]

    from src.config import (
        DEFAULT_WATCHED_CONTAINERS,
        DEFAULT_ERROR_PATTERNS,
        DEFAULT_IGNORE_PATTERNS,
        DEFAULT_LOG_WATCHING,
    )

    # Check containers list
    assert "plex" in DEFAULT_WATCHED_CONTAINERS
    assert "radarr" in DEFAULT_WATCHED_CONTAINERS
    assert "sonarr" in DEFAULT_WATCHED_CONTAINERS
    assert "qbit" in DEFAULT_WATCHED_CONTAINERS

    # Check error patterns
    assert "error" in DEFAULT_ERROR_PATTERNS
    assert "fatal" in DEFAULT_ERROR_PATTERNS
    assert "exception" in DEFAULT_ERROR_PATTERNS

    # Check ignore patterns
    assert "DeprecationWarning" in DEFAULT_IGNORE_PATTERNS
    assert "DEBUG" in DEFAULT_IGNORE_PATTERNS

    # Check combined default
    assert DEFAULT_LOG_WATCHING["containers"] == DEFAULT_WATCHED_CONTAINERS
    assert DEFAULT_LOG_WATCHING["error_patterns"] == DEFAULT_ERROR_PATTERNS
    assert DEFAULT_LOG_WATCHING["ignore_patterns"] == DEFAULT_IGNORE_PATTERNS
    assert DEFAULT_LOG_WATCHING["cooldown_seconds"] == 900


def test_app_config_class():
    """Test the AppConfig class combines Settings and YAML config."""
    yaml_content = """
ignored_containers:
  - Kometa
  - test-container

log_watching:
  containers:
    - plex
  error_patterns:
    - "error"
  ignore_patterns:
    - "DEBUG"
  cooldown_seconds: 600
"""
    # Remove cached module to force reimport
    if "src.config" in sys.modules:
        del sys.modules["src.config"]

    with patch.dict("os.environ", {
        "TELEGRAM_BOT_TOKEN": "test-token",
        "TELEGRAM_ALLOWED_USERS": "123",
    }, clear=True):
        with patch("src.config.open", mock_open(read_data=yaml_content)):
            with patch("os.path.exists", return_value=True):
                from src.config import Settings, AppConfig

                settings = Settings(_env_file=None)
                app_config = AppConfig(settings)

                assert app_config.ignored_containers == ["Kometa", "test-container"]
                assert app_config.log_watching["containers"] == ["plex"]
                assert app_config.log_watching["cooldown_seconds"] == 600
