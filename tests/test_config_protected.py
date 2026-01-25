import pytest
from unittest.mock import patch, mock_open
import sys


def test_config_loads_protected_containers():
    """Test that protected_containers is loaded from YAML."""
    yaml_content = """
protected_containers:
  - unraid-monitor-bot
  - mariadb
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
                config = AppConfig(settings)

                assert config.protected_containers == ["unraid-monitor-bot", "mariadb"]


def test_config_protected_containers_defaults_to_empty():
    """Test that protected_containers defaults to empty list."""
    # Remove cached module to force reimport
    if "src.config" in sys.modules:
        del sys.modules["src.config"]

    with patch.dict("os.environ", {
        "TELEGRAM_BOT_TOKEN": "test-token",
        "TELEGRAM_ALLOWED_USERS": "123",
    }, clear=True):
        with patch("os.path.exists", return_value=False):
            from src.config import Settings, AppConfig

            settings = Settings(_env_file=None)
            config = AppConfig(settings)

            assert config.protected_containers == []


def test_config_protected_containers_with_empty_yaml():
    """Test that protected_containers defaults to empty list when YAML exists but key is missing."""
    yaml_content = """
ignored_containers:
  - Kometa
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
                config = AppConfig(settings)

                assert config.protected_containers == []


def test_config_protected_containers_single_item():
    """Test that protected_containers works with single container."""
    yaml_content = """
protected_containers:
  - unraid-monitor-bot
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
                config = AppConfig(settings)

                assert config.protected_containers == ["unraid-monitor-bot"]
