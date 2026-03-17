import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


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
class AIConfig:
    """Configuration for AI/Claude API settings."""

    # Model names
    pattern_analyzer_model: str = "claude-haiku-4-5-20251001"
    nl_processor_model: str = "claude-sonnet-4-5-20250929"
    diagnostic_model: str = "claude-haiku-4-5-20251001"

    # Token limits
    pattern_analyzer_max_tokens: int = 500
    nl_processor_max_tokens: int = 1024
    diagnostic_brief_max_tokens: int = 300
    diagnostic_detail_max_tokens: int = 800

    # NL processor settings
    nl_max_tool_iterations: int = 10
    nl_max_conversation_exchanges: int = 5

    # Pattern analyzer settings
    pattern_analyzer_context_lines: int = 30

    # Diagnostic settings
    diagnostic_context_expiry_seconds: int = 600

    # Multi-provider settings
    default_provider: str = "anthropic"
    default_model: str = "claude-haiku-4-5-20251001"
    anthropic_prompt_caching: bool = True
    ollama_host: str = "http://localhost:11434"
    # Default model used when Ollama is auto-selected as the provider
    ollama_default_model: str = "qwen2.5:7b"

    @classmethod
    def from_dict(cls, data: dict) -> "AIConfig":
        """Create AIConfig from YAML dict."""
        models = data.get("models", {})
        max_tokens = data.get("max_tokens", {})
        nl = data.get("nl_processor", {})
        providers = data.get("providers", {})
        return cls(
            pattern_analyzer_model=models.get("pattern_analyzer", "claude-haiku-4-5-20251001"),
            nl_processor_model=models.get("nl_processor", "claude-sonnet-4-5-20250929"),
            diagnostic_model=models.get("diagnostic", "claude-haiku-4-5-20251001"),
            pattern_analyzer_max_tokens=max_tokens.get("pattern_analyzer", 500),
            nl_processor_max_tokens=max_tokens.get("nl_processor", 1024),
            diagnostic_brief_max_tokens=max_tokens.get("diagnostic_brief", 300),
            diagnostic_detail_max_tokens=max_tokens.get("diagnostic_detail", 800),
            nl_max_tool_iterations=nl.get("max_tool_iterations", 10),
            nl_max_conversation_exchanges=nl.get("max_conversation_exchanges", 5),
            pattern_analyzer_context_lines=data.get("pattern_analyzer_context_lines", 30),
            diagnostic_context_expiry_seconds=data.get("diagnostic_context_expiry_seconds", 600),
            default_provider=data.get("default_provider", "anthropic"),
            default_model=data.get("default_model", "claude-haiku-4-5-20251001"),
            anthropic_prompt_caching=providers.get("anthropic", {}).get("prompt_caching", True),
            ollama_host=providers.get("ollama", {}).get("host", "http://localhost:11434"),
            ollama_default_model=providers.get("ollama", {}).get("default_model", "qwen2.5:7b"),
        )


@dataclass
class BotConfig:
    """Configuration for Telegram bot display and behaviour."""

    confirmation_timeout_seconds: int = 60
    log_max_lines: int = 100
    log_max_chars: int = 4000
    nl_log_max_chars: int = 3000
    diagnose_max_lines: int = 500
    error_display_max_chars: int = 200

    @classmethod
    def from_dict(cls, data: dict) -> "BotConfig":
        """Create BotConfig from YAML dict."""
        log_display = data.get("log_display", {})
        return cls(
            confirmation_timeout_seconds=data.get("confirmation_timeout_seconds", 60),
            log_max_lines=log_display.get("max_lines", 100),
            log_max_chars=log_display.get("max_chars", 4000),
            nl_log_max_chars=log_display.get("nl_max_chars", 3000),
            diagnose_max_lines=log_display.get("diagnose_max_lines", 500),
            error_display_max_chars=data.get("error_display_max_chars", 200),
        )


@dataclass
class DockerConfig:
    """Configuration for Docker connection."""

    socket_path: str = "unix:///var/run/docker.sock"

    @classmethod
    def from_dict(cls, data: dict) -> "DockerConfig":
        """Create DockerConfig from YAML dict."""
        return cls(
            socket_path=data.get("socket_path", "unix:///var/run/docker.sock"),
        )


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
            poll_interval_seconds=max(data.get("poll_interval_seconds", 60), 10),
            sustained_threshold_seconds=max(data.get("sustained_threshold_seconds", 120), 10),
            default_cpu_percent=max(min(defaults.get("cpu_percent", 80), 100), 1),
            default_memory_percent=max(min(defaults.get("memory_percent", 85), 100), 1),
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
class MemoryConfig:
    """Configuration for system memory pressure management."""

    enabled: bool
    warning_threshold: int  # Notify at this % (default 90)
    critical_threshold: int  # Start kill sequence at this % (default 95)
    safe_threshold: int  # Offer restart when below this % (default 80)
    kill_delay_seconds: int  # Warning before killing (default 60)
    stabilization_wait: int  # Wait between kills in seconds (default 180)
    priority_containers: list[str]  # Never kill these
    killable_containers: list[str]  # Kill in this order

    @classmethod
    def from_dict(cls, data: dict) -> "MemoryConfig":
        config = cls(
            enabled=data.get("enabled", False),
            warning_threshold=max(min(data.get("warning_threshold", 90), 99), 50),
            critical_threshold=max(min(data.get("critical_threshold", 95), 100), 60),
            safe_threshold=max(min(data.get("safe_threshold", 80), 95), 30),
            kill_delay_seconds=max(data.get("kill_delay_seconds", 60), 10),
            stabilization_wait=max(data.get("stabilization_wait", 180), 10),
            priority_containers=data.get("priority_containers", []),
            killable_containers=data.get("killable_containers", []),
        )
        # Validate threshold ordering: critical > warning > safe
        if not (config.critical_threshold > config.warning_threshold > config.safe_threshold):
            logger.warning(
                "Memory thresholds out of order (critical=%d, warning=%d, safe=%d), "
                "using defaults (95, 90, 80)",
                config.critical_threshold, config.warning_threshold, config.safe_threshold,
            )
            config.critical_threshold = 95
            config.warning_threshold = 90
            config.safe_threshold = 80
        return config


@dataclass
class UnraidConfig:
    """Configuration for Unraid server monitoring."""

    enabled: bool = False
    host: str = ""
    port: int = 80
    use_ssl: bool = False
    verify_ssl: bool = True
    poll_system_seconds: int = 30
    poll_array_seconds: int = 300
    cpu_temp_threshold: int = 80
    cpu_usage_threshold: int = 95
    memory_usage_threshold: int = 90
    disk_temp_threshold: int = 50
    array_usage_threshold: int = 85

    @classmethod
    def from_dict(cls, data: dict) -> "UnraidConfig":
        """Create UnraidConfig from YAML dict."""
        polling = data.get("polling", {})
        thresholds = data.get("thresholds", {})
        return cls(
            enabled=data.get("enabled", False),
            host=data.get("host", ""),
            port=data.get("port", 80),
            use_ssl=data.get("use_ssl", False),
            verify_ssl=data.get("verify_ssl", True),
            poll_system_seconds=max(polling.get("system", 30), 10),
            poll_array_seconds=max(polling.get("array", 300), 30),
            cpu_temp_threshold=max(20, min(thresholds.get("cpu_temp", 80), 120)),
            cpu_usage_threshold=max(1, min(thresholds.get("cpu_usage", 95), 100)),
            memory_usage_threshold=max(1, min(thresholds.get("memory_usage", 90), 100)),
            disk_temp_threshold=max(20, min(thresholds.get("disk_temp", 50), 100)),
            array_usage_threshold=max(1, min(thresholds.get("array_usage", 85), 100)),
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
        try:
            return yaml.safe_load(content) or {}
        except yaml.YAMLError as e:
            raise ValueError(f"Invalid YAML in {path}: {e}") from e


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file="config/.env", env_file_encoding="utf-8")

    telegram_bot_token: str
    telegram_allowed_users: list[int] | str  # Accept string, convert to list
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    unraid_api_key: str | None = None
    ollama_host: str | None = None
    default_model: str | None = None
    config_path: str = "config/config.yaml"
    log_level: str = "INFO"

    # Build-time variable (ignored at runtime, but allowed in .env for convenience)
    docker_gid: str | None = None

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

        # Cache config objects once (config is read-only after startup)
        self._ai = AIConfig.from_dict(self._yaml_config.get("ai", {}))

        # Environment variable DEFAULT_MODEL overrides any YAML/code default
        if settings.default_model:
            self._ai.default_model = settings.default_model

        # Environment variable OLLAMA_HOST overrides any YAML/code default
        if settings.ollama_host:
            self._ai.ollama_host = settings.ollama_host

        self._bot_config = BotConfig.from_dict(self._yaml_config.get("bot", {}))
        self._docker = DockerConfig.from_dict(self._yaml_config.get("docker", {}))
        self._resource_monitoring = ResourceConfig.from_dict(
            self._yaml_config.get("resource_monitoring", {})
        )
        self._unraid = UnraidConfig.from_dict(self._yaml_config.get("unraid", {}))
        self._memory_management = MemoryConfig.from_dict(
            self._yaml_config.get("memory_management", {})
        )

    @property
    def ignored_containers(self) -> list[str]:
        """Get list of container names to ignore."""
        return self._yaml_config.get("ignored_containers") or []

    @property
    def protected_containers(self) -> list[str]:
        """Get list of containers that cannot be controlled via Telegram."""
        return self._yaml_config.get("protected_containers") or []

    @property
    def log_watching(self) -> dict[str, Any]:
        """Get log watching configuration.

        Returns YAML config if present, otherwise returns defaults.
        """
        config = self._yaml_config.get("log_watching")
        if config is None:
            config = dict(DEFAULT_LOG_WATCHING)
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
    def openai_api_key(self) -> str | None:
        """Get OpenAI API key."""
        return self._settings.openai_api_key

    @property
    def ollama_host(self) -> str | None:
        """Get Ollama host URL from environment."""
        return self._settings.ollama_host

    @property
    def log_level(self) -> str:
        """Get log level."""
        return self._settings.log_level

    @property
    def ai(self) -> AIConfig:
        """Get AI/Claude API configuration."""
        return self._ai

    @property
    def bot(self) -> BotConfig:
        """Get bot display and behaviour configuration."""
        return self._bot_config

    @property
    def docker(self) -> DockerConfig:
        """Get Docker connection configuration."""
        return self._docker

    @property
    def resource_monitoring(self) -> ResourceConfig:
        """Get resource monitoring configuration."""
        return self._resource_monitoring

    @property
    def unraid(self) -> UnraidConfig:
        """Get Unraid configuration."""
        return self._unraid

    @property
    def memory_management(self) -> MemoryConfig:
        """Get memory management configuration."""
        return self._memory_management


class ConfigWriter:
    """Writes and merges wizard results into config.yaml.

    Used by the setup wizard to create a fresh config or to update
    container roles while preserving manually-tuned thresholds.
    """

    def __init__(self, config_path: str) -> None:
        self._path = Path(config_path)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def write(
        self,
        *,
        unraid_host: str | None,
        unraid_port: int,
        unraid_use_ssl: bool,
        watched_containers: list[str],
        protected_containers: list[str],
        ignored_containers: list[str],
        priority_containers: list[str],
        killable_containers: list[str],
    ) -> None:
        """Write a fresh config.yaml from wizard results + defaults."""
        config = self._build_config(
            unraid_host=unraid_host,
            unraid_port=unraid_port,
            unraid_use_ssl=unraid_use_ssl,
            watched_containers=watched_containers,
            protected_containers=protected_containers,
            ignored_containers=ignored_containers,
            priority_containers=priority_containers,
            killable_containers=killable_containers,
        )
        self._write_yaml(config)

    def merge(
        self,
        *,
        unraid_host: str | None,
        unraid_port: int,
        unraid_use_ssl: bool,
        watched_containers: list[str],
        protected_containers: list[str],
        ignored_containers: list[str],
        priority_containers: list[str],
        killable_containers: list[str],
        skip_unraid: bool = False,
    ) -> None:
        """Merge wizard results into an existing config.yaml.

        Updates container roles and optionally Unraid connection settings
        while preserving all other values (thresholds, polling intervals, etc.).
        """
        existing = load_yaml_config(str(self._path))
        if not existing:
            # No existing config -- fall back to a full write
            self.write(
                unraid_host=unraid_host,
                unraid_port=unraid_port,
                unraid_use_ssl=unraid_use_ssl,
                watched_containers=watched_containers,
                protected_containers=protected_containers,
                ignored_containers=ignored_containers,
                priority_containers=priority_containers,
                killable_containers=killable_containers,
            )
            return

        # Container roles (always overwritten by wizard)
        existing.setdefault("log_watching", {})["containers"] = watched_containers
        existing["protected_containers"] = protected_containers
        existing["ignored_containers"] = ignored_containers
        existing.setdefault("memory_management", {})["priority_containers"] = priority_containers
        existing.setdefault("memory_management", {})["killable_containers"] = killable_containers

        # Unraid connection (only update if user entered new details)
        if not skip_unraid:
            unraid_section = existing.setdefault("unraid", {})
            unraid_enabled = unraid_host is not None
            unraid_section["enabled"] = unraid_enabled
            if unraid_enabled:
                unraid_section["host"] = unraid_host
            unraid_section["port"] = unraid_port
            unraid_section["use_ssl"] = unraid_use_ssl

        self._write_yaml(existing)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_config(
        self,
        *,
        unraid_host: str | None,
        unraid_port: int,
        unraid_use_ssl: bool,
        watched_containers: list[str],
        protected_containers: list[str],
        ignored_containers: list[str],
        priority_containers: list[str],
        killable_containers: list[str],
    ) -> dict[str, Any]:
        """Build a complete config dict from wizard results + defaults."""
        unraid_enabled = unraid_host is not None
        return {
            "ai": {
                "models": {
                    "pattern_analyzer": "claude-haiku-4-5-20251001",
                    "nl_processor": "claude-sonnet-4-5-20250929",
                    "diagnostic": "claude-haiku-4-5-20251001",
                },
                "max_tokens": {
                    "pattern_analyzer": 500,
                    "nl_processor": 1024,
                    "diagnostic_brief": 300,
                    "diagnostic_detail": 800,
                },
                "nl_processor": {
                    "max_tool_iterations": 10,
                    "max_conversation_exchanges": 5,
                },
                "pattern_analyzer_context_lines": 30,
                "diagnostic_context_expiry_seconds": 600,
            },
            "bot": {
                "confirmation_timeout_seconds": 60,
                "log_display": {
                    "max_lines": 100,
                    "max_chars": 4000,
                    "nl_max_chars": 3000,
                    "diagnose_max_lines": 500,
                },
                "error_display_max_chars": 200,
            },
            "docker": {
                "socket_path": "unix:///var/run/docker.sock",
            },
            "ignored_containers": ignored_containers,
            "protected_containers": protected_containers,
            "log_watching": {
                "containers": watched_containers,
                "error_patterns": list(DEFAULT_ERROR_PATTERNS),
                "ignore_patterns": list(DEFAULT_IGNORE_PATTERNS),
                "cooldown_seconds": 900,
            },
            "resource_monitoring": {
                "enabled": True,
                "poll_interval_seconds": 60,
                "sustained_threshold_seconds": 120,
                "defaults": {
                    "cpu_percent": 80,
                    "memory_percent": 85,
                },
                "containers": {},
            },
            "memory_management": {
                "enabled": bool(killable_containers),
                "warning_threshold": 90,
                "critical_threshold": 95,
                "safe_threshold": 80,
                "kill_delay_seconds": 60,
                "stabilization_wait": 180,
                "priority_containers": priority_containers,
                "killable_containers": killable_containers,
            },
            "unraid": {
                "enabled": unraid_enabled,
                "host": unraid_host or "your-unraid-ip",
                "port": unraid_port,
                "use_ssl": unraid_use_ssl,
                "verify_ssl": True,
                "polling": {
                    "system": 30,
                    "array": 300,
                },
                "thresholds": {
                    "cpu_temp": 80,
                    "cpu_usage": 95,
                    "memory_usage": 90,
                    "disk_temp": 50,
                    "array_usage": 85,
                },
            },
        }

    def _write_yaml(self, config: dict[str, Any]) -> None:
        """Write config dict to the YAML file atomically."""
        import tempfile
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Write to temp file then atomically rename to prevent corruption
        fd, tmp_path = tempfile.mkstemp(
            dir=self._path.parent, suffix=".tmp", prefix=".config_"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                yaml.dump(config, f, default_flow_style=False, sort_keys=False)
            os.replace(tmp_path, self._path)
        except Exception:
            # Clean up temp file on failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise


DEFAULT_CONFIG_TEMPLATE = '''# Unraid Monitor Bot Configuration
# Generated automatically on first run

# AI / Claude API configuration
ai:
  models:
    pattern_analyzer: "claude-haiku-4-5-20251001"
    nl_processor: "claude-sonnet-4-5-20250929"
    diagnostic: "claude-haiku-4-5-20251001"
  max_tokens:
    pattern_analyzer: 500
    nl_processor: 1024
    diagnostic_brief: 300
    diagnostic_detail: 800
  nl_processor:
    max_tool_iterations: 10
    max_conversation_exchanges: 5
  pattern_analyzer_context_lines: 30
  diagnostic_context_expiry_seconds: 600

# Bot display and behaviour settings
bot:
  confirmation_timeout_seconds: 60
  log_display:
    max_lines: 100
    max_chars: 4000
    nl_max_chars: 3000
    diagnose_max_lines: 500
  error_display_max_chars: 200

# Docker connection settings
docker:
  socket_path: "unix:///var/run/docker.sock"

# Containers to ignore (won't be monitored or shown)
ignored_containers: []

# Containers that cannot be controlled via Telegram
protected_containers:
  - unraid-monitor-bot

# Log watching configuration
log_watching:
  containers: []  # Add container names to watch
  error_patterns:
    - "error"
    - "exception"
    - "fatal"
    - "failed"
    - "critical"
    - "panic"
    - "traceback"
  ignore_patterns:
    - "DeprecationWarning"
    - "DEBUG"
  cooldown_seconds: 900

# Resource monitoring (CPU/memory per container)
resource_monitoring:
  enabled: true
  poll_interval_seconds: 60
  sustained_threshold_seconds: 120
  defaults:
    cpu_percent: 80
    memory_percent: 85
  containers: {}  # Per-container overrides, e.g.: plex: { cpu_percent: 95 }

# Memory pressure management (system-wide)
memory_management:
  enabled: false
  warning_threshold: 90
  critical_threshold: 95
  safe_threshold: 80
  kill_delay_seconds: 60
  stabilization_wait: 180
  priority_containers: []
  killable_containers: []

# Unraid server monitoring
unraid:
  enabled: false
  host: "your-unraid-ip"
  port: 443
  use_ssl: true
  # WARNING: Set verify_ssl to true in production for security
  # Only set to false if using self-signed certs and you understand the risk
  verify_ssl: true
  polling:
    system: 30
    array: 300
  thresholds:
    cpu_temp: 80
    cpu_usage: 95
    memory_usage: 90
    disk_temp: 50
    array_usage: 85
'''


def generate_default_config(config_path: str) -> bool:
    """Generate default config file if it doesn't exist.

    Returns True if config was created, False if it already existed.
    """
    path = Path(config_path)

    if path.exists():
        return False

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(DEFAULT_CONFIG_TEMPLATE)
    return True
