# Phase 1 MVP Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a working Telegram bot that shows Docker container status from an Unraid server.

**Architecture:** Async Python application with Docker SDK running in thread executor, aiogram Telegram bot, and in-memory container state cache. Pydantic for configuration validation.

**Tech Stack:** Python 3.11+, docker SDK, aiogram 3.x, pydantic-settings, pytest

---

## Task 1: Project Setup

**Files:**
- Create: `requirements.txt`
- Create: `pyproject.toml`
- Create: `src/__init__.py`
- Create: `tests/__init__.py`

**Step 1: Create requirements.txt**

```
docker>=7.0.0
aiogram>=3.4.0
pyyaml>=6.0
pydantic>=2.0
pydantic-settings>=2.0
```

**Step 2: Create pyproject.toml**

```toml
[project]
name = "unraid-monitor-bot"
version = "0.1.0"
requires-python = ">=3.11"

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"

[tool.mypy]
python_version = "3.11"
strict = true
ignore_missing_imports = true

[tool.ruff]
line-length = 100
target-version = "py311"
```

**Step 3: Create package init files**

Create empty `src/__init__.py` and `tests/__init__.py`.

**Step 4: Install dependencies**

Run: `pip install -r requirements.txt pytest pytest-asyncio`

**Step 5: Verify pytest works**

Run: `pytest --collect-only`
Expected: "no tests ran" (no errors)

**Step 6: Commit**

```bash
git add requirements.txt pyproject.toml src/__init__.py tests/__init__.py
git commit -m "chore: initial project setup with dependencies"
```

---

## Task 2: Configuration with Pydantic

**Files:**
- Create: `src/config.py`
- Create: `tests/test_config.py`

**Step 1: Write the failing test**

Create `tests/test_config.py`:

```python
import pytest
from unittest.mock import patch


def test_config_loads_telegram_token_from_env():
    with patch.dict("os.environ", {
        "TELEGRAM_BOT_TOKEN": "test-token-123",
        "TELEGRAM_ALLOWED_USERS": "111,222",
    }):
        from src.config import Settings
        settings = Settings()
        assert settings.telegram_bot_token == "test-token-123"


def test_config_parses_allowed_users_as_list():
    with patch.dict("os.environ", {
        "TELEGRAM_BOT_TOKEN": "test-token",
        "TELEGRAM_ALLOWED_USERS": "111,222,333",
    }):
        from src.config import Settings
        settings = Settings()
        assert settings.telegram_allowed_users == [111, 222, 333]


def test_config_raises_without_required_vars():
    with patch.dict("os.environ", {}, clear=True):
        from src.config import Settings
        with pytest.raises(Exception):  # ValidationError
            Settings()
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py -v`
Expected: FAIL with "cannot import name 'Settings'"

**Step 3: Write minimal implementation**

Create `src/config.py`:

```python
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    telegram_bot_token: str
    telegram_allowed_users: list[int]
    anthropic_api_key: str | None = None
    config_path: str = "config/config.yaml"
    log_level: str = "INFO"

    class Config:
        env_file = ".env"

    @classmethod
    def parse_env_var(cls, field_name: str, raw_val: str):
        if field_name == "telegram_allowed_users":
            return [int(x.strip()) for x in raw_val.split(",")]
        return raw_val
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_config.py -v`
Expected: All 3 tests PASS

**Step 5: Commit**

```bash
git add src/config.py tests/test_config.py
git commit -m "feat: add configuration with pydantic-settings"
```

---

## Task 3: Container Models

**Files:**
- Create: `src/models.py`
- Create: `tests/test_models.py`

**Step 1: Write the failing test**

Create `tests/test_models.py`:

```python
from datetime import datetime


def test_container_info_creation():
    from src.models import ContainerInfo

    info = ContainerInfo(
        name="radarr",
        status="running",
        health="healthy",
        image="linuxserver/radarr:latest",
        started_at=datetime(2025, 1, 25, 10, 0, 0),
    )
    assert info.name == "radarr"
    assert info.status == "running"
    assert info.health == "healthy"


def test_container_info_health_optional():
    from src.models import ContainerInfo

    info = ContainerInfo(
        name="plex",
        status="running",
        health=None,
        image="linuxserver/plex:latest",
        started_at=None,
    )
    assert info.health is None
    assert info.started_at is None
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_models.py -v`
Expected: FAIL with "cannot import name 'ContainerInfo'"

**Step 3: Write minimal implementation**

Create `src/models.py`:

```python
from dataclasses import dataclass
from datetime import datetime


@dataclass
class ContainerInfo:
    name: str
    status: str  # running, exited, paused
    health: str | None  # healthy, unhealthy, starting, None
    image: str
    started_at: datetime | None
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_models.py -v`
Expected: All 2 tests PASS

**Step 5: Commit**

```bash
git add src/models.py tests/test_models.py
git commit -m "feat: add ContainerInfo model"
```

---

## Task 4: Container State Manager

**Files:**
- Create: `src/state.py`
- Create: `tests/test_state.py`

**Step 1: Write the failing test**

Create `tests/test_state.py`:

```python
from datetime import datetime


def test_state_manager_update_and_get():
    from src.state import ContainerStateManager
    from src.models import ContainerInfo

    manager = ContainerStateManager()
    info = ContainerInfo(
        name="radarr",
        status="running",
        health="healthy",
        image="linuxserver/radarr:latest",
        started_at=datetime.now(),
    )
    manager.update(info)

    result = manager.get("radarr")
    assert result is not None
    assert result.name == "radarr"


def test_state_manager_get_all():
    from src.state import ContainerStateManager
    from src.models import ContainerInfo

    manager = ContainerStateManager()
    manager.update(ContainerInfo("a", "running", None, "img", None))
    manager.update(ContainerInfo("b", "exited", None, "img", None))

    all_containers = manager.get_all()
    assert len(all_containers) == 2


def test_state_manager_find_by_partial_name():
    from src.state import ContainerStateManager
    from src.models import ContainerInfo

    manager = ContainerStateManager()
    manager.update(ContainerInfo("radarr", "running", None, "img", None))
    manager.update(ContainerInfo("sonarr", "running", None, "img", None))
    manager.update(ContainerInfo("radar-test", "running", None, "img", None))

    matches = manager.find_by_name("radar")
    assert len(matches) == 2
    names = [m.name for m in matches]
    assert "radarr" in names
    assert "radar-test" in names


def test_state_manager_summary():
    from src.state import ContainerStateManager
    from src.models import ContainerInfo

    manager = ContainerStateManager()
    manager.update(ContainerInfo("a", "running", "healthy", "img", None))
    manager.update(ContainerInfo("b", "running", "unhealthy", "img", None))
    manager.update(ContainerInfo("c", "exited", None, "img", None))
    manager.update(ContainerInfo("d", "running", None, "img", None))

    summary = manager.get_summary()
    assert summary["running"] == 3
    assert summary["stopped"] == 1
    assert summary["unhealthy"] == 1
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_state.py -v`
Expected: FAIL with "cannot import name 'ContainerStateManager'"

**Step 3: Write minimal implementation**

Create `src/state.py`:

```python
from src.models import ContainerInfo


class ContainerStateManager:
    def __init__(self):
        self._containers: dict[str, ContainerInfo] = {}

    def update(self, info: ContainerInfo) -> None:
        self._containers[info.name] = info

    def get(self, name: str) -> ContainerInfo | None:
        return self._containers.get(name)

    def get_all(self) -> list[ContainerInfo]:
        return list(self._containers.values())

    def find_by_name(self, partial: str) -> list[ContainerInfo]:
        partial_lower = partial.lower()
        return [
            c for c in self._containers.values()
            if partial_lower in c.name.lower()
        ]

    def get_summary(self) -> dict[str, int]:
        running = 0
        stopped = 0
        unhealthy = 0

        for c in self._containers.values():
            if c.status == "running":
                running += 1
            else:
                stopped += 1
            if c.health == "unhealthy":
                unhealthy += 1

        return {
            "running": running,
            "stopped": stopped,
            "unhealthy": unhealthy,
        }
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_state.py -v`
Expected: All 4 tests PASS

**Step 5: Commit**

```bash
git add src/state.py tests/test_state.py
git commit -m "feat: add ContainerStateManager for in-memory state"
```

---

## Task 5: Docker Event Monitor

**Files:**
- Create: `src/monitors/__init__.py`
- Create: `src/monitors/docker_events.py`
- Create: `tests/test_docker_events.py`

**Step 1: Write the failing test**

Create `tests/test_docker_events.py`:

```python
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime


def test_parse_container_from_docker_api():
    from src.monitors.docker_events import parse_container

    # Mock Docker container object
    mock_container = MagicMock()
    mock_container.name = "radarr"
    mock_container.status = "running"
    mock_container.image.tags = ["linuxserver/radarr:latest"]
    mock_container.attrs = {
        "State": {
            "Health": {"Status": "healthy"},
            "StartedAt": "2025-01-25T10:00:00.000000000Z",
        }
    }

    info = parse_container(mock_container)
    assert info.name == "radarr"
    assert info.status == "running"
    assert info.health == "healthy"
    assert info.image == "linuxserver/radarr:latest"


def test_parse_container_without_health_check():
    from src.monitors.docker_events import parse_container

    mock_container = MagicMock()
    mock_container.name = "plex"
    mock_container.status = "running"
    mock_container.image.tags = ["linuxserver/plex:latest"]
    mock_container.attrs = {
        "State": {
            "StartedAt": "2025-01-25T10:00:00.000000000Z",
        }
    }

    info = parse_container(mock_container)
    assert info.health is None


def test_parse_container_no_image_tags():
    from src.monitors.docker_events import parse_container

    mock_container = MagicMock()
    mock_container.name = "test"
    mock_container.status = "running"
    mock_container.image.tags = []
    mock_container.image.id = "sha256:abc123"
    mock_container.attrs = {"State": {}}

    info = parse_container(mock_container)
    assert info.image == "sha256:abc123"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_docker_events.py -v`
Expected: FAIL with "cannot import name 'parse_container'"

**Step 3: Write minimal implementation**

Create `src/monitors/__init__.py` (empty file).

Create `src/monitors/docker_events.py`:

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
    ):
        self.state_manager = state_manager
        self.ignored_containers = set(ignored_containers or [])
        self._client: docker.DockerClient | None = None
        self._running = False

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

        # Run blocking event loop in thread
        await asyncio.to_thread(self._event_loop)

    def stop(self) -> None:
        """Stop monitoring."""
        self._running = False
        logger.info("Stopping Docker event monitor")

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
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_docker_events.py -v`
Expected: All 3 tests PASS

**Step 5: Commit**

```bash
git add src/monitors/__init__.py src/monitors/docker_events.py tests/test_docker_events.py
git commit -m "feat: add Docker event monitor with container parsing"
```

---

## Task 6: Telegram Bot Setup

**Files:**
- Create: `src/bot/__init__.py`
- Create: `src/bot/telegram_bot.py`
- Create: `tests/test_telegram_bot.py`

**Step 1: Write the failing test**

Create `tests/test_telegram_bot.py`:

```python
import pytest
from unittest.mock import MagicMock, AsyncMock


@pytest.mark.asyncio
async def test_auth_middleware_allows_authorized_user():
    from src.bot.telegram_bot import create_auth_middleware

    middleware = create_auth_middleware(allowed_users=[123, 456])

    # Mock message from authorized user
    message = MagicMock()
    message.from_user.id = 123

    handler = AsyncMock(return_value="ok")

    result = await middleware(handler, message, {})

    handler.assert_called_once()
    assert result == "ok"


@pytest.mark.asyncio
async def test_auth_middleware_blocks_unauthorized_user():
    from src.bot.telegram_bot import create_auth_middleware

    middleware = create_auth_middleware(allowed_users=[123, 456])

    # Mock message from unauthorized user
    message = MagicMock()
    message.from_user.id = 999

    handler = AsyncMock(return_value="ok")

    result = await middleware(handler, message, {})

    handler.assert_not_called()
    assert result is None
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_telegram_bot.py -v`
Expected: FAIL with "cannot import name 'create_auth_middleware'"

**Step 3: Write minimal implementation**

Create `src/bot/__init__.py` (empty file).

Create `src/bot/telegram_bot.py`:

```python
import logging
from typing import Any, Awaitable, Callable

from aiogram import Bot, Dispatcher, BaseMiddleware
from aiogram.types import Message

logger = logging.getLogger(__name__)


class AuthMiddleware(BaseMiddleware):
    def __init__(self, allowed_users: list[int]):
        self.allowed_users = set(allowed_users)
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

        return await handler(event, data)


def create_auth_middleware(allowed_users: list[int]) -> AuthMiddleware:
    """Factory function for auth middleware."""
    return AuthMiddleware(allowed_users)


def create_bot(token: str) -> Bot:
    """Create Telegram bot instance."""
    return Bot(token=token)


def create_dispatcher(allowed_users: list[int]) -> Dispatcher:
    """Create dispatcher with auth middleware."""
    dp = Dispatcher()
    dp.message.middleware(AuthMiddleware(allowed_users))
    return dp
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_telegram_bot.py -v`
Expected: All 2 tests PASS

**Step 5: Commit**

```bash
git add src/bot/__init__.py src/bot/telegram_bot.py tests/test_telegram_bot.py
git commit -m "feat: add Telegram bot with auth middleware"
```

---

## Task 7: Bot Commands - /help

**Files:**
- Create: `src/bot/commands.py`
- Create: `tests/test_commands.py`

**Step 1: Write the failing test**

Create `tests/test_commands.py`:

```python
import pytest
from unittest.mock import MagicMock, AsyncMock


@pytest.mark.asyncio
async def test_help_command_returns_help_text():
    from src.bot.commands import help_command
    from src.state import ContainerStateManager

    state = ContainerStateManager()
    handler = help_command(state)

    message = MagicMock()
    message.answer = AsyncMock()

    await handler(message)

    message.answer.assert_called_once()
    call_args = message.answer.call_args[0][0]
    assert "/status" in call_args
    assert "/help" in call_args
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_commands.py::test_help_command_returns_help_text -v`
Expected: FAIL with "cannot import name 'help_command'"

**Step 3: Write minimal implementation**

Create `src/bot/commands.py`:

```python
from typing import Callable, Awaitable

from aiogram.types import Message

from src.state import ContainerStateManager


HELP_TEXT = """📋 *Available Commands*

/status - Container status overview
/status <name> - Details for specific container
/help - Show this help message

_Partial container names work: /status rad → radarr_"""


def help_command(state: ContainerStateManager) -> Callable[[Message], Awaitable[None]]:
    """Factory for /help command handler."""
    async def handler(message: Message) -> None:
        await message.answer(HELP_TEXT, parse_mode="Markdown")
    return handler
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_commands.py::test_help_command_returns_help_text -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/bot/commands.py tests/test_commands.py
git commit -m "feat: add /help command"
```

---

## Task 8: Bot Commands - /status (summary)

**Files:**
- Modify: `src/bot/commands.py`
- Modify: `tests/test_commands.py`

**Step 1: Write the failing test**

Add to `tests/test_commands.py`:

```python
@pytest.mark.asyncio
async def test_status_command_shows_summary():
    from src.bot.commands import status_command
    from src.state import ContainerStateManager
    from src.models import ContainerInfo

    state = ContainerStateManager()
    state.update(ContainerInfo("plex", "running", "healthy", "img", None))
    state.update(ContainerInfo("radarr", "running", "unhealthy", "img", None))
    state.update(ContainerInfo("backup", "exited", None, "img", None))

    handler = status_command(state)

    message = MagicMock()
    message.text = "/status"
    message.answer = AsyncMock()

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]
    assert "Running: 2" in response
    assert "Stopped: 1" in response
    assert "Unhealthy: 1" in response
    assert "backup" in response  # stopped container listed
    assert "radarr" in response  # unhealthy container listed
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_commands.py::test_status_command_shows_summary -v`
Expected: FAIL with "cannot import name 'status_command'"

**Step 3: Write minimal implementation**

Add to `src/bot/commands.py`:

```python
def format_status_summary(state: ContainerStateManager) -> str:
    """Format container status summary."""
    summary = state.get_summary()
    all_containers = state.get_all()

    stopped = [c.name for c in all_containers if c.status != "running"]
    unhealthy = [c.name for c in all_containers if c.health == "unhealthy"]

    lines = [
        "📊 *Container Status*",
        "",
        f"✅ Running: {summary['running']}",
        f"🔴 Stopped: {summary['stopped']}",
        f"⚠️ Unhealthy: {summary['unhealthy']}",
    ]

    if stopped:
        lines.append("")
        lines.append(f"*Stopped:* {', '.join(stopped)}")

    if unhealthy:
        lines.append(f"*Unhealthy:* {', '.join(unhealthy)}")

    if not stopped and not unhealthy:
        lines.append("")
        lines.append("_All containers healthy_ ✨")
    else:
        lines.append("")
        lines.append("_Use /status <name> for details_")

    return "\n".join(lines)


def status_command(state: ContainerStateManager) -> Callable[[Message], Awaitable[None]]:
    """Factory for /status command handler."""
    async def handler(message: Message) -> None:
        text = message.text or ""
        parts = text.strip().split(maxsplit=1)

        if len(parts) == 1:
            # No argument - show summary
            response = format_status_summary(state)
            await message.answer(response, parse_mode="Markdown")
        else:
            # Has argument - will handle in next task
            await message.answer("Container details not yet implemented")

    return handler
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_commands.py::test_status_command_shows_summary -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/bot/commands.py tests/test_commands.py
git commit -m "feat: add /status command with summary view"
```

---

## Task 9: Bot Commands - /status <name>

**Files:**
- Modify: `src/bot/commands.py`
- Modify: `tests/test_commands.py`

**Step 1: Write the failing tests**

Add to `tests/test_commands.py`:

```python
@pytest.mark.asyncio
async def test_status_command_shows_container_details():
    from src.bot.commands import status_command
    from src.state import ContainerStateManager
    from src.models import ContainerInfo
    from datetime import datetime

    state = ContainerStateManager()
    state.update(ContainerInfo(
        "radarr", "running", "healthy",
        "linuxserver/radarr:latest",
        datetime(2025, 1, 25, 10, 0, 0),
    ))

    handler = status_command(state)

    message = MagicMock()
    message.text = "/status radarr"
    message.answer = AsyncMock()

    await handler(message)

    response = message.answer.call_args[0][0]
    assert "radarr" in response
    assert "running" in response.lower()
    assert "healthy" in response.lower()


@pytest.mark.asyncio
async def test_status_command_partial_match():
    from src.bot.commands import status_command
    from src.state import ContainerStateManager
    from src.models import ContainerInfo

    state = ContainerStateManager()
    state.update(ContainerInfo("radarr", "running", None, "img", None))

    handler = status_command(state)

    message = MagicMock()
    message.text = "/status rad"
    message.answer = AsyncMock()

    await handler(message)

    response = message.answer.call_args[0][0]
    assert "radarr" in response


@pytest.mark.asyncio
async def test_status_command_multiple_matches():
    from src.bot.commands import status_command
    from src.state import ContainerStateManager
    from src.models import ContainerInfo

    state = ContainerStateManager()
    state.update(ContainerInfo("radarr", "running", None, "img", None))
    state.update(ContainerInfo("radarr-test", "running", None, "img", None))

    handler = status_command(state)

    message = MagicMock()
    message.text = "/status radar"
    message.answer = AsyncMock()

    await handler(message)

    response = message.answer.call_args[0][0]
    assert "radarr" in response
    assert "radarr-test" in response
    assert "multiple" in response.lower() or "matches" in response.lower()


@pytest.mark.asyncio
async def test_status_command_no_match():
    from src.bot.commands import status_command
    from src.state import ContainerStateManager

    state = ContainerStateManager()
    handler = status_command(state)

    message = MagicMock()
    message.text = "/status nonexistent"
    message.answer = AsyncMock()

    await handler(message)

    response = message.answer.call_args[0][0]
    assert "not found" in response.lower() or "no container" in response.lower()
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_commands.py -k "status_command" -v`
Expected: 3 FAIL (details not implemented yet)

**Step 3: Update implementation**

Update `status_command` in `src/bot/commands.py`:

```python
def format_container_details(container: ContainerInfo) -> str:
    """Format detailed container info."""
    health_emoji = {
        "healthy": "✅",
        "unhealthy": "⚠️",
        "starting": "🔄",
        None: "➖",
    }
    status_emoji = "🟢" if container.status == "running" else "🔴"

    lines = [
        f"*{container.name}*",
        "",
        f"Status: {status_emoji} {container.status}",
        f"Health: {health_emoji.get(container.health, '➖')} {container.health or 'no healthcheck'}",
        f"Image: `{container.image}`",
    ]

    if container.started_at:
        lines.append(f"Started: {container.started_at.strftime('%Y-%m-%d %H:%M:%S')}")

    return "\n".join(lines)


def status_command(state: ContainerStateManager) -> Callable[[Message], Awaitable[None]]:
    """Factory for /status command handler."""
    async def handler(message: Message) -> None:
        text = message.text or ""
        parts = text.strip().split(maxsplit=1)

        if len(parts) == 1:
            # No argument - show summary
            response = format_status_summary(state)
        else:
            # Search for container
            query = parts[1].strip()
            matches = state.find_by_name(query)

            if not matches:
                response = f"❌ No container found matching '{query}'"
            elif len(matches) == 1:
                response = format_container_details(matches[0])
            else:
                names = ", ".join(m.name for m in matches)
                response = f"Multiple matches found: {names}\n\n_Be more specific_"

        await message.answer(response, parse_mode="Markdown")

    return handler
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_commands.py -k "status_command" -v`
Expected: All 4 tests PASS

**Step 5: Commit**

```bash
git add src/bot/commands.py tests/test_commands.py
git commit -m "feat: add /status <name> with partial matching"
```

---

## Task 10: Register Commands with Dispatcher

**Files:**
- Modify: `src/bot/telegram_bot.py`
- Modify: `tests/test_telegram_bot.py`

**Step 1: Write the failing test**

Add to `tests/test_telegram_bot.py`:

```python
@pytest.mark.asyncio
async def test_register_commands_adds_handlers():
    from src.bot.telegram_bot import create_dispatcher, register_commands
    from src.state import ContainerStateManager

    state = ContainerStateManager()
    dp = create_dispatcher(allowed_users=[123])

    register_commands(dp, state)

    # Check that handlers were registered
    # aiogram 3.x stores handlers in router
    message_handlers = dp.message.handlers
    assert len(message_handlers) >= 2  # /status and /help
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_telegram_bot.py::test_register_commands_adds_handlers -v`
Expected: FAIL with "cannot import name 'register_commands'"

**Step 3: Update implementation**

Add to `src/bot/telegram_bot.py`:

```python
from aiogram.filters import Command

from src.state import ContainerStateManager
from src.bot.commands import help_command, status_command


def register_commands(dp: Dispatcher, state: ContainerStateManager) -> None:
    """Register all command handlers."""
    dp.message.register(help_command(state), Command("help"))
    dp.message.register(status_command(state), Command("status"))
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_telegram_bot.py::test_register_commands_adds_handlers -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/bot/telegram_bot.py tests/test_telegram_bot.py
git commit -m "feat: register bot commands with dispatcher"
```

---

## Task 11: Main Entry Point

**Files:**
- Create: `src/main.py`

**Step 1: Write the main.py**

Create `src/main.py`:

```python
import asyncio
import logging
import signal
import sys

from src.config import Settings
from src.state import ContainerStateManager
from src.monitors.docker_events import DockerEventMonitor
from src.bot.telegram_bot import create_bot, create_dispatcher, register_commands


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    # Load configuration
    try:
        settings = Settings()
    except Exception as e:
        logger.error(f"Failed to load configuration: {e}")
        sys.exit(1)

    logging.getLogger().setLevel(settings.log_level)
    logger.info("Configuration loaded")

    # Initialize state manager
    state = ContainerStateManager()

    # Initialize Docker monitor
    monitor = DockerEventMonitor(
        state_manager=state,
        ignored_containers=[],  # TODO: load from config
    )

    try:
        monitor.connect()
        monitor.load_initial_state()
    except Exception as e:
        logger.error(f"Failed to connect to Docker: {e}")
        sys.exit(1)

    # Initialize Telegram bot
    bot = create_bot(settings.telegram_bot_token)
    dp = create_dispatcher(settings.telegram_allowed_users)
    register_commands(dp, state)

    # Handle shutdown signals
    shutdown_event = asyncio.Event()

    def signal_handler(sig: int, frame) -> None:
        logger.info(f"Received signal {sig}, shutting down...")
        shutdown_event.set()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Start Docker event monitor as background task
    monitor_task = asyncio.create_task(monitor.start())

    logger.info("Starting Telegram bot...")

    try:
        # Run bot until shutdown
        await dp.start_polling(bot, handle_signals=False)
    finally:
        logger.info("Shutting down...")
        monitor.stop()
        monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
```

**Step 2: Verify syntax**

Run: `python -m py_compile src/main.py`
Expected: No output (success)

**Step 3: Commit**

```bash
git add src/main.py
git commit -m "feat: add main entry point with async orchestration"
```

---

## Task 12: Default Config File

**Files:**
- Create: `config/config.yaml`

**Step 1: Create config file**

Create `config/config.yaml`:

```yaml
# Unraid Monitor Bot Configuration

monitoring:
  health_check_interval: 60  # seconds (used in Phase 2)

# Containers to ignore (won't be monitored or shown)
ignored_containers:
  - Kometa

# Log filtering (Phase 2)
log_filters:
  error_patterns:
    - "error"
    - "exception"
    - "fatal"
  ignore_patterns:
    - "DeprecationWarning"
```

**Step 2: Commit**

```bash
git add config/config.yaml
git commit -m "chore: add default config file"
```

---

## Task 13: Dockerfile

**Files:**
- Create: `Dockerfile`

**Step 1: Create Dockerfile**

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY src/ ./src/
COPY config/ ./config/

# Run as non-root
RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

CMD ["python", "-m", "src.main"]
```

**Step 2: Commit**

```bash
git add Dockerfile
git commit -m "chore: add Dockerfile"
```

---

## Task 14: Docker Compose

**Files:**
- Create: `docker-compose.yml`
- Create: `.env.example`

**Step 1: Create docker-compose.yml**

```yaml
services:
  unraid-monitor-bot:
    build: .
    container_name: unraid-monitor-bot
    restart: unless-stopped
    environment:
      - TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
      - TELEGRAM_ALLOWED_USERS=${TELEGRAM_ALLOWED_USERS}
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:-}
      - TZ=Europe/London
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - ./config:/app/config:ro
```

**Step 2: Create .env.example**

```bash
# Required
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
TELEGRAM_ALLOWED_USERS=123456789

# Optional
ANTHROPIC_API_KEY=your_anthropic_api_key_here
```

**Step 3: Add .env to .gitignore**

Create `.gitignore`:

```
.env
__pycache__/
*.pyc
.pytest_cache/
.mypy_cache/
*.egg-info/
dist/
build/
.venv/
venv/
```

**Step 4: Commit**

```bash
git add docker-compose.yml .env.example .gitignore
git commit -m "chore: add docker-compose and env example"
```

---

## Task 15: Integration Test

**Files:**
- Create: `tests/test_integration.py`

**Step 1: Write integration test**

Create `tests/test_integration.py`:

```python
"""
Integration test - verifies all components work together.
Run with: pytest tests/test_integration.py -v
"""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch


@pytest.mark.asyncio
async def test_full_status_flow():
    """Test: Docker data flows through to Telegram response."""
    from src.state import ContainerStateManager
    from src.models import ContainerInfo
    from src.bot.commands import status_command

    # 1. Set up state (simulating Docker monitor)
    state = ContainerStateManager()
    state.update(ContainerInfo("plex", "running", "healthy", "linuxserver/plex", None))
    state.update(ContainerInfo("radarr", "running", None, "linuxserver/radarr", None))
    state.update(ContainerInfo("backup", "exited", None, "backup:latest", None))

    # 2. Create command handler
    handler = status_command(state)

    # 3. Simulate Telegram message
    message = MagicMock()
    message.text = "/status"
    message.answer = AsyncMock()

    await handler(message)

    # 4. Verify response contains expected data
    response = message.answer.call_args[0][0]
    assert "Running: 2" in response
    assert "Stopped: 1" in response
    assert "backup" in response


@pytest.mark.asyncio
async def test_docker_event_updates_state():
    """Test: Docker events update state manager."""
    from src.monitors.docker_events import parse_container
    from src.state import ContainerStateManager

    state = ContainerStateManager()

    # Simulate container
    mock_container = MagicMock()
    mock_container.name = "radarr"
    mock_container.status = "running"
    mock_container.image.tags = ["linuxserver/radarr:latest"]
    mock_container.attrs = {"State": {"Health": {"Status": "healthy"}}}

    # Parse and update state
    info = parse_container(mock_container)
    state.update(info)

    # Verify state updated
    result = state.get("radarr")
    assert result is not None
    assert result.status == "running"
    assert result.health == "healthy"
```

**Step 2: Run integration test**

Run: `pytest tests/test_integration.py -v`
Expected: All tests PASS

**Step 3: Run all tests**

Run: `pytest -v`
Expected: All tests PASS

**Step 4: Commit**

```bash
git add tests/test_integration.py
git commit -m "test: add integration tests"
```

---

## Task 16: Final Verification

**Step 1: Run full test suite**

Run: `pytest -v --tb=short`
Expected: All tests pass

**Step 2: Check types (optional)**

Run: `pip install mypy && mypy src/`
Expected: No errors (or only minor issues)

**Step 3: Tag release**

```bash
git tag -a v0.1.0 -m "Phase 1 MVP: Basic status bot"
```

---

## Deployment Instructions

1. Copy project to Unraid server
2. Create `.env` file with your tokens:
   ```bash
   TELEGRAM_BOT_TOKEN=your_token
   TELEGRAM_ALLOWED_USERS=your_user_id
   ```
3. Build and run:
   ```bash
   docker-compose up -d
   ```
4. Test by sending `/status` to your bot

---

## Success Criteria Checklist

- [ ] Bot responds to `/status` with container counts
- [ ] `/status <name>` shows specific container details
- [ ] Partial name matching works (`/status rad` → radarr)
- [ ] Unauthorized users get no response
- [ ] Container state updates when containers start/stop
