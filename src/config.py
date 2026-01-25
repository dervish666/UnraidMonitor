import os
from typing import Any

import yaml
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# Default containers to watch for log errors
DEFAULT_WATCHED_CONTAINERS: list[str] = [
    "plex",
    "radarr",
    "sonarr",
    "lidarr",
    "readarr",
    "prowlarr",
    "qbit",
    "sab",
    "tautulli",
    "overseerr",
    "mariadb",
    "postgresql14",
    "redis",
    "Brisbooks",
]

# Default patterns to match as errors
DEFAULT_ERROR_PATTERNS: list[str] = [
    "error",
    "exception",
    "fatal",
    "failed",
    "critical",
    "panic",
    "traceback",
]

# Default patterns to ignore (even if they match error patterns)
DEFAULT_IGNORE_PATTERNS: list[str] = [
    "DeprecationWarning",
    "DEBUG",
]

# Combined default log watching configuration
DEFAULT_LOG_WATCHING: dict[str, Any] = {
    "containers": DEFAULT_WATCHED_CONTAINERS,
    "error_patterns": DEFAULT_ERROR_PATTERNS,
    "ignore_patterns": DEFAULT_IGNORE_PATTERNS,
    "cooldown_seconds": 900,
}


def load_yaml_config(path: str) -> dict[str, Any]:
    """Load YAML configuration file.

    Args:
        path: Path to the YAML configuration file.

    Returns:
        Dictionary with configuration values, or empty dict if file doesn't exist.
    """
    if not os.path.exists(path):
        return {}

    with open(path, encoding="utf-8") as f:
        content = f.read()
        if not content.strip():
            return {}
        return yaml.safe_load(content) or {}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    telegram_bot_token: str
    telegram_allowed_users: list[int] | str  # Accept string, convert to list
    anthropic_api_key: str | None = None
    config_path: str = "config/config.yaml"
    log_level: str = "INFO"

    @field_validator("telegram_allowed_users", mode="before")
    @classmethod
    def parse_allowed_users(cls, v: Any) -> list[int]:
        """Parse comma-separated string of user IDs into list of integers."""
        if isinstance(v, int):
            return [v]
        if isinstance(v, list):
            return [int(x) for x in v]
        if isinstance(v, str):
            v = v.strip()
            if not v:
                raise ValueError("TELEGRAM_ALLOWED_USERS cannot be empty")
            try:
                return [int(x.strip()) for x in v.split(",") if x.strip()]
            except ValueError:
                raise ValueError(
                    f"TELEGRAM_ALLOWED_USERS must be comma-separated integers, got: {v}"
                )
        raise ValueError(f"TELEGRAM_ALLOWED_USERS must be a string or list, got: {type(v)}")


class AppConfig:
    """Application configuration combining Settings (env) and YAML config."""

    def __init__(self, settings: Settings):
        """Initialize AppConfig with Settings and load YAML config.

        Args:
            settings: Pydantic Settings instance with environment variables.
        """
        self._settings = settings
        self._yaml_config = load_yaml_config(settings.config_path)

    @property
    def settings(self) -> Settings:
        """Get the underlying Settings object."""
        return self._settings

    @property
    def ignored_containers(self) -> list[str]:
        """Get list of container names to ignore."""
        return self._yaml_config.get("ignored_containers", [])

    @property
    def protected_containers(self) -> list[str]:
        """Get list of containers that cannot be controlled via Telegram."""
        return self._yaml_config.get("protected_containers", [])

    @property
    def log_watching(self) -> dict[str, Any]:
        """Get log watching configuration.

        Returns YAML config if present, otherwise returns defaults.
        """
        return self._yaml_config.get("log_watching", DEFAULT_LOG_WATCHING)

    @property
    def telegram_bot_token(self) -> str:
        """Get Telegram bot token."""
        return self._settings.telegram_bot_token

    @property
    def telegram_allowed_users(self) -> list[int]:
        """Get list of allowed Telegram user IDs."""
        return self._settings.telegram_allowed_users  # type: ignore

    @property
    def anthropic_api_key(self) -> str | None:
        """Get Anthropic API key."""
        return self._settings.anthropic_api_key

    @property
    def log_level(self) -> str:
        """Get log level."""
        return self._settings.log_level
