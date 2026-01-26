import os
from dataclasses import dataclass, field
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
    "container_ignores": {},
}


@dataclass
class ResourceConfig:
    """Configuration for resource monitoring."""

    enabled: bool = True
    poll_interval_seconds: int = 60
    sustained_threshold_seconds: int = 120
    default_cpu_percent: int = 80
    default_memory_percent: int = 85
    container_overrides: dict[str, dict[str, int]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> "ResourceConfig":
        """Create ResourceConfig from YAML dict."""
        defaults = data.get("defaults", {})
        return cls(
            enabled=data.get("enabled", True),
            poll_interval_seconds=data.get("poll_interval_seconds", 60),
            sustained_threshold_seconds=data.get("sustained_threshold_seconds", 120),
            default_cpu_percent=defaults.get("cpu_percent", 80),
            default_memory_percent=defaults.get("memory_percent", 85),
            container_overrides=data.get("containers", {}),
        )

    def get_thresholds(self, container_name: str) -> tuple[int, int]:
        """Get CPU and memory thresholds for a container.

        Returns:
            Tuple of (cpu_percent, memory_percent) thresholds.
        """
        overrides = self.container_overrides.get(container_name, {})
        cpu = overrides.get("cpu_percent", self.default_cpu_percent)
        memory = overrides.get("memory_percent", self.default_memory_percent)
        return cpu, memory


@dataclass
class UnraidConfig:
    """Configuration for Unraid server monitoring."""

    enabled: bool = False
    host: str = ""
    port: int = 443
    poll_system_seconds: int = 30
    poll_array_seconds: int = 300
    poll_ups_seconds: int = 60
    cpu_temp_threshold: int = 80
    cpu_usage_threshold: int = 95
    memory_usage_threshold: int = 90
    disk_temp_threshold: int = 50
    array_usage_threshold: int = 85
    ups_battery_threshold: int = 30

    @classmethod
    def from_dict(cls, data: dict) -> "UnraidConfig":
        """Create UnraidConfig from YAML dict."""
        polling = data.get("polling", {})
        thresholds = data.get("thresholds", {})
        return cls(
            enabled=data.get("enabled", False),
            host=data.get("host", ""),
            port=data.get("port", 443),
            poll_system_seconds=polling.get("system", 30),
            poll_array_seconds=polling.get("array", 300),
            poll_ups_seconds=polling.get("ups", 60),
            cpu_temp_threshold=thresholds.get("cpu_temp", 80),
            cpu_usage_threshold=thresholds.get("cpu_usage", 95),
            memory_usage_threshold=thresholds.get("memory_usage", 90),
            disk_temp_threshold=thresholds.get("disk_temp", 50),
            array_usage_threshold=thresholds.get("array_usage", 85),
            ups_battery_threshold=thresholds.get("ups_battery", 30),
        )


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
    unraid_api_key: str | None = None
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
        config = self._yaml_config.get("log_watching", DEFAULT_LOG_WATCHING)
        # Ensure container_ignores exists
        if "container_ignores" not in config:
            config["container_ignores"] = {}
        return config

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

    @property
    def resource_monitoring(self) -> ResourceConfig:
        """Get resource monitoring configuration."""
        raw = self._yaml_config.get("resource_monitoring", {})
        return ResourceConfig.from_dict(raw)

    @property
    def unraid(self) -> UnraidConfig:
        """Get Unraid configuration."""
        return UnraidConfig.from_dict(self._yaml_config.get("unraid", {}))
