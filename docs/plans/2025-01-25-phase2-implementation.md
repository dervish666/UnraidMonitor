# Phase 2 Implementation Plan: Alerts & Log Watching

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add automatic crash alerts and log error monitoring with rate-limited Telegram notifications.

**Architecture:** Extend DockerEventMonitor to trigger alerts on crashes. Add LogWatcher for error pattern matching. AlertManager handles Telegram notifications with RateLimiter for spam prevention.

**Tech Stack:** Python 3.11+, aiogram 3.x, docker SDK, pyyaml

---

## Task 1: Extended Configuration Model

**Files:**
- Modify: `src/config.py`
- Modify: `config/config.yaml`
- Create: `tests/test_config_extended.py`

**Step 1: Write the failing test**

Create `tests/test_config_extended.py`:

```python
import pytest
from unittest.mock import patch
import yaml


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
    with patch.dict("os.environ", {
        "TELEGRAM_BOT_TOKEN": "test-token",
        "TELEGRAM_ALLOWED_USERS": "123",
    }, clear=True):
        with patch("builtins.open", create=True) as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = yaml_content
            with patch("os.path.exists", return_value=True):
                from src.config import Settings, load_yaml_config

                yaml_config = load_yaml_config("config/config.yaml")

                assert yaml_config["ignored_containers"] == ["Kometa", "test-container"]
                assert yaml_config["log_watching"]["containers"] == ["plex", "radarr"]
                assert yaml_config["log_watching"]["cooldown_seconds"] == 900


def test_config_uses_defaults_when_no_yaml():
    """Test that sensible defaults are used when YAML is missing."""
    with patch.dict("os.environ", {
        "TELEGRAM_BOT_TOKEN": "test-token",
        "TELEGRAM_ALLOWED_USERS": "123",
    }, clear=True):
        with patch("os.path.exists", return_value=False):
            from src.config import load_yaml_config, DEFAULT_LOG_WATCHING

            yaml_config = load_yaml_config("config/config.yaml")

            assert yaml_config.get("ignored_containers", []) == []
            # Defaults should be used
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_config_extended.py -v`
Expected: FAIL with "cannot import name 'load_yaml_config'"

**Step 3: Update implementation**

Update `src/config.py`:

```python
from typing import Any
import os

import yaml
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# Default watched containers
DEFAULT_WATCHED_CONTAINERS = [
    "plex", "radarr", "sonarr", "lidarr", "readarr", "prowlarr",
    "qbit", "sab", "tautulli", "overseerr",
    "mariadb", "postgresql14", "redis",
    "Brisbooks",
]

# Default error patterns (case-insensitive matching)
DEFAULT_ERROR_PATTERNS = [
    "error", "exception", "fatal", "failed", "critical", "panic", "traceback"
]

# Default ignore patterns
DEFAULT_IGNORE_PATTERNS = [
    "DeprecationWarning", "DEBUG"
]

# Default log watching config
DEFAULT_LOG_WATCHING = {
    "containers": DEFAULT_WATCHED_CONTAINERS,
    "error_patterns": DEFAULT_ERROR_PATTERNS,
    "ignore_patterns": DEFAULT_IGNORE_PATTERNS,
    "cooldown_seconds": 900,  # 15 minutes
}


def load_yaml_config(path: str) -> dict[str, Any]:
    """Load YAML config file, return empty dict if not found."""
    if not os.path.exists(path):
        return {}

    with open(path, "r") as f:
        content = yaml.safe_load(f)
        return content if content else {}


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
    """Combined application configuration from env and YAML."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._yaml_config = load_yaml_config(settings.config_path)

    @property
    def ignored_containers(self) -> list[str]:
        return self._yaml_config.get("ignored_containers", [])

    @property
    def log_watching(self) -> dict[str, Any]:
        yaml_log = self._yaml_config.get("log_watching", {})
        return {
            "containers": yaml_log.get("containers", DEFAULT_WATCHED_CONTAINERS),
            "error_patterns": yaml_log.get("error_patterns", DEFAULT_ERROR_PATTERNS),
            "ignore_patterns": yaml_log.get("ignore_patterns", DEFAULT_IGNORE_PATTERNS),
            "cooldown_seconds": yaml_log.get("cooldown_seconds", 900),
        }
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_config_extended.py -v`
Expected: PASS

**Step 5: Update config.yaml**

Update `config/config.yaml`:

```yaml
# Unraid Monitor Bot Configuration

# Containers to ignore (won't trigger crash alerts)
ignored_containers:
  - Kometa

# Log watching configuration
log_watching:
  # Containers to watch for errors (comment out to use defaults)
  containers:
    - plex
    - radarr
    - sonarr
    - lidarr
    - readarr
    - prowlarr
    - qbit
    - sab
    - tautulli
    - overseerr
    - mariadb
    - postgresql14
    - redis
    - Brisbooks

  # Error patterns to match (case-insensitive)
  error_patterns:
    - "error"
    - "exception"
    - "fatal"
    - "failed"
    - "critical"
    - "panic"
    - "traceback"

  # Patterns to ignore
  ignore_patterns:
    - "DeprecationWarning"
    - "DEBUG"

  # Cooldown between alerts per container (seconds)
  cooldown_seconds: 900
```

**Step 6: Commit**

```bash
git add src/config.py config/config.yaml tests/test_config_extended.py
git commit -m "feat: add YAML config loading with log watching settings"
```

---

## Task 2: Rate Limiter

**Files:**
- Create: `src/alerts/__init__.py`
- Create: `src/alerts/rate_limiter.py`
- Create: `tests/test_rate_limiter.py`

**Step 1: Write the failing test**

Create `tests/test_rate_limiter.py`:

```python
import pytest
from datetime import datetime, timedelta


def test_rate_limiter_allows_first_event():
    from src.alerts.rate_limiter import RateLimiter

    limiter = RateLimiter(cooldown_seconds=900)

    assert limiter.should_alert("radarr") is True


def test_rate_limiter_blocks_during_cooldown():
    from src.alerts.rate_limiter import RateLimiter

    limiter = RateLimiter(cooldown_seconds=900)

    limiter.record_alert("radarr")

    assert limiter.should_alert("radarr") is False


def test_rate_limiter_allows_after_cooldown():
    from src.alerts.rate_limiter import RateLimiter

    limiter = RateLimiter(cooldown_seconds=900)

    # Simulate alert 20 minutes ago
    limiter._last_alert["radarr"] = datetime.now() - timedelta(minutes=20)

    assert limiter.should_alert("radarr") is True


def test_rate_limiter_tracks_containers_independently():
    from src.alerts.rate_limiter import RateLimiter

    limiter = RateLimiter(cooldown_seconds=900)

    limiter.record_alert("radarr")

    assert limiter.should_alert("radarr") is False
    assert limiter.should_alert("sonarr") is True


def test_rate_limiter_records_suppressed_count():
    from src.alerts.rate_limiter import RateLimiter

    limiter = RateLimiter(cooldown_seconds=900)

    limiter.record_alert("radarr")
    limiter.record_suppressed("radarr")
    limiter.record_suppressed("radarr")

    assert limiter.get_suppressed_count("radarr") == 2


def test_rate_limiter_resets_suppressed_on_alert():
    from src.alerts.rate_limiter import RateLimiter

    limiter = RateLimiter(cooldown_seconds=900)

    limiter.record_alert("radarr")
    limiter.record_suppressed("radarr")
    limiter.record_suppressed("radarr")

    # Simulate cooldown expired
    limiter._last_alert["radarr"] = datetime.now() - timedelta(minutes=20)

    # This should reset the suppressed count
    limiter.record_alert("radarr")

    assert limiter.get_suppressed_count("radarr") == 0
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_rate_limiter.py -v`
Expected: FAIL with "No module named 'src.alerts'"

**Step 3: Write minimal implementation**

Create `src/alerts/__init__.py`:

```python
# Alerts module
```

Create `src/alerts/rate_limiter.py`:

```python
from datetime import datetime, timedelta


class RateLimiter:
    """Rate limiter to prevent alert spam."""

    def __init__(self, cooldown_seconds: int = 900):
        self.cooldown_seconds = cooldown_seconds
        self._last_alert: dict[str, datetime] = {}
        self._suppressed_count: dict[str, int] = {}

    def should_alert(self, container_name: str) -> bool:
        """Check if an alert should be sent for this container."""
        last = self._last_alert.get(container_name)
        if last is None:
            return True

        elapsed = datetime.now() - last
        return elapsed >= timedelta(seconds=self.cooldown_seconds)

    def record_alert(self, container_name: str) -> None:
        """Record that an alert was sent."""
        self._last_alert[container_name] = datetime.now()
        self._suppressed_count[container_name] = 0

    def record_suppressed(self, container_name: str) -> None:
        """Record that an alert was suppressed."""
        current = self._suppressed_count.get(container_name, 0)
        self._suppressed_count[container_name] = current + 1

    def get_suppressed_count(self, container_name: str) -> int:
        """Get count of suppressed alerts since last sent alert."""
        return self._suppressed_count.get(container_name, 0)
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_rate_limiter.py -v`
Expected: All 6 tests PASS

**Step 5: Commit**

```bash
git add src/alerts/__init__.py src/alerts/rate_limiter.py tests/test_rate_limiter.py
git commit -m "feat: add rate limiter for alert spam prevention"
```

---

## Task 3: Alert Manager

**Files:**
- Create: `src/alerts/manager.py`
- Create: `tests/test_alert_manager.py`

**Step 1: Write the failing test**

Create `tests/test_alert_manager.py`:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_alert_manager_sends_crash_alert():
    from src.alerts.manager import AlertManager

    bot = MagicMock()
    bot.send_message = AsyncMock()

    manager = AlertManager(bot=bot, chat_id=12345)

    await manager.send_crash_alert(
        container_name="radarr",
        exit_code=137,
        image="linuxserver/radarr:latest",
        uptime_seconds=9240,  # 2h 34m
    )

    bot.send_message.assert_called_once()
    call_args = bot.send_message.call_args
    assert call_args[1]["chat_id"] == 12345
    assert "CRASHED" in call_args[1]["text"]
    assert "radarr" in call_args[1]["text"]
    assert "137" in call_args[1]["text"]


@pytest.mark.asyncio
async def test_alert_manager_sends_log_error_alert():
    from src.alerts.manager import AlertManager

    bot = MagicMock()
    bot.send_message = AsyncMock()

    manager = AlertManager(bot=bot, chat_id=12345)

    await manager.send_log_error_alert(
        container_name="radarr",
        error_line="Database connection failed: timeout",
        suppressed_count=0,
    )

    bot.send_message.assert_called_once()
    call_args = bot.send_message.call_args
    assert "ERRORS" in call_args[1]["text"]
    assert "radarr" in call_args[1]["text"]
    assert "Database connection failed" in call_args[1]["text"]


@pytest.mark.asyncio
async def test_alert_manager_includes_suppressed_count():
    from src.alerts.manager import AlertManager

    bot = MagicMock()
    bot.send_message = AsyncMock()

    manager = AlertManager(bot=bot, chat_id=12345)

    await manager.send_log_error_alert(
        container_name="radarr",
        error_line="Latest error",
        suppressed_count=5,
    )

    call_args = bot.send_message.call_args
    assert "6 errors" in call_args[1]["text"]  # 5 suppressed + 1 current


@pytest.mark.asyncio
async def test_alert_manager_formats_uptime():
    from src.alerts.manager import format_uptime

    assert format_uptime(3661) == "1h 1m"
    assert format_uptime(120) == "2m"
    assert format_uptime(3600) == "1h 0m"
    assert format_uptime(86400) == "24h 0m"
    assert format_uptime(45) == "0m"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_alert_manager.py -v`
Expected: FAIL with "cannot import name 'AlertManager'"

**Step 3: Write minimal implementation**

Create `src/alerts/manager.py`:

```python
import logging
from aiogram import Bot

logger = logging.getLogger(__name__)


def format_uptime(seconds: int) -> str:
    """Format uptime in human-readable form."""
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60

    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


class AlertManager:
    """Manages sending alerts to Telegram."""

    def __init__(self, bot: Bot, chat_id: int):
        self.bot = bot
        self.chat_id = chat_id

    async def send_crash_alert(
        self,
        container_name: str,
        exit_code: int,
        image: str,
        uptime_seconds: int | None = None,
    ) -> None:
        """Send a container crash alert."""
        uptime_str = format_uptime(uptime_seconds) if uptime_seconds else "unknown"

        # Interpret common exit codes
        exit_reason = ""
        if exit_code == 137:
            exit_reason = " (OOM killed)"
        elif exit_code == 143:
            exit_reason = " (SIGTERM)"
        elif exit_code == 139:
            exit_reason = " (segfault)"

        text = f"""🔴 *CONTAINER CRASHED:* {container_name}

Exit code: {exit_code}{exit_reason}
Image: `{image}`
Uptime: {uptime_str}

/status {container_name} - View details
/logs {container_name} - View recent logs"""

        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=text,
                parse_mode="Markdown",
            )
            logger.info(f"Sent crash alert for {container_name}")
        except Exception as e:
            logger.error(f"Failed to send crash alert: {e}")

    async def send_log_error_alert(
        self,
        container_name: str,
        error_line: str,
        suppressed_count: int = 0,
    ) -> None:
        """Send a log error alert."""
        total_errors = suppressed_count + 1

        # Truncate long error lines
        if len(error_line) > 200:
            error_line = error_line[:200] + "..."

        if total_errors > 1:
            count_text = f"Found {total_errors} errors in the last 15 minutes"
        else:
            count_text = "New error detected"

        text = f"""⚠️ *ERRORS IN:* {container_name}

{count_text}

Latest: `{error_line}`

/logs {container_name} 50 - View last 50 lines"""

        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=text,
                parse_mode="Markdown",
            )
            logger.info(f"Sent log error alert for {container_name}")
        except Exception as e:
            logger.error(f"Failed to send log error alert: {e}")
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_alert_manager.py -v`
Expected: All 4 tests PASS

**Step 5: Commit**

```bash
git add src/alerts/manager.py tests/test_alert_manager.py
git commit -m "feat: add alert manager for Telegram notifications"
```

---

## Task 4: Chat ID Storage

**Files:**
- Modify: `src/alerts/manager.py`
- Modify: `src/bot/telegram_bot.py`
- Modify: `tests/test_alert_manager.py`

**Step 1: Write the failing test**

Add to `tests/test_alert_manager.py`:

```python
@pytest.mark.asyncio
async def test_chat_id_store_saves_and_retrieves():
    from src.alerts.manager import ChatIdStore

    store = ChatIdStore()

    store.set_chat_id(12345)

    assert store.get_chat_id() == 12345


@pytest.mark.asyncio
async def test_chat_id_store_returns_none_when_not_set():
    from src.alerts.manager import ChatIdStore

    store = ChatIdStore()

    assert store.get_chat_id() is None
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_alert_manager.py::test_chat_id_store_saves_and_retrieves -v`
Expected: FAIL with "cannot import name 'ChatIdStore'"

**Step 3: Update implementation**

Add to `src/alerts/manager.py` (at the top, before AlertManager):

```python
class ChatIdStore:
    """Simple in-memory storage for the alert chat ID."""

    def __init__(self):
        self._chat_id: int | None = None

    def set_chat_id(self, chat_id: int) -> None:
        """Store the chat ID."""
        self._chat_id = chat_id

    def get_chat_id(self) -> int | None:
        """Get the stored chat ID."""
        return self._chat_id
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_alert_manager.py -v`
Expected: All 6 tests PASS

**Step 5: Update telegram_bot.py to capture chat ID**

Modify `src/bot/telegram_bot.py` - add chat_id_store parameter to middleware:

```python
import logging
from typing import Any, Awaitable, Callable

from aiogram import Bot, Dispatcher, BaseMiddleware
from aiogram.filters import Command
from aiogram.types import Message

from src.state import ContainerStateManager
from src.bot.commands import help_command, status_command

logger = logging.getLogger(__name__)


class AuthMiddleware(BaseMiddleware):
    def __init__(self, allowed_users: list[int], chat_id_store=None):
        self.allowed_users = set(allowed_users)
        self.chat_id_store = chat_id_store
        super().__init__()

    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        user_id = event.from_user.id if event.from_user else None

        if user_id not in self.allowed_users:
            logger.warning(f"Unauthorized access attempt from user {user_id}")
            return None

        # Store chat ID for alerts
        if self.chat_id_store is not None and event.chat:
            self.chat_id_store.set_chat_id(event.chat.id)

        return await handler(event, data)


def create_auth_middleware(allowed_users: list[int], chat_id_store=None) -> AuthMiddleware:
    """Factory function for auth middleware."""
    return AuthMiddleware(allowed_users, chat_id_store)


def create_bot(token: str) -> Bot:
    """Create Telegram bot instance."""
    return Bot(token=token)


def create_dispatcher(allowed_users: list[int], chat_id_store=None) -> Dispatcher:
    """Create dispatcher with auth middleware."""
    dp = Dispatcher()
    dp.message.middleware(AuthMiddleware(allowed_users, chat_id_store))
    return dp


def register_commands(dp: Dispatcher, state: ContainerStateManager) -> None:
    """Register all command handlers."""
    dp.message.register(help_command(state), Command("help"))
    dp.message.register(status_command(state), Command("status"))
```

**Step 6: Run all tests**

Run: `pytest -v`
Expected: All tests PASS

**Step 7: Commit**

```bash
git add src/alerts/manager.py src/bot/telegram_bot.py tests/test_alert_manager.py
git commit -m "feat: add chat ID storage for alert delivery"
```

---

## Task 5: Crash Alert Integration

**Files:**
- Modify: `src/monitors/docker_events.py`
- Create: `tests/test_crash_alerts.py`

**Step 1: Write the failing test**

Create `tests/test_crash_alerts.py`:

```python
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime, timezone


@pytest.mark.asyncio
async def test_docker_monitor_triggers_crash_alert():
    from src.monitors.docker_events import DockerEventMonitor
    from src.state import ContainerStateManager
    from src.alerts.manager import AlertManager, ChatIdStore
    from src.alerts.rate_limiter import RateLimiter

    state = ContainerStateManager()
    chat_store = ChatIdStore()
    chat_store.set_chat_id(12345)

    bot = MagicMock()
    bot.send_message = AsyncMock()

    alert_manager = AlertManager(bot=bot, chat_id=12345)
    rate_limiter = RateLimiter(cooldown_seconds=900)

    monitor = DockerEventMonitor(
        state_manager=state,
        ignored_containers=[],
        alert_manager=alert_manager,
        rate_limiter=rate_limiter,
    )

    # Simulate a crash event
    event = {
        "Action": "die",
        "Actor": {
            "Attributes": {
                "name": "radarr",
                "exitCode": "137",
            }
        },
    }

    await monitor._handle_crash_event(event)

    bot.send_message.assert_called_once()
    assert "CRASHED" in bot.send_message.call_args[1]["text"]


@pytest.mark.asyncio
async def test_docker_monitor_ignores_normal_stop():
    from src.monitors.docker_events import DockerEventMonitor
    from src.state import ContainerStateManager
    from src.alerts.manager import AlertManager, ChatIdStore
    from src.alerts.rate_limiter import RateLimiter

    state = ContainerStateManager()
    bot = MagicMock()
    bot.send_message = AsyncMock()

    alert_manager = AlertManager(bot=bot, chat_id=12345)
    rate_limiter = RateLimiter(cooldown_seconds=900)

    monitor = DockerEventMonitor(
        state_manager=state,
        ignored_containers=[],
        alert_manager=alert_manager,
        rate_limiter=rate_limiter,
    )

    # Simulate a normal stop (exit code 0)
    event = {
        "Action": "die",
        "Actor": {
            "Attributes": {
                "name": "radarr",
                "exitCode": "0",
            }
        },
    }

    await monitor._handle_crash_event(event)

    bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_docker_monitor_respects_ignored_containers():
    from src.monitors.docker_events import DockerEventMonitor
    from src.state import ContainerStateManager
    from src.alerts.manager import AlertManager
    from src.alerts.rate_limiter import RateLimiter

    state = ContainerStateManager()
    bot = MagicMock()
    bot.send_message = AsyncMock()

    alert_manager = AlertManager(bot=bot, chat_id=12345)
    rate_limiter = RateLimiter(cooldown_seconds=900)

    monitor = DockerEventMonitor(
        state_manager=state,
        ignored_containers=["Kometa"],
        alert_manager=alert_manager,
        rate_limiter=rate_limiter,
    )

    event = {
        "Action": "die",
        "Actor": {
            "Attributes": {
                "name": "Kometa",
                "exitCode": "1",
            }
        },
    }

    await monitor._handle_crash_event(event)

    bot.send_message.assert_not_called()
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_crash_alerts.py -v`
Expected: FAIL with TypeError about unexpected keyword argument 'alert_manager'

**Step 3: Update implementation**

Update `src/monitors/docker_events.py`:

```python
import asyncio
import logging
from datetime import datetime
from typing import Any, Callable, Awaitable

import docker
from docker.models.containers import Container

from src.models import ContainerInfo
from src.state import ContainerStateManager

logger = logging.getLogger(__name__)


def parse_container(container: Container) -> ContainerInfo:
    """Convert Docker SDK container to ContainerInfo."""
    # Get image name
    tags = container.image.tags
    image = tags[0] if tags else container.image.id

    # Get health status if available
    state = container.attrs.get("State", {})
    health_info = state.get("Health")
    health = health_info.get("Status") if health_info else None

    # Parse started_at timestamp
    started_at_str = state.get("StartedAt")
    started_at = None
    if started_at_str and not started_at_str.startswith("0001"):
        try:
            # Remove nanoseconds and parse
            clean_ts = started_at_str.split(".")[0] + "Z"
            started_at = datetime.fromisoformat(clean_ts.replace("Z", "+00:00"))
        except (ValueError, IndexError):
            pass

    return ContainerInfo(
        name=container.name,
        status=container.status,
        health=health,
        image=image,
        started_at=started_at,
    )


class DockerEventMonitor:
    def __init__(
        self,
        state_manager: ContainerStateManager,
        ignored_containers: list[str] | None = None,
        alert_manager=None,
        rate_limiter=None,
    ):
        self.state_manager = state_manager
        self.ignored_containers = set(ignored_containers or [])
        self.alert_manager = alert_manager
        self.rate_limiter = rate_limiter
        self._client: docker.DockerClient | None = None
        self._running = False
        self._pending_alerts: asyncio.Queue = asyncio.Queue()

    def connect(self) -> None:
        """Connect to Docker socket."""
        self._client = docker.DockerClient(base_url="unix:///var/run/docker.sock")
        logger.info("Connected to Docker socket")

    def load_initial_state(self) -> None:
        """Load all containers into state manager."""
        if not self._client:
            raise RuntimeError("Not connected to Docker")

        containers = self._client.containers.list(all=True)
        for container in containers:
            if container.name not in self.ignored_containers:
                info = parse_container(container)
                self.state_manager.update(info)

        logger.info(f"Loaded {len(containers)} containers into state")

    async def start(self) -> None:
        """Start monitoring Docker events."""
        if not self._client:
            raise RuntimeError("Not connected to Docker")

        self._running = True
        logger.info("Starting Docker event monitor")

        # Start alert processor
        alert_task = asyncio.create_task(self._process_alerts())

        # Run blocking event loop in thread
        try:
            await asyncio.to_thread(self._event_loop)
        finally:
            alert_task.cancel()

    def stop(self) -> None:
        """Stop monitoring."""
        self._running = False
        logger.info("Stopping Docker event monitor")

    async def _process_alerts(self) -> None:
        """Process pending alerts from the queue."""
        while self._running:
            try:
                event = await asyncio.wait_for(
                    self._pending_alerts.get(),
                    timeout=1.0
                )
                await self._handle_crash_event(event)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error processing alert: {e}")

    def _event_loop(self) -> None:
        """Blocking event loop - runs in thread."""
        if not self._client:
            return

        for event in self._client.events(decode=True, filters={"type": "container"}):
            if not self._running:
                break

            action = event.get("Action", "")
            container_name = event.get("Actor", {}).get("Attributes", {}).get("name", "")

            if container_name in self.ignored_containers:
                continue

            if action in ("start", "die", "health_status"):
                self._handle_event(event)

            # Queue die events for crash alert processing
            if action == "die":
                try:
                    self._pending_alerts.put_nowait(event)
                except asyncio.QueueFull:
                    logger.warning("Alert queue full, dropping event")

    def _handle_event(self, event: dict[str, Any]) -> None:
        """Handle a Docker event."""
        if not self._client:
            return

        container_name = event.get("Actor", {}).get("Attributes", {}).get("name", "")
        action = event.get("Action", "")

        logger.info(f"Docker event: {action} for {container_name}")

        try:
            container = self._client.containers.get(container_name)
            info = parse_container(container)
            self.state_manager.update(info)
        except docker.errors.NotFound:
            logger.warning(f"Container {container_name} not found after event")
        except Exception as e:
            logger.error(f"Error handling event for {container_name}: {e}")

    async def _handle_crash_event(self, event: dict[str, Any]) -> None:
        """Handle a container crash and send alert if needed."""
        attrs = event.get("Actor", {}).get("Attributes", {})
        container_name = attrs.get("name", "")
        exit_code_str = attrs.get("exitCode", "0")

        try:
            exit_code = int(exit_code_str)
        except ValueError:
            exit_code = 0

        # Skip normal stops
        if exit_code == 0:
            return

        # Skip ignored containers
        if container_name in self.ignored_containers:
            return

        # Check if we have alert manager
        if self.alert_manager is None:
            logger.warning("No alert manager configured, skipping crash alert")
            return

        # Get container info for additional details
        container_info = self.state_manager.get(container_name)
        image = container_info.image if container_info else "unknown"

        # Calculate uptime if we have started_at
        uptime_seconds = None
        if container_info and container_info.started_at:
            uptime = datetime.now(container_info.started_at.tzinfo) - container_info.started_at
            uptime_seconds = int(uptime.total_seconds())

        await self.alert_manager.send_crash_alert(
            container_name=container_name,
            exit_code=exit_code,
            image=image,
            uptime_seconds=uptime_seconds,
        )
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_crash_alerts.py -v`
Expected: All 3 tests PASS

**Step 5: Run all tests**

Run: `pytest -v`
Expected: All tests PASS

**Step 6: Commit**

```bash
git add src/monitors/docker_events.py tests/test_crash_alerts.py
git commit -m "feat: add crash alert integration to Docker monitor"
```

---

## Task 6: Log Watcher

**Files:**
- Create: `src/monitors/log_watcher.py`
- Create: `tests/test_log_watcher.py`

**Step 1: Write the failing test**

Create `tests/test_log_watcher.py`:

```python
import pytest
from unittest.mock import MagicMock, AsyncMock
import re


def test_log_watcher_matches_error_patterns():
    from src.monitors.log_watcher import matches_error_pattern

    error_patterns = ["error", "exception", "fatal"]
    ignore_patterns = ["DEBUG"]

    assert matches_error_pattern("Something error happened", error_patterns, ignore_patterns) is True
    assert matches_error_pattern("FATAL: Cannot connect", error_patterns, ignore_patterns) is True
    assert matches_error_pattern("All good here", error_patterns, ignore_patterns) is False


def test_log_watcher_respects_ignore_patterns():
    from src.monitors.log_watcher import matches_error_pattern

    error_patterns = ["error"]
    ignore_patterns = ["DeprecationWarning", "DEBUG"]

    assert matches_error_pattern("DEBUG: error in test", error_patterns, ignore_patterns) is False
    assert matches_error_pattern("DeprecationWarning: error", error_patterns, ignore_patterns) is False
    assert matches_error_pattern("Real error occurred", error_patterns, ignore_patterns) is True


def test_log_watcher_case_insensitive():
    from src.monitors.log_watcher import matches_error_pattern

    error_patterns = ["error", "fatal"]
    ignore_patterns = []

    assert matches_error_pattern("ERROR: something", error_patterns, ignore_patterns) is True
    assert matches_error_pattern("Error: something", error_patterns, ignore_patterns) is True
    assert matches_error_pattern("FATAL crash", error_patterns, ignore_patterns) is True
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_log_watcher.py -v`
Expected: FAIL with "No module named 'src.monitors.log_watcher'"

**Step 3: Write minimal implementation**

Create `src/monitors/log_watcher.py`:

```python
import asyncio
import logging
import re
from typing import Callable, Awaitable

import docker
from docker.models.containers import Container

logger = logging.getLogger(__name__)


def matches_error_pattern(
    line: str,
    error_patterns: list[str],
    ignore_patterns: list[str],
) -> bool:
    """Check if a log line matches any error pattern and no ignore pattern."""
    line_lower = line.lower()

    # Check ignore patterns first
    for pattern in ignore_patterns:
        if pattern.lower() in line_lower:
            return False

    # Check error patterns
    for pattern in error_patterns:
        if pattern.lower() in line_lower:
            return True

    return False


class LogWatcher:
    """Watch container logs for error patterns."""

    def __init__(
        self,
        containers: list[str],
        error_patterns: list[str],
        ignore_patterns: list[str],
        on_error: Callable[[str, str], Awaitable[None]] | None = None,
    ):
        self.containers = containers
        self.error_patterns = error_patterns
        self.ignore_patterns = ignore_patterns
        self.on_error = on_error
        self._client: docker.DockerClient | None = None
        self._running = False
        self._tasks: list[asyncio.Task] = []

    def connect(self) -> None:
        """Connect to Docker socket."""
        self._client = docker.DockerClient(base_url="unix:///var/run/docker.sock")
        logger.info("LogWatcher connected to Docker socket")

    async def start(self) -> None:
        """Start watching logs for all configured containers."""
        if not self._client:
            raise RuntimeError("Not connected to Docker")

        self._running = True

        # Start a log watcher task for each container
        for container_name in self.containers:
            task = asyncio.create_task(self._watch_container(container_name))
            self._tasks.append(task)

        logger.info(f"Started watching logs for {len(self.containers)} containers")

        # Wait for all tasks
        await asyncio.gather(*self._tasks, return_exceptions=True)

    def stop(self) -> None:
        """Stop watching logs."""
        self._running = False
        for task in self._tasks:
            task.cancel()
        logger.info("LogWatcher stopped")

    async def _watch_container(self, container_name: str) -> None:
        """Watch logs for a single container."""
        while self._running:
            try:
                await self._stream_logs(container_name)
            except docker.errors.NotFound:
                logger.warning(f"Container {container_name} not found, waiting...")
                await asyncio.sleep(30)
            except Exception as e:
                logger.error(f"Error watching {container_name}: {e}")
                await asyncio.sleep(5)

    async def _stream_logs(self, container_name: str) -> None:
        """Stream and process logs from a container."""
        if not self._client:
            return

        container = self._client.containers.get(container_name)

        # Stream logs (blocking, run in thread)
        def stream():
            for line in container.logs(stream=True, follow=True, tail=0):
                if not self._running:
                    break
                yield line.decode("utf-8", errors="replace").strip()

        async def process_lines():
            for line in await asyncio.to_thread(lambda: list(stream())):
                if not self._running:
                    break

                if matches_error_pattern(line, self.error_patterns, self.ignore_patterns):
                    logger.debug(f"Error in {container_name}: {line[:100]}")
                    if self.on_error:
                        await self.on_error(container_name, line)

        await process_lines()
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_log_watcher.py -v`
Expected: All 3 tests PASS

**Step 5: Commit**

```bash
git add src/monitors/log_watcher.py tests/test_log_watcher.py
git commit -m "feat: add log watcher with error pattern matching"
```

---

## Task 7: Log Watcher Alert Integration

**Files:**
- Modify: `src/monitors/log_watcher.py`
- Add to: `tests/test_log_watcher.py`

**Step 1: Write the failing test**

Add to `tests/test_log_watcher.py`:

```python
@pytest.mark.asyncio
async def test_log_watcher_triggers_callback_on_error():
    from src.monitors.log_watcher import LogWatcher, matches_error_pattern

    errors_received = []

    async def on_error(container: str, line: str):
        errors_received.append((container, line))

    watcher = LogWatcher(
        containers=["radarr"],
        error_patterns=["error"],
        ignore_patterns=[],
        on_error=on_error,
    )

    # Simulate processing a line
    line = "Something error happened"
    if matches_error_pattern(line, watcher.error_patterns, watcher.ignore_patterns):
        await watcher.on_error("radarr", line)

    assert len(errors_received) == 1
    assert errors_received[0] == ("radarr", "Something error happened")
```

**Step 2: Run test to verify it passes**

Run: `pytest tests/test_log_watcher.py::test_log_watcher_triggers_callback_on_error -v`
Expected: PASS (already implemented)

**Step 3: Commit**

```bash
git add tests/test_log_watcher.py
git commit -m "test: add callback test for log watcher"
```

---

## Task 8: /logs Command

**Files:**
- Modify: `src/bot/commands.py`
- Modify: `src/bot/telegram_bot.py`
- Add to: `tests/test_commands.py`

**Step 1: Write the failing test**

Add to `tests/test_commands.py`:

```python
@pytest.mark.asyncio
async def test_logs_command_returns_container_logs():
    from src.bot.commands import logs_command
    from src.state import ContainerStateManager
    from src.models import ContainerInfo
    from unittest.mock import MagicMock, AsyncMock, patch

    state = ContainerStateManager()
    state.update(ContainerInfo("radarr", "running", None, "linuxserver/radarr", None))

    # Mock Docker client
    mock_container = MagicMock()
    mock_container.logs.return_value = b"Line 1\nLine 2\nLine 3\n"

    mock_client = MagicMock()
    mock_client.containers.get.return_value = mock_container

    handler = logs_command(state, mock_client)

    message = MagicMock()
    message.text = "/logs radarr"
    message.answer = AsyncMock()

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]
    assert "radarr" in response
    assert "Line 1" in response


@pytest.mark.asyncio
async def test_logs_command_with_line_count():
    from src.bot.commands import logs_command
    from src.state import ContainerStateManager
    from src.models import ContainerInfo
    from unittest.mock import MagicMock, AsyncMock

    state = ContainerStateManager()
    state.update(ContainerInfo("radarr", "running", None, "linuxserver/radarr", None))

    mock_container = MagicMock()
    mock_container.logs.return_value = b"Log output\n"

    mock_client = MagicMock()
    mock_client.containers.get.return_value = mock_container

    handler = logs_command(state, mock_client)

    message = MagicMock()
    message.text = "/logs radarr 50"
    message.answer = AsyncMock()

    await handler(message)

    # Verify logs was called with tail=50
    mock_container.logs.assert_called_once()
    call_kwargs = mock_container.logs.call_args[1]
    assert call_kwargs["tail"] == 50


@pytest.mark.asyncio
async def test_logs_command_container_not_found():
    from src.bot.commands import logs_command
    from src.state import ContainerStateManager
    from unittest.mock import MagicMock, AsyncMock

    state = ContainerStateManager()
    mock_client = MagicMock()

    handler = logs_command(state, mock_client)

    message = MagicMock()
    message.text = "/logs nonexistent"
    message.answer = AsyncMock()

    await handler(message)

    response = message.answer.call_args[0][0]
    assert "not found" in response.lower() or "No container" in response
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_commands.py::test_logs_command_returns_container_logs -v`
Expected: FAIL with "cannot import name 'logs_command'"

**Step 3: Update implementation**

Add to `src/bot/commands.py`:

```python
import docker


def logs_command(
    state: ContainerStateManager,
    docker_client: docker.DockerClient,
) -> Callable[[Message], Awaitable[None]]:
    """Factory for /logs command handler."""
    async def handler(message: Message) -> None:
        text = message.text or ""
        parts = text.strip().split()

        if len(parts) < 2:
            await message.answer("Usage: /logs <container> [lines]\n\nExample: /logs radarr 50")
            return

        container_name = parts[1]

        # Parse optional line count
        try:
            lines = int(parts[2]) if len(parts) > 2 else 20
        except ValueError:
            lines = 20

        # Cap at reasonable limit
        lines = min(lines, 100)

        # Find container
        matches = state.find_by_name(container_name)

        if not matches:
            await message.answer(f"❌ No container found matching '{container_name}'")
            return

        if len(matches) > 1:
            names = ", ".join(m.name for m in matches)
            await message.answer(f"Multiple matches found: {names}\n\n_Be more specific_", parse_mode="Markdown")
            return

        container = matches[0]

        try:
            docker_container = docker_client.containers.get(container.name)
            log_bytes = docker_container.logs(tail=lines, timestamps=False)
            log_text = log_bytes.decode("utf-8", errors="replace")

            # Truncate if too long for Telegram
            if len(log_text) > 4000:
                log_text = log_text[-4000:]
                log_text = "...(truncated)\n" + log_text

            response = f"📋 *Logs: {container.name}* (last {lines} lines)\n\n```\n{log_text}\n```"
            await message.answer(response, parse_mode="Markdown")

        except docker.errors.NotFound:
            await message.answer(f"❌ Container '{container.name}' not found in Docker")
        except Exception as e:
            await message.answer(f"❌ Error getting logs: {e}")

    return handler
```

Update the import at the top of `src/bot/commands.py`:

```python
from typing import Callable, Awaitable

import docker
from aiogram.types import Message

from src.models import ContainerInfo
from src.state import ContainerStateManager
```

**Step 4: Update telegram_bot.py to register /logs**

Update `src/bot/telegram_bot.py`:

```python
import logging
from typing import Any, Awaitable, Callable

import docker
from aiogram import Bot, Dispatcher, BaseMiddleware
from aiogram.filters import Command
from aiogram.types import Message

from src.state import ContainerStateManager
from src.bot.commands import help_command, status_command, logs_command

logger = logging.getLogger(__name__)


class AuthMiddleware(BaseMiddleware):
    def __init__(self, allowed_users: list[int], chat_id_store=None):
        self.allowed_users = set(allowed_users)
        self.chat_id_store = chat_id_store
        super().__init__()

    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        user_id = event.from_user.id if event.from_user else None

        if user_id not in self.allowed_users:
            logger.warning(f"Unauthorized access attempt from user {user_id}")
            return None

        # Store chat ID for alerts
        if self.chat_id_store is not None and event.chat:
            self.chat_id_store.set_chat_id(event.chat.id)

        return await handler(event, data)


def create_auth_middleware(allowed_users: list[int], chat_id_store=None) -> AuthMiddleware:
    """Factory function for auth middleware."""
    return AuthMiddleware(allowed_users, chat_id_store)


def create_bot(token: str) -> Bot:
    """Create Telegram bot instance."""
    return Bot(token=token)


def create_dispatcher(allowed_users: list[int], chat_id_store=None) -> Dispatcher:
    """Create dispatcher with auth middleware."""
    dp = Dispatcher()
    dp.message.middleware(AuthMiddleware(allowed_users, chat_id_store))
    return dp


def register_commands(
    dp: Dispatcher,
    state: ContainerStateManager,
    docker_client: docker.DockerClient | None = None,
) -> None:
    """Register all command handlers."""
    dp.message.register(help_command(state), Command("help"))
    dp.message.register(status_command(state), Command("status"))

    if docker_client:
        dp.message.register(logs_command(state, docker_client), Command("logs"))
```

**Step 5: Update help text in commands.py**

Update `HELP_TEXT` in `src/bot/commands.py`:

```python
HELP_TEXT = """📋 *Available Commands*

/status - Container status overview
/status <name> - Details for specific container
/logs <name> [n] - Last n log lines (default 20)
/help - Show this help message

_Partial container names work: /status rad → radarr_"""
```

**Step 6: Run tests**

Run: `pytest tests/test_commands.py -v`
Expected: All tests PASS

**Step 7: Commit**

```bash
git add src/bot/commands.py src/bot/telegram_bot.py tests/test_commands.py
git commit -m "feat: add /logs command to view container logs"
```

---

## Task 9: Main Integration

**Files:**
- Modify: `src/main.py`

**Step 1: Update main.py to wire everything together**

Update `src/main.py`:

```python
import asyncio
import logging
import sys

from src.config import Settings, AppConfig
from src.state import ContainerStateManager
from src.monitors.docker_events import DockerEventMonitor
from src.monitors.log_watcher import LogWatcher
from src.bot.telegram_bot import create_bot, create_dispatcher, register_commands
from src.alerts.manager import AlertManager, ChatIdStore
from src.alerts.rate_limiter import RateLimiter


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    # Load configuration
    try:
        settings = Settings()
        config = AppConfig(settings)
    except Exception as e:
        logger.error(f"Failed to load configuration: {e}")
        sys.exit(1)

    logging.getLogger().setLevel(settings.log_level)
    logger.info("Configuration loaded")

    # Initialize state manager
    state = ContainerStateManager()

    # Initialize chat ID store for alerts
    chat_id_store = ChatIdStore()

    # Initialize rate limiter
    rate_limiter = RateLimiter(
        cooldown_seconds=config.log_watching["cooldown_seconds"]
    )

    # Initialize Telegram bot first (needed for alerts)
    bot = create_bot(settings.telegram_bot_token)

    # Alert manager (chat_id will be set when user first messages bot)
    # We'll update this dynamically
    alert_manager = None

    async def get_alert_manager():
        nonlocal alert_manager
        chat_id = chat_id_store.get_chat_id()
        if chat_id and alert_manager is None:
            alert_manager = AlertManager(bot=bot, chat_id=chat_id)
        return alert_manager

    # Initialize Docker monitor with alert support
    from src.monitors.docker_events import DockerEventMonitor
    import docker

    docker_client = docker.DockerClient(base_url="unix:///var/run/docker.sock")

    # Create a wrapper for crash alerts that gets the alert manager dynamically
    class AlertManagerProxy:
        async def send_crash_alert(self, **kwargs):
            manager = await get_alert_manager()
            if manager:
                await manager.send_crash_alert(**kwargs)
            else:
                logger.warning("No chat ID yet, cannot send crash alert")

    monitor = DockerEventMonitor(
        state_manager=state,
        ignored_containers=config.ignored_containers,
        alert_manager=AlertManagerProxy(),
        rate_limiter=rate_limiter,
    )

    try:
        monitor.connect()
        monitor.load_initial_state()
    except Exception as e:
        logger.error(f"Failed to connect to Docker: {e}")
        sys.exit(1)

    # Initialize log watcher
    async def on_log_error(container_name: str, error_line: str):
        manager = await get_alert_manager()
        if not manager:
            logger.warning("No chat ID yet, cannot send log error alert")
            return

        if rate_limiter.should_alert(container_name):
            suppressed = rate_limiter.get_suppressed_count(container_name)
            await manager.send_log_error_alert(
                container_name=container_name,
                error_line=error_line,
                suppressed_count=suppressed,
            )
            rate_limiter.record_alert(container_name)
        else:
            rate_limiter.record_suppressed(container_name)

    log_watcher = LogWatcher(
        containers=config.log_watching["containers"],
        error_patterns=config.log_watching["error_patterns"],
        ignore_patterns=config.log_watching["ignore_patterns"],
        on_error=on_log_error,
    )
    log_watcher._client = docker_client  # Share the client

    # Initialize Telegram dispatcher
    dp = create_dispatcher(settings.telegram_allowed_users, chat_id_store)
    register_commands(dp, state, docker_client)

    # Start Docker event monitor as background task
    monitor_task = asyncio.create_task(monitor.start())

    # Start log watcher as background task
    log_watcher_task = asyncio.create_task(log_watcher.start())

    logger.info("Starting Telegram bot...")

    try:
        # Run bot until shutdown (aiogram handles SIGINT/SIGTERM)
        await dp.start_polling(bot)
    finally:
        logger.info("Shutting down...")
        monitor.stop()
        log_watcher.stop()
        monitor_task.cancel()
        log_watcher_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass
        try:
            await log_watcher_task
        except asyncio.CancelledError:
            pass
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
```

**Step 2: Run all tests**

Run: `pytest -v`
Expected: All tests PASS

**Step 3: Commit**

```bash
git add src/main.py
git commit -m "feat: integrate alerts and log watching into main"
```

---

## Task 10: Integration Tests

**Files:**
- Create: `tests/test_phase2_integration.py`

**Step 1: Write integration tests**

Create `tests/test_phase2_integration.py`:

```python
"""
Phase 2 integration tests - verify alert flow works end-to-end.
"""
import pytest
from unittest.mock import MagicMock, AsyncMock


@pytest.mark.asyncio
async def test_crash_alert_flow():
    """Test: Container crash triggers Telegram alert."""
    from src.state import ContainerStateManager
    from src.models import ContainerInfo
    from src.monitors.docker_events import DockerEventMonitor
    from src.alerts.manager import AlertManager
    from src.alerts.rate_limiter import RateLimiter

    # Setup
    state = ContainerStateManager()
    state.update(ContainerInfo("radarr", "running", None, "linuxserver/radarr", None))

    bot = MagicMock()
    bot.send_message = AsyncMock()

    alert_manager = AlertManager(bot=bot, chat_id=12345)
    rate_limiter = RateLimiter(cooldown_seconds=900)

    monitor = DockerEventMonitor(
        state_manager=state,
        ignored_containers=[],
        alert_manager=alert_manager,
        rate_limiter=rate_limiter,
    )

    # Simulate crash
    event = {
        "Action": "die",
        "Actor": {"Attributes": {"name": "radarr", "exitCode": "137"}}
    }

    await monitor._handle_crash_event(event)

    # Verify
    bot.send_message.assert_called_once()
    msg = bot.send_message.call_args[1]["text"]
    assert "CRASHED" in msg
    assert "radarr" in msg
    assert "137" in msg


@pytest.mark.asyncio
async def test_log_error_with_rate_limiting():
    """Test: Log errors are rate limited."""
    from src.alerts.manager import AlertManager
    from src.alerts.rate_limiter import RateLimiter

    bot = MagicMock()
    bot.send_message = AsyncMock()

    alert_manager = AlertManager(bot=bot, chat_id=12345)
    rate_limiter = RateLimiter(cooldown_seconds=900)

    # First error - should alert
    if rate_limiter.should_alert("radarr"):
        await alert_manager.send_log_error_alert("radarr", "Error 1", 0)
        rate_limiter.record_alert("radarr")

    assert bot.send_message.call_count == 1

    # Second error - should be suppressed
    if rate_limiter.should_alert("radarr"):
        await alert_manager.send_log_error_alert("radarr", "Error 2", 0)
        rate_limiter.record_alert("radarr")
    else:
        rate_limiter.record_suppressed("radarr")

    assert bot.send_message.call_count == 1  # Still 1
    assert rate_limiter.get_suppressed_count("radarr") == 1


@pytest.mark.asyncio
async def test_ignored_container_no_alert():
    """Test: Ignored containers don't trigger alerts."""
    from src.state import ContainerStateManager
    from src.monitors.docker_events import DockerEventMonitor
    from src.alerts.manager import AlertManager
    from src.alerts.rate_limiter import RateLimiter

    state = ContainerStateManager()

    bot = MagicMock()
    bot.send_message = AsyncMock()

    alert_manager = AlertManager(bot=bot, chat_id=12345)
    rate_limiter = RateLimiter(cooldown_seconds=900)

    monitor = DockerEventMonitor(
        state_manager=state,
        ignored_containers=["Kometa"],
        alert_manager=alert_manager,
        rate_limiter=rate_limiter,
    )

    # Simulate Kometa crash
    event = {
        "Action": "die",
        "Actor": {"Attributes": {"name": "Kometa", "exitCode": "1"}}
    }

    await monitor._handle_crash_event(event)

    # Verify no alert sent
    bot.send_message.assert_not_called()
```

**Step 2: Run integration tests**

Run: `pytest tests/test_phase2_integration.py -v`
Expected: All 3 tests PASS

**Step 3: Run full test suite**

Run: `pytest -v`
Expected: All tests PASS

**Step 4: Commit**

```bash
git add tests/test_phase2_integration.py
git commit -m "test: add Phase 2 integration tests"
```

---

## Task 11: Final Verification & Documentation

**Step 1: Run full test suite**

Run: `pytest -v --tb=short`
Expected: All tests pass

**Step 2: Update requirements.txt if needed**

Verify `requirements.txt` has all dependencies:

```
docker>=7.0.0
aiogram>=3.4.0
pyyaml>=6.0
pydantic>=2.0
pydantic-settings>=2.0
```

**Step 3: Build and test Docker image**

```bash
docker-compose build --no-cache
```

**Step 4: Tag release**

```bash
git tag -a v0.2.0 -m "Phase 2: Alerts and log watching"
```

**Step 5: Push to remote**

```bash
git push origin master
git push origin v0.2.0
```

---

## Success Criteria Checklist

- [ ] Crash alerts sent when container exits with non-zero code
- [ ] Log errors detected and alerted with 15-min rate limiting
- [ ] `/logs` command shows container logs
- [ ] Ignored containers (Kometa) don't trigger alerts
- [ ] Config file controls watched containers and patterns
- [ ] All tests pass
- [ ] Docker image builds successfully
