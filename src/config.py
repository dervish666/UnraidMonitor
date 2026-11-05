import logging
import math
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.constants import (
    AUTOHEAL_MAX_RESTARTS,
    AUTOHEAL_WINDOW_MINUTES,
    DEFAULT_COOLDOWN_SECONDS,
    DEFAULT_CPU_PERCENT,
    DEFAULT_HAIKU_MODEL,
    DEFAULT_MEMORY_PERCENT,
    DEFAULT_OLLAMA_HOST,
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_SONNET_MODEL,
    DIAGNOSTIC_BRIEF_MAX_TOKENS,
    DIAGNOSTIC_CONTEXT_EXPIRY_SECONDS,
    DIAGNOSTIC_DETAIL_MAX_TOKENS,
    DIAGNOSE_MAX_LINES,
    ERROR_DISPLAY_MAX_CHARS,
    IMAGE_UPDATE_POLL_INTERVAL_HOURS,
    LOG_MAX_CHARS,
    LOG_MAX_LINES,
    MEMORY_CRITICAL_THRESHOLD,
    MEMORY_KILL_DELAY_SECONDS,
    MEMORY_SAFE_THRESHOLD,
    MEMORY_STABILIZATION_WAIT,
    MEMORY_WARNING_THRESHOLD,
    NL_LOG_MAX_CHARS,
    NL_MAX_CONVERSATION_EXCHANGES,
    NL_MAX_TOOL_ITERATIONS,
    NL_PROCESSOR_MAX_TOKENS,
    NUT_BATTERY_CHARGE_THRESHOLD,
    NUT_DEFAULT_PORT,
    NUT_LOAD_THRESHOLD,
    NUT_POLL_SECONDS,
    PATTERN_ANALYZER_CONTEXT_LINES,
    PATTERN_ANALYZER_MAX_TOKENS,
    RESOURCE_POLL_INTERVAL_SECONDS,
    RESOURCE_SUSTAINED_THRESHOLD_SECONDS,
    UNRAID_ARRAY_USAGE_THRESHOLD,
    UNRAID_CPU_TEMP_THRESHOLD,
    UNRAID_CPU_USAGE_THRESHOLD,
    UNRAID_DISK_TEMP_THRESHOLD,
    UNRAID_MEMORY_USAGE_THRESHOLD,
    UNRAID_POLL_ARRAY_SECONDS,
    UNRAID_POLL_NOTIFICATIONS_SECONDS,
    UNRAID_NOTIFICATION_MIN_IMPORTANCE,
    NOTIFICATION_IMPORTANCE_LEVELS,
    UNRAID_POLL_SYSTEM_SECONDS,
)

logger = logging.getLogger(__name__)

# What the config generator writes when no Unraid host is known. Never polled.
PLACEHOLDER_UNRAID_HOST = "your-unraid-ip"

_TRUE_STRINGS = {"true", "yes", "on", "1"}
_FALSE_STRINGS = {"false", "no", "off", "0"}


class ConfigError(ValueError):
    """A config.yaml value that cannot be read as the type it needs."""


class _Section:
    """Typed, tolerant reads from one YAML mapping.

    A missing or empty section (``unraid:`` with nothing under it reads as
    None) is an empty mapping, so every key takes its default. Values are
    coerced where the intent is unambiguous: the string "false" is False, not
    truthy, and "90" is 90. Anything else raises ConfigError naming the path,
    the expected type and what was found. Keys nobody read are reported by
    ``warn_unknown()``, which catches typos like ``treshold``.
    """

    def __init__(self, data: Any, path: str) -> None:
        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise ConfigError(f"{path}: expected a mapping, got {type(data).__name__}")
        self._data = data
        self._path = path
        self._seen: set[str] = set()
        self._children: list[_Section] = []

    def _raw(self, key: str) -> Any:
        self._seen.add(key)
        return self._data.get(key)

    def _where(self, key: str) -> str:
        return f"{self._path}.{key}" if self._path else key

    def section(self, key: str) -> "_Section":
        child = _Section(self._raw(key), self._where(key))
        self._children.append(child)
        return child

    def get_bool(self, key: str, default: bool) -> bool:
        value = self._raw(key)
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, int) and value in (0, 1):
            return bool(value)
        if isinstance(value, str) and value.strip().lower() in _TRUE_STRINGS | _FALSE_STRINGS:
            return value.strip().lower() in _TRUE_STRINGS
        raise ConfigError(f"{self._where(key)}: expected true or false, got {value!r}")

    def get_int(self, key: str, default: int, lo: int | None = None, hi: int | None = None) -> int:
        value = self._raw(key)
        if value is None:
            result = default
        elif isinstance(value, bool):
            raise ConfigError(f"{self._where(key)}: expected a number, got {value!r}")
        elif isinstance(value, int):
            result = value
        else:
            # A decimal such as 92.5 or 0.5 rounds to the nearest whole number;
            # releases before 0.23 accepted decimals, so refusing them would
            # stop an upgraded bot at boot.
            try:
                number = float(value.strip()) if isinstance(value, str) else float(value)
            except (TypeError, ValueError):
                number = math.nan
            if not math.isfinite(number):
                raise ConfigError(f"{self._where(key)}: expected a number, got {value!r}")
            result = round(number)
        if lo is not None:
            result = max(lo, result)
        if hi is not None:
            result = min(hi, result)
        return result

    def get_str(self, key: str, default: str) -> str:
        value = self._raw(key)
        if value is None:
            return default
        if isinstance(value, (str, int, float)) and not isinstance(value, bool):
            return str(value).strip()
        raise ConfigError(f"{self._where(key)}: expected text, got {type(value).__name__}")

    def str_list(self, key: str) -> list[str]:
        value = self._raw(key)
        if value is None:
            return []
        if not isinstance(value, list):
            raise ConfigError(f"{self._where(key)}: expected a list, got {type(value).__name__}")
        return [str(v) for v in value if v is not None]

    def mapping(self, key: str) -> dict[str, Any]:
        value = self._raw(key)
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ConfigError(f"{self._where(key)}: expected a mapping, got {type(value).__name__}")
        return value

    def warn_unknown(self) -> None:
        unknown = sorted(str(k) for k in self._data if k not in self._seen)
        for key in unknown:
            logger.warning(f"Unknown config key {self._where(key)!r} is ignored (typo?)")
        for child in self._children:
            child.warn_unknown()


def atomic_yaml_write(data: dict[str, Any], target: Path) -> None:
    """Write a dict as YAML atomically using tempfile + os.replace."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=target.parent, suffix=".tmp", prefix=".config_"
    )
    try:
        os.fchmod(fd, 0o644)  # mkstemp creates 0o600; match version_store.py
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)
        os.replace(tmp_path, target)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


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
    "cooldown_seconds": DEFAULT_COOLDOWN_SECONDS,
    "container_ignores": {},
}


@dataclass
class AIConfig:
    """Configuration for AI/Claude API settings."""

    # Model names
    pattern_analyzer_model: str = DEFAULT_HAIKU_MODEL
    nl_processor_model: str = DEFAULT_SONNET_MODEL
    diagnostic_model: str = DEFAULT_HAIKU_MODEL

    # Token limits
    pattern_analyzer_max_tokens: int = PATTERN_ANALYZER_MAX_TOKENS
    nl_processor_max_tokens: int = NL_PROCESSOR_MAX_TOKENS
    diagnostic_brief_max_tokens: int = DIAGNOSTIC_BRIEF_MAX_TOKENS
    diagnostic_detail_max_tokens: int = DIAGNOSTIC_DETAIL_MAX_TOKENS

    # NL processor settings
    nl_max_tool_iterations: int = NL_MAX_TOOL_ITERATIONS
    nl_max_conversation_exchanges: int = NL_MAX_CONVERSATION_EXCHANGES

    # Pattern analyzer settings
    pattern_analyzer_context_lines: int = PATTERN_ANALYZER_CONTEXT_LINES

    # Diagnostic settings
    diagnostic_context_expiry_seconds: int = DIAGNOSTIC_CONTEXT_EXPIRY_SECONDS

    # Multi-provider settings
    default_model: str = DEFAULT_HAIKU_MODEL
    ollama_host: str = DEFAULT_OLLAMA_HOST
    # Default model used when Ollama is auto-selected as the provider
    ollama_default_model: str = DEFAULT_OLLAMA_MODEL

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AIConfig":
        """Create AIConfig from YAML dict."""
        return cls._read(_Section(data, "ai"))

    @classmethod
    def _read(cls, sec: "_Section") -> "AIConfig":
        models = sec.section("models")
        max_tokens = sec.section("max_tokens")
        nl = sec.section("nl_processor")
        ollama = sec.section("providers").section("ollama")
        return cls(
            pattern_analyzer_model=models.get_str("pattern_analyzer", DEFAULT_HAIKU_MODEL),
            nl_processor_model=models.get_str("nl_processor", DEFAULT_SONNET_MODEL),
            diagnostic_model=models.get_str("diagnostic", DEFAULT_HAIKU_MODEL),
            pattern_analyzer_max_tokens=max_tokens.get_int("pattern_analyzer", PATTERN_ANALYZER_MAX_TOKENS, lo=1),
            nl_processor_max_tokens=max_tokens.get_int("nl_processor", NL_PROCESSOR_MAX_TOKENS, lo=1),
            diagnostic_brief_max_tokens=max_tokens.get_int("diagnostic_brief", DIAGNOSTIC_BRIEF_MAX_TOKENS, lo=1),
            diagnostic_detail_max_tokens=max_tokens.get_int("diagnostic_detail", DIAGNOSTIC_DETAIL_MAX_TOKENS, lo=1),
            nl_max_tool_iterations=nl.get_int("max_tool_iterations", NL_MAX_TOOL_ITERATIONS, lo=1),
            nl_max_conversation_exchanges=nl.get_int("max_conversation_exchanges", NL_MAX_CONVERSATION_EXCHANGES, lo=1),
            pattern_analyzer_context_lines=sec.get_int("pattern_analyzer_context_lines", PATTERN_ANALYZER_CONTEXT_LINES, lo=1),
            diagnostic_context_expiry_seconds=sec.get_int("diagnostic_context_expiry_seconds", DIAGNOSTIC_CONTEXT_EXPIRY_SECONDS, lo=1),
            default_model=sec.get_str("default_model", DEFAULT_HAIKU_MODEL),
            ollama_host=ollama.get_str("host", DEFAULT_OLLAMA_HOST),
            ollama_default_model=ollama.get_str("default_model", DEFAULT_OLLAMA_MODEL),
        )


@dataclass
class BotConfig:
    """Configuration for Telegram bot display and behaviour."""

    log_max_lines: int = LOG_MAX_LINES
    log_max_chars: int = LOG_MAX_CHARS
    nl_log_max_chars: int = NL_LOG_MAX_CHARS
    diagnose_max_lines: int = DIAGNOSE_MAX_LINES
    error_display_max_chars: int = ERROR_DISPLAY_MAX_CHARS

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BotConfig":
        """Create BotConfig from YAML dict."""
        return cls._read(_Section(data, "bot"))

    @classmethod
    def _read(cls, sec: "_Section") -> "BotConfig":
        log_display = sec.section("log_display")
        return cls(
            log_max_lines=log_display.get_int("max_lines", LOG_MAX_LINES, lo=1),
            log_max_chars=log_display.get_int("max_chars", LOG_MAX_CHARS, lo=100),
            nl_log_max_chars=log_display.get_int("nl_max_chars", NL_LOG_MAX_CHARS, lo=100),
            diagnose_max_lines=log_display.get_int("diagnose_max_lines", DIAGNOSE_MAX_LINES, lo=1),
            error_display_max_chars=sec.get_int("error_display_max_chars", ERROR_DISPLAY_MAX_CHARS, lo=20),
        )


@dataclass
class DockerConfig:
    """Configuration for Docker connection."""

    socket_path: str = "unix:///var/run/docker.sock"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DockerConfig":
        """Create DockerConfig from YAML dict."""
        return cls._read(_Section(data, "docker"))

    @classmethod
    def _read(cls, sec: "_Section") -> "DockerConfig":
        return cls(socket_path=sec.get_str("socket_path", "unix:///var/run/docker.sock"))


@dataclass
class ResourceConfig:
    """Configuration for resource monitoring."""

    enabled: bool = True
    poll_interval_seconds: int = RESOURCE_POLL_INTERVAL_SECONDS
    sustained_threshold_seconds: int = RESOURCE_SUSTAINED_THRESHOLD_SECONDS
    default_cpu_percent: int = DEFAULT_CPU_PERCENT
    default_memory_percent: int = DEFAULT_MEMORY_PERCENT
    container_overrides: dict[str, dict[str, int]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ResourceConfig":
        """Create ResourceConfig from YAML dict."""
        return cls._read(_Section(data, "resource_monitoring"))

    @classmethod
    def _read(cls, sec: "_Section") -> "ResourceConfig":
        defaults = sec.section("defaults")
        overrides: dict[str, dict[str, int]] = {}
        for name, values in sec.mapping("containers").items():
            entry = _Section(values, f"resource_monitoring.containers.{name}")
            item = {}
            for metric in ("cpu_percent", "memory_percent"):
                if entry._data.get(metric) is not None:
                    item[metric] = entry.get_int(metric, 0, lo=1, hi=100 if metric == "memory_percent" else None)
            entry.warn_unknown()
            overrides[str(name)] = item
        return cls(
            enabled=sec.get_bool("enabled", True),
            poll_interval_seconds=sec.get_int("poll_interval_seconds", RESOURCE_POLL_INTERVAL_SECONDS, lo=10),
            sustained_threshold_seconds=sec.get_int("sustained_threshold_seconds", RESOURCE_SUSTAINED_THRESHOLD_SECONDS, lo=10),
            default_cpu_percent=defaults.get_int("cpu_percent", DEFAULT_CPU_PERCENT, lo=1),
            default_memory_percent=defaults.get_int("memory_percent", DEFAULT_MEMORY_PERCENT, lo=1, hi=100),
            container_overrides=overrides,
        )

    config_path: str | None = None

    def get_thresholds(self, container_name: str) -> tuple[int, int]:
        """Get CPU and memory thresholds for a container.

        Returns:
            Tuple of (cpu_percent, memory_percent) thresholds.
        """
        overrides = self.container_overrides.get(container_name, {})
        cpu = overrides.get("cpu_percent", self.default_cpu_percent)
        memory = overrides.get("memory_percent", self.default_memory_percent)
        return cpu, memory

    def set_threshold(self, container_name: str, metric: str, value: int) -> None:
        """Set a per-container threshold and persist to config.yaml.

        Args:
            container_name: Container name.
            metric: "cpu" or "memory".
            value: Threshold percentage, or 0 to remove the override.
                   CPU can exceed 100% on multi-core (per-core reporting).
                   Memory is clamped to 1-100%.
        """
        key = f"{metric}_percent"

        if value == 0:
            if container_name in self.container_overrides:
                self.container_overrides[container_name].pop(key, None)
                if not self.container_overrides[container_name]:
                    del self.container_overrides[container_name]
        else:
            if metric == "cpu":
                value = max(1, value)
            else:
                value = max(1, min(value, 100))
            self.container_overrides.setdefault(container_name, {})[key] = value

        self._persist()

    def _persist(self) -> None:
        """Write current container_overrides back to config.yaml."""
        def _update(data: dict[str, Any]) -> None:
            rm = config_section(data, "resource_monitoring")
            if self.container_overrides:
                rm["containers"] = self.container_overrides
            else:
                rm.pop("containers", None)

        update_yaml_config(self.config_path, _update)


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
    restart_containers: list[str] = field(default_factory=list)  # Offer restart on pressure alerts
    config_path: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MemoryConfig":
        return cls._read(_Section(data, "memory_management"))

    @classmethod
    def _read(cls, sec: "_Section") -> "MemoryConfig":
        config = cls(
            enabled=sec.get_bool("enabled", False),
            warning_threshold=sec.get_int("warning_threshold", MEMORY_WARNING_THRESHOLD, lo=50, hi=99),
            critical_threshold=sec.get_int("critical_threshold", MEMORY_CRITICAL_THRESHOLD, lo=60, hi=100),
            safe_threshold=sec.get_int("safe_threshold", MEMORY_SAFE_THRESHOLD, lo=30, hi=95),
            kill_delay_seconds=sec.get_int("kill_delay_seconds", MEMORY_KILL_DELAY_SECONDS, lo=10),
            stabilization_wait=sec.get_int("stabilization_wait", MEMORY_STABILIZATION_WAIT, lo=10),
            priority_containers=sec.str_list("priority_containers"),
            killable_containers=sec.str_list("killable_containers"),
            restart_containers=sec.str_list("restart_containers"),
        )
        overlap = [c for c in config.killable_containers if c in set(config.priority_containers)]
        if overlap:
            # Priority means never kill. A name in both lists used to become
            # the first automatic target.
            logger.warning(
                "Containers listed as both priority and killable will not be killed: %s",
                ", ".join(overlap),
            )
            config.killable_containers = [
                c for c in config.killable_containers if c not in set(overlap)
            ]
        # Validate threshold ordering: critical > warning > safe
        if not (config.critical_threshold > config.warning_threshold > config.safe_threshold):
            logger.warning(
                "Memory thresholds out of order (critical=%d, warning=%d, safe=%d), "
                "using defaults (95, 90, 80)",
                config.critical_threshold, config.warning_threshold, config.safe_threshold,
            )
            config.critical_threshold = MEMORY_CRITICAL_THRESHOLD
            config.warning_threshold = MEMORY_WARNING_THRESHOLD
            config.safe_threshold = MEMORY_SAFE_THRESHOLD
        return config

    def set_restart_containers(self, containers: list[str]) -> None:
        """Set the restart-offer list and persist it to config.yaml.

        These containers get a one-tap Restart button on memory pressure
        alerts. The running MemoryMonitor holds a reference to this object,
        so the change applies live without a restart. Names failing
        container-name validation are dropped — defense in depth; the picker
        callback already rejects anything not in the live container list.
        """
        from src.utils.formatting import validate_container_name  # local: config stays free of UI-layer imports

        valid = [c for c in containers if validate_container_name(c)]
        dropped = set(containers) - set(valid)
        if dropped:
            logger.warning(f"Dropped invalid memory restart container names: {sorted(dropped)}")
        self.restart_containers = valid
        self._persist_restart_containers()

    def _persist_restart_containers(self) -> None:
        """Write only restart_containers back to the memory_management section.

        The section's other keys (thresholds, killable list, etc.) keep
        whatever is currently on disk.
        """
        def _update(data: dict[str, Any]) -> None:
            config_section(data, "memory_management")["restart_containers"] = self.restart_containers

        update_yaml_config(self.config_path, _update)


@dataclass
class ImageUpdatesConfig:
    """Configuration for proactive image-update detection."""

    enabled: bool = False
    poll_interval_hours: int = IMAGE_UPDATE_POLL_INTERVAL_HOURS
    config_path: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ImageUpdatesConfig":
        return cls._read(_Section(data, "image_updates"))

    @classmethod
    def _read(cls, sec: "_Section") -> "ImageUpdatesConfig":
        return cls(
            enabled=sec.get_bool("enabled", False),
            poll_interval_hours=sec.get_int("poll_interval_hours", IMAGE_UPDATE_POLL_INTERVAL_HOURS, lo=1),
        )

    def set_enabled(self, enabled: bool) -> None:
        """Toggle image-update detection and persist to config.yaml.

        Takes effect after a restart (the monitor is only constructed at
        startup when enabled).
        """
        self.enabled = enabled
        self._persist()

    def _persist(self) -> None:
        """Write current image-update settings back to config.yaml."""
        def _update(data: dict[str, Any]) -> None:
            section = config_section(data, "image_updates")
            section["enabled"] = self.enabled
            section["poll_interval_hours"] = self.poll_interval_hours

        update_yaml_config(self.config_path, _update)


@dataclass
class AutoHealConfig:
    """Configuration for auto-restarting unhealthy containers."""

    enabled: bool = True
    containers: list[str] = field(default_factory=list)
    max_restarts: int = AUTOHEAL_MAX_RESTARTS
    window_minutes: int = AUTOHEAL_WINDOW_MINUTES
    config_path: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AutoHealConfig":
        return cls._read(_Section(data, "auto_heal"))

    @classmethod
    def _read(cls, sec: "_Section") -> "AutoHealConfig":
        return cls(
            enabled=sec.get_bool("enabled", True),
            containers=sec.str_list("containers"),
            max_restarts=sec.get_int("max_restarts", AUTOHEAL_MAX_RESTARTS, lo=1),
            window_minutes=sec.get_int("window_minutes", AUTOHEAL_WINDOW_MINUTES, lo=1),
        )

    def set_containers(self, containers: list[str]) -> None:
        """Set the opted-in container list and persist to config.yaml.

        Auto-heal is considered enabled only while at least one container is
        opted in, so ``enabled`` tracks whether the list is non-empty. The
        running AutoHealer holds a reference to this object, so the change
        applies live without a restart. Names failing container-name
        validation are dropped — defense in depth; the picker callback
        already rejects anything not in the live container list.
        """
        from src.utils.formatting import validate_container_name  # local: config stays free of UI-layer imports

        valid = [c for c in containers if validate_container_name(c)]
        dropped = set(containers) - set(valid)
        if dropped:
            logger.warning(f"Dropped invalid auto-heal container names: {sorted(dropped)}")
        self.containers = valid
        self.enabled = bool(self.containers)
        self._persist()

    def _persist(self) -> None:
        """Write current auto-heal settings back to config.yaml."""
        def _update(data: dict[str, Any]) -> None:
            section = config_section(data, "auto_heal")
            section["enabled"] = self.enabled
            section["containers"] = self.containers
            section["max_restarts"] = self.max_restarts
            section["window_minutes"] = self.window_minutes

        update_yaml_config(self.config_path, _update)


@dataclass
class UnraidConfig:
    """Configuration for Unraid server monitoring."""

    enabled: bool = False
    host: str = ""
    port: int = 80
    use_ssl: bool = False
    verify_ssl: bool = True
    poll_system_seconds: int = UNRAID_POLL_SYSTEM_SECONDS
    poll_array_seconds: int = UNRAID_POLL_ARRAY_SECONDS
    cpu_temp_threshold: int = UNRAID_CPU_TEMP_THRESHOLD
    cpu_usage_threshold: int = UNRAID_CPU_USAGE_THRESHOLD
    memory_usage_threshold: int = UNRAID_MEMORY_USAGE_THRESHOLD
    disk_temp_threshold: int = UNRAID_DISK_TEMP_THRESHOLD
    array_usage_threshold: int = UNRAID_ARRAY_USAGE_THRESHOLD

    # Relay of Unraid's own notification feed. Off by default: Unraid emits a
    # lot of INFO chatter (backup done, parity tuning pause/resume), so this is
    # opt-in and floored at WARNING unless the user says otherwise.
    notifications_enabled: bool = False
    notifications_min_importance: str = UNRAID_NOTIFICATION_MIN_IMPORTANCE
    poll_notifications_seconds: int = UNRAID_POLL_NOTIFICATIONS_SECONDS

    config_path: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UnraidConfig":
        """Create UnraidConfig from YAML dict."""
        return cls._read(_Section(data, "unraid"))

    @classmethod
    def _read(cls, sec: "_Section") -> "UnraidConfig":
        polling = sec.section("polling")
        thresholds = sec.section("thresholds")
        notifications = sec.section("notifications")
        importance = notifications.get_str(
            "min_importance", UNRAID_NOTIFICATION_MIN_IMPORTANCE
        ).upper()
        if importance not in NOTIFICATION_IMPORTANCE_LEVELS:
            logger.warning(
                f"Unknown unraid.notifications.min_importance {importance!r}; "
                f"falling back to {UNRAID_NOTIFICATION_MIN_IMPORTANCE}"
            )
            importance = UNRAID_NOTIFICATION_MIN_IMPORTANCE
        host = sec.get_str("host", "")
        enabled = sec.get_bool("enabled", False)
        if host == PLACEHOLDER_UNRAID_HOST:
            # The generated config's hint, not an address: never poll it (and
            # NUT, which falls back to this host, must not try it either).
            if enabled:
                logger.warning(
                    f"unraid.host is still the placeholder {PLACEHOLDER_UNRAID_HOST!r}; "
                    "Unraid monitoring stays off until a real host is set"
                )
            host, enabled = "", False
        return cls(
            notifications_enabled=notifications.get_bool("enabled", False),
            notifications_min_importance=importance,
            poll_notifications_seconds=polling.get_int("notifications", UNRAID_POLL_NOTIFICATIONS_SECONDS, lo=30),
            enabled=enabled,
            host=host,
            port=sec.get_int("port", 80, lo=1, hi=65535),
            use_ssl=sec.get_bool("use_ssl", False),
            verify_ssl=sec.get_bool("verify_ssl", True),
            poll_system_seconds=polling.get_int("system", UNRAID_POLL_SYSTEM_SECONDS, lo=10),
            poll_array_seconds=polling.get_int("array", UNRAID_POLL_ARRAY_SECONDS, lo=30),
            cpu_temp_threshold=thresholds.get_int("cpu_temp", UNRAID_CPU_TEMP_THRESHOLD, lo=20, hi=120),
            cpu_usage_threshold=thresholds.get_int("cpu_usage", UNRAID_CPU_USAGE_THRESHOLD, lo=1, hi=100),
            memory_usage_threshold=thresholds.get_int("memory_usage", UNRAID_MEMORY_USAGE_THRESHOLD, lo=1, hi=100),
            disk_temp_threshold=thresholds.get_int("disk_temp", UNRAID_DISK_TEMP_THRESHOLD, lo=20, hi=100),
            array_usage_threshold=thresholds.get_int("array_usage", UNRAID_ARRAY_USAGE_THRESHOLD, lo=1, hi=100),
        )

    def set_threshold(self, metric: str, value: int) -> None:
        """Set a threshold and persist to config.yaml.

        Args:
            metric: "array_usage", "disk_temp", "cpu_temp", or "cpu_usage".
            value: New threshold value, or 0 to reset to default.
        """
        if metric == "array_usage":
            if value == 0:
                value = UNRAID_ARRAY_USAGE_THRESHOLD
            self.array_usage_threshold = max(1, min(value, 100))
        elif metric == "disk_temp":
            if value == 0:
                value = UNRAID_DISK_TEMP_THRESHOLD
            self.disk_temp_threshold = max(20, min(value, 100))
        elif metric == "cpu_temp":
            if value == 0:
                value = UNRAID_CPU_TEMP_THRESHOLD
            self.cpu_temp_threshold = max(20, min(value, 120))
        elif metric == "cpu_usage":
            if value == 0:
                value = UNRAID_CPU_USAGE_THRESHOLD
            self.cpu_usage_threshold = max(1, min(value, 100))
        else:
            return
        self._persist()

    def _persist(self) -> None:
        """Write current thresholds back to config.yaml."""
        def _update(data: dict[str, Any]) -> None:
            unraid = config_section(data, "unraid")
            thresholds = config_section(unraid, "thresholds")
            thresholds["array_usage"] = self.array_usage_threshold
            thresholds["disk_temp"] = self.disk_temp_threshold
            thresholds["cpu_temp"] = self.cpu_temp_threshold
            thresholds["cpu_usage"] = self.cpu_usage_threshold

            notifications = config_section(unraid, "notifications")
            notifications["enabled"] = self.notifications_enabled
            notifications["min_importance"] = self.notifications_min_importance

        update_yaml_config(self.config_path, _update)

    def set_notifications_enabled(self, enabled: bool) -> None:
        """Toggle the Unraid notification relay and persist to config.yaml.

        Takes effect after a restart -- the monitor is only constructed at
        startup when enabled.
        """
        self.notifications_enabled = enabled
        self._persist()

    def set_notifications_min_importance(self, importance: str) -> None:
        """Set the minimum importance to relay and persist to config.yaml.

        Applies live: the running monitor reads this off the shared config
        object on every poll.
        """
        importance = importance.upper()
        if importance not in NOTIFICATION_IMPORTANCE_LEVELS:
            logger.warning(f"Ignoring unknown notification importance {importance!r}")
            return
        self.notifications_min_importance = importance
        self._persist()


def config_section(data: dict[str, Any], key: str) -> dict[str, Any]:
    """The mapping at data[key], created when missing or empty.

    ``data.setdefault(key, {})`` returns None for a section written as
    ``key:`` with nothing under it, so every writer goes through here.
    """
    section = data.get(key)
    if not isinstance(section, dict):
        section = {}
        data[key] = section
    return section


def update_yaml_config(
    config_path: str | None, update: Callable[[dict[str, Any]], None]
) -> None:
    """Load config.yaml, apply *update* to it, and write it back atomically.

    No-op without a path or file: tests and first-run have nothing to persist.
    """
    if not config_path:
        return
    path = Path(config_path)
    if not path.exists():
        return
    data = load_yaml_config(str(path))
    update(data)
    atomic_yaml_write(data, path)


def unknown_env_file_keys(env_file: str = "config/.env") -> list[str]:
    """Keys in the .env file that Settings does not read (typos, old names)."""
    from dotenv import dotenv_values

    if not os.path.exists(env_file):
        return []
    known = {name.upper() for name in Settings.model_fields}
    return sorted(k for k in dotenv_values(env_file) if k and k.upper() not in known)


def describe_settings_error(error: Exception) -> str:
    """A settings failure as "FIELD: problem" lines, without the values.

    pydantic's default message echoes the input, which for this model can be
    the bot token or an API key, straight into the container log.
    """
    from pydantic import ValidationError

    if not isinstance(error, ValidationError):
        return type(error).__name__
    lines = []
    for item in error.errors(include_input=False, include_url=False):
        field_name = ".".join(str(p) for p in item.get("loc", ())) or "settings"
        lines.append(f"{field_name.upper()}: {item.get('msg', 'invalid')}")
    return "; ".join(lines)


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


_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


class Settings(BaseSettings):
    # extra="ignore": a stray or misspelt key in config/.env used to stop the
    # bot at boot. unknown_env_file_keys() names them in the log instead.
    model_config = SettingsConfigDict(
        env_file="config/.env", env_file_encoding="utf-8", extra="ignore"
    )

    telegram_bot_token: str
    telegram_allowed_users: list[int] | str  # Accept string, convert to list
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    unraid_api_key: str | None = None
    nut_username: str | None = None
    nut_password: str | None = None
    ollama_host: str | None = None
    default_model: str | None = None
    config_path: str = "config/config.yaml"
    log_level: str = "INFO"

    # Build-time variable (ignored at runtime, but allowed in .env for convenience)
    docker_gid: str | None = None

    @field_validator("log_level", mode="before")
    @classmethod
    def normalise_log_level(cls, v: Any) -> str:
        """LOG_LEVEL=debug works; an unknown level falls back to INFO, loudly."""
        level = str(v or "INFO").strip().upper()
        if level == "WARN":
            level = "WARNING"
        if level not in _LOG_LEVELS:
            logger.warning(f"LOG_LEVEL {v!r} is not one of {', '.join(_LOG_LEVELS)}; using INFO")
            return "INFO"
        return level

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
                # No echo of the value: a pasted token here would land in the log.
                raise ValueError("TELEGRAM_ALLOWED_USERS must be comma-separated numeric user IDs")
        raise ValueError(f"TELEGRAM_ALLOWED_USERS must be a string or list, got: {type(v)}")


@dataclass
class NutConfig:
    """Configuration for UPS monitoring via a NUT server.

    On by default. It stays dormant unless a host resolves, and a host that
    never answers is logged rather than alerted about, so enabling it for
    everyone does not spam the majority who run no UPS.
    """

    enabled: bool = True
    host: str = ""
    port: int = NUT_DEFAULT_PORT
    ups_name: str = ""
    poll_seconds: int = NUT_POLL_SECONDS
    battery_charge_threshold: int = NUT_BATTERY_CHARGE_THRESHOLD
    load_threshold: int = NUT_LOAD_THRESHOLD
    config_path: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NutConfig":
        return cls._read(_Section(data, "nut"))

    @classmethod
    def _read(cls, sec: "_Section") -> "NutConfig":
        thresholds = sec.section("thresholds")
        host = sec.get_str("host", "")
        return cls(
            enabled=sec.get_bool("enabled", True),
            host="" if host == PLACEHOLDER_UNRAID_HOST else host,
            port=sec.get_int("port", NUT_DEFAULT_PORT, lo=1, hi=65535),
            ups_name=sec.get_str("ups_name", ""),
            poll_seconds=sec.get_int("poll_seconds", NUT_POLL_SECONDS, lo=10),
            battery_charge_threshold=thresholds.get_int("battery_charge", NUT_BATTERY_CHARGE_THRESHOLD, lo=1, hi=100),
            load_threshold=thresholds.get_int("load", NUT_LOAD_THRESHOLD, lo=1, hi=200),
        )

    def resolve_host(self, unraid_host: str = "") -> str:
        """Pick the NUT host to talk to.

        An explicit nut.host always wins. Otherwise fall back to the Unraid
        server, since the Unraid NUT plugin runs upsd on that box, and the bot
        usually sits in a container that cannot see it as localhost.
        """
        return self.host or (unraid_host or "").strip()

    def set_enabled(self, enabled: bool) -> None:
        """Toggle UPS monitoring and persist to config.yaml.

        Takes effect after a restart: the monitor is only built at startup.
        """
        self.enabled = enabled
        self._persist()

    def _persist(self) -> None:
        """Write current NUT settings back to config.yaml."""
        def _update(data: dict[str, Any]) -> None:
            section = config_section(data, "nut")
            section["enabled"] = self.enabled
            if self.host:
                section["host"] = self.host
            section["port"] = self.port
            if self.ups_name:
                section["ups_name"] = self.ups_name
            section["poll_seconds"] = self.poll_seconds
            thresholds = config_section(section, "thresholds")
            thresholds["battery_charge"] = self.battery_charge_threshold
            thresholds["load"] = self.load_threshold

        update_yaml_config(self.config_path, _update)


def _read_log_watching(sec: _Section) -> dict[str, Any]:
    """log_watching merged over DEFAULT_LOG_WATCHING, key by key."""
    present = set(sec._data)
    config: dict[str, Any] = {
        "containers": sec.str_list("containers") if "containers" in present
        else list(DEFAULT_WATCHED_CONTAINERS),
        "error_patterns": sec.str_list("error_patterns") if "error_patterns" in present
        else list(DEFAULT_ERROR_PATTERNS),
        "ignore_patterns": sec.str_list("ignore_patterns") if "ignore_patterns" in present
        else list(DEFAULT_IGNORE_PATTERNS),
        "cooldown_seconds": sec.get_int("cooldown_seconds", DEFAULT_COOLDOWN_SECONDS, lo=1),
        "container_ignores": {},
    }
    for name, patterns in sec.mapping("container_ignores").items():
        if patterns is None:
            continue
        if not isinstance(patterns, list):
            raise ConfigError(
                f"log_watching.container_ignores.{name}: expected a list, got {type(patterns).__name__}"
            )
        config["container_ignores"][str(name)] = [str(p) for p in patterns]
    return config


class AppConfig:
    """Application configuration combining Settings (env) and YAML config."""

    def __init__(self, settings: Settings):
        """Initialize AppConfig with Settings and load YAML config.

        Args:
            settings: Pydantic Settings instance with environment variables.
        """
        self._settings = settings
        self._yaml_config = load_yaml_config(settings.config_path)
        if not isinstance(self._yaml_config, dict):
            raise ConfigError(
                f"{settings.config_path}: expected a mapping at the top level, "
                f"got {type(self._yaml_config).__name__}"
            )
        root = _Section(self._yaml_config, "")

        # Cache config objects once (config is read-only after startup)
        self._ai = AIConfig._read(root.section("ai"))

        # Environment variable DEFAULT_MODEL overrides any YAML/code default
        if settings.default_model:
            self._ai.default_model = settings.default_model

        # Environment variable OLLAMA_HOST overrides any YAML/code default
        if settings.ollama_host:
            self._ai.ollama_host = settings.ollama_host

        self._bot_config = BotConfig._read(root.section("bot"))
        self._docker = DockerConfig._read(root.section("docker"))
        self._resource_monitoring = ResourceConfig._read(root.section("resource_monitoring"))
        self._resource_monitoring.config_path = settings.config_path
        self._unraid = UnraidConfig._read(root.section("unraid"))
        self._unraid.config_path = settings.config_path
        self._memory_management = MemoryConfig._read(root.section("memory_management"))
        self._memory_management.config_path = settings.config_path
        self._image_updates = ImageUpdatesConfig._read(root.section("image_updates"))
        self._image_updates.config_path = settings.config_path
        self._auto_heal = AutoHealConfig._read(root.section("auto_heal"))
        self._auto_heal.config_path = settings.config_path
        self._nut = NutConfig._read(root.section("nut"))
        self._nut.config_path = settings.config_path

        self._ignored = root.str_list("ignored_containers")
        self._protected = root.str_list("protected_containers")
        self._log_watching = _read_log_watching(root.section("log_watching"))
        root.warn_unknown()

    @property
    def ignored_containers(self) -> list[str]:
        """Get list of container names to ignore."""
        return self._ignored

    @property
    def protected_containers(self) -> list[str]:
        """Get list of containers that cannot be controlled via Telegram."""
        return self._protected

    @property
    def log_watching(self) -> dict[str, Any]:
        """Get log watching configuration.

        Each key present in YAML overrides its default; missing keys (or a
        bare ``log_watching:``) keep the defaults.
        """
        return self._log_watching

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
    def nut(self) -> NutConfig:
        """Get NUT / UPS configuration."""
        return self._nut

    @property
    def memory_management(self) -> MemoryConfig:
        """Get memory management configuration."""
        return self._memory_management

    @property
    def image_updates(self) -> ImageUpdatesConfig:
        """Get image-update detection configuration."""
        return self._image_updates

    @property
    def auto_heal(self) -> AutoHealConfig:
        """Get auto-heal configuration."""
        return self._auto_heal


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
        unraid_verify_ssl: bool = True,
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
            unraid_verify_ssl=unraid_verify_ssl,
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
        unraid_verify_ssl: bool = True,
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
                unraid_verify_ssl=unraid_verify_ssl,
                watched_containers=watched_containers,
                protected_containers=protected_containers,
                ignored_containers=ignored_containers,
                priority_containers=priority_containers,
                killable_containers=killable_containers,
            )
            return

        # Container roles (always overwritten by wizard)
        config_section(existing, "log_watching")["containers"] = watched_containers
        existing["protected_containers"] = protected_containers
        existing["ignored_containers"] = ignored_containers
        config_section(existing, "memory_management")["priority_containers"] = priority_containers
        config_section(existing, "memory_management")["killable_containers"] = killable_containers

        # Unraid connection (only update if user entered new details)
        if not skip_unraid:
            unraid_section = config_section(existing, "unraid")
            unraid_enabled = unraid_host is not None
            unraid_section["enabled"] = unraid_enabled
            if unraid_enabled:
                unraid_section["host"] = unraid_host
            unraid_section["port"] = unraid_port
            unraid_section["use_ssl"] = unraid_use_ssl
            unraid_section["verify_ssl"] = unraid_verify_ssl

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
        unraid_verify_ssl: bool = True,
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
                    "pattern_analyzer": DEFAULT_HAIKU_MODEL,
                    "nl_processor": DEFAULT_SONNET_MODEL,
                    "diagnostic": DEFAULT_HAIKU_MODEL,
                },
                "max_tokens": {
                    "pattern_analyzer": PATTERN_ANALYZER_MAX_TOKENS,
                    "nl_processor": NL_PROCESSOR_MAX_TOKENS,
                    "diagnostic_brief": DIAGNOSTIC_BRIEF_MAX_TOKENS,
                    "diagnostic_detail": DIAGNOSTIC_DETAIL_MAX_TOKENS,
                },
                "nl_processor": {
                    "max_tool_iterations": NL_MAX_TOOL_ITERATIONS,
                    "max_conversation_exchanges": NL_MAX_CONVERSATION_EXCHANGES,
                },
                "pattern_analyzer_context_lines": PATTERN_ANALYZER_CONTEXT_LINES,
                "diagnostic_context_expiry_seconds": DIAGNOSTIC_CONTEXT_EXPIRY_SECONDS,
            },
            "bot": {
                "log_display": {
                    "max_lines": LOG_MAX_LINES,
                    "max_chars": LOG_MAX_CHARS,
                    "nl_max_chars": NL_LOG_MAX_CHARS,
                    "diagnose_max_lines": DIAGNOSE_MAX_LINES,
                },
                "error_display_max_chars": ERROR_DISPLAY_MAX_CHARS,
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
                "cooldown_seconds": DEFAULT_COOLDOWN_SECONDS,
            },
            "resource_monitoring": {
                "enabled": True,
                "poll_interval_seconds": RESOURCE_POLL_INTERVAL_SECONDS,
                "sustained_threshold_seconds": RESOURCE_SUSTAINED_THRESHOLD_SECONDS,
                "defaults": {
                    "cpu_percent": DEFAULT_CPU_PERCENT,
                    "memory_percent": DEFAULT_MEMORY_PERCENT,
                },
                "containers": {},
            },
            "memory_management": {
                "enabled": bool(killable_containers),
                "warning_threshold": MEMORY_WARNING_THRESHOLD,
                "critical_threshold": MEMORY_CRITICAL_THRESHOLD,
                "safe_threshold": MEMORY_SAFE_THRESHOLD,
                "kill_delay_seconds": MEMORY_KILL_DELAY_SECONDS,
                "stabilization_wait": MEMORY_STABILIZATION_WAIT,
                "priority_containers": priority_containers,
                "killable_containers": killable_containers,
            },
            "unraid": {
                "enabled": unraid_enabled,
                "host": unraid_host or "your-unraid-ip",
                "port": unraid_port,
                "use_ssl": unraid_use_ssl,
                "verify_ssl": unraid_verify_ssl,
                "polling": {
                    "system": UNRAID_POLL_SYSTEM_SECONDS,
                    "array": UNRAID_POLL_ARRAY_SECONDS,
                },
                "thresholds": {
                    "cpu_temp": UNRAID_CPU_TEMP_THRESHOLD,
                    "cpu_usage": UNRAID_CPU_USAGE_THRESHOLD,
                    "memory_usage": UNRAID_MEMORY_USAGE_THRESHOLD,
                    "disk_temp": UNRAID_DISK_TEMP_THRESHOLD,
                    "array_usage": UNRAID_ARRAY_USAGE_THRESHOLD,
                },
            },
        }

    def _write_yaml(self, config: dict[str, Any]) -> None:
        """Write config dict to the YAML file atomically."""
        atomic_yaml_write(config, self._path)
