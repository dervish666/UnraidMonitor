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
