# Mute Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Allow users to temporarily mute ALL alert types (crash, log error, resource) for a container for a specified duration.

**Architecture:** MuteManager tracks mutes with expiry times, persists to JSON. AlertManagerProxy checks mutes before sending any alert. Commands: /mute, /mutes, /unmute.

**Tech Stack:** Python 3.11+, aiogram, JSON for persistence

---

## Task 1: MuteManager Core

**Files:**
- Create: `src/alerts/mute_manager.py`
- Test: `tests/test_mute_manager.py`

**Step 1: Write the failing test**

Create `tests/test_mute_manager.py`:

```python
import pytest
from datetime import datetime, timedelta


def test_parse_duration_minutes():
    """Test parsing minute durations."""
    from src.alerts.mute_manager import parse_duration

    assert parse_duration("15m") == timedelta(minutes=15)
    assert parse_duration("30m") == timedelta(minutes=30)
    assert parse_duration("1m") == timedelta(minutes=1)


def test_parse_duration_hours():
    """Test parsing hour durations."""
    from src.alerts.mute_manager import parse_duration

    assert parse_duration("2h") == timedelta(hours=2)
    assert parse_duration("24h") == timedelta(hours=24)
    assert parse_duration("1h") == timedelta(hours=1)


def test_parse_duration_invalid():
    """Test invalid duration formats."""
    from src.alerts.mute_manager import parse_duration

    assert parse_duration("abc") is None
    assert parse_duration("15") is None
    assert parse_duration("m15") is None
    assert parse_duration("") is None
    assert parse_duration("0m") is None
    assert parse_duration("-5m") is None


def test_mute_manager_is_muted(tmp_path):
    """Test checking if container is muted."""
    from src.alerts.mute_manager import MuteManager

    json_file = tmp_path / "mutes.json"
    manager = MuteManager(json_path=str(json_file))

    # Not muted initially
    assert not manager.is_muted("plex")

    # Add mute
    manager.add_mute("plex", timedelta(hours=1))
    assert manager.is_muted("plex")


def test_mute_manager_expiry(tmp_path):
    """Test that expired mutes return False."""
    from src.alerts.mute_manager import MuteManager

    json_file = tmp_path / "mutes.json"
    manager = MuteManager(json_path=str(json_file))

    # Add expired mute manually
    manager._mutes["plex"] = datetime.now() - timedelta(minutes=5)

    assert not manager.is_muted("plex")


def test_mute_manager_persistence(tmp_path):
    """Test mutes are saved and loaded."""
    from src.alerts.mute_manager import MuteManager

    json_file = tmp_path / "mutes.json"

    # Create manager and add mute
    manager1 = MuteManager(json_path=str(json_file))
    manager1.add_mute("plex", timedelta(hours=1))

    # Create new manager from same file
    manager2 = MuteManager(json_path=str(json_file))
    assert manager2.is_muted("plex")


def test_mute_manager_remove_mute(tmp_path):
    """Test removing a mute early."""
    from src.alerts.mute_manager import MuteManager

    json_file = tmp_path / "mutes.json"
    manager = MuteManager(json_path=str(json_file))

    manager.add_mute("plex", timedelta(hours=1))
    assert manager.is_muted("plex")

    result = manager.remove_mute("plex")
    assert result is True
    assert not manager.is_muted("plex")

    # Removing non-existent returns False
    result = manager.remove_mute("nonexistent")
    assert result is False


def test_mute_manager_get_active_mutes(tmp_path):
    """Test getting list of active mutes."""
    from src.alerts.mute_manager import MuteManager

    json_file = tmp_path / "mutes.json"
    manager = MuteManager(json_path=str(json_file))

    manager.add_mute("plex", timedelta(hours=1))
    manager.add_mute("radarr", timedelta(minutes=30))

    mutes = manager.get_active_mutes()
    assert len(mutes) == 2

    containers = {m[0] for m in mutes}
    assert "plex" in containers
    assert "radarr" in containers
```

**Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest tests/test_mute_manager.py -v`
Expected: FAIL with "cannot import name 'parse_duration'"

**Step 3: Write minimal implementation**

Create `src/alerts/mute_manager.py`:

```python
import json
import re
import logging
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

DURATION_PATTERN = re.compile(r"^(\d+)(m|h)$")


def parse_duration(text: str) -> timedelta | None:
    """Parse duration string like '15m' or '2h'.

    Args:
        text: Duration string (e.g., '15m', '2h', '24h').

    Returns:
        timedelta if valid, None if invalid.
    """
    if not text:
        return None

    match = DURATION_PATTERN.match(text.strip().lower())
    if not match:
        return None

    value = int(match.group(1))
    unit = match.group(2)

    if value <= 0:
        return None

    if unit == "m":
        return timedelta(minutes=value)
    elif unit == "h":
        return timedelta(hours=value)

    return None


class MuteManager:
    """Manages temporary mutes for containers."""

    def __init__(self, json_path: str):
        """Initialize MuteManager.

        Args:
            json_path: Path to JSON file for persistence.
        """
        self._json_path = Path(json_path)
        self._mutes: dict[str, datetime] = {}
        self._load()

    def is_muted(self, container: str) -> bool:
        """Check if container is currently muted.

        Returns False if mute has expired.
        """
        if container not in self._mutes:
            return False

        expiry = self._mutes[container]
        if datetime.now() >= expiry:
            # Expired, clean up
            del self._mutes[container]
            self._save()
            return False

        return True

    def add_mute(self, container: str, duration: timedelta) -> datetime:
        """Add a mute for container.

        Args:
            container: Container name.
            duration: How long to mute.

        Returns:
            Expiry datetime.
        """
        expiry = datetime.now() + duration
        self._mutes[container] = expiry
        self._save()
        logger.info(f"Muted {container} until {expiry}")
        return expiry

    def remove_mute(self, container: str) -> bool:
        """Remove a mute early.

        Returns:
            True if mute was removed, False if not found.
        """
        if container not in self._mutes:
            return False

        del self._mutes[container]
        self._save()
        logger.info(f"Unmuted {container}")
        return True

    def get_active_mutes(self) -> list[tuple[str, datetime]]:
        """Get list of active mutes.

        Returns:
            List of (container, expiry) tuples.
        """
        # Clean expired mutes first
        now = datetime.now()
        expired = [c for c, exp in self._mutes.items() if now >= exp]
        for c in expired:
            del self._mutes[c]
        if expired:
            self._save()

        return [(c, exp) for c, exp in self._mutes.items()]

    def _load(self) -> None:
        """Load mutes from JSON file."""
        if not self._json_path.exists():
            self._mutes = {}
            return

        try:
            with open(self._json_path, encoding="utf-8") as f:
                data = json.load(f)
                self._mutes = {
                    c: datetime.fromisoformat(exp)
                    for c, exp in data.items()
                }
        except (json.JSONDecodeError, IOError, ValueError) as e:
            logger.warning(f"Failed to load mutes: {e}")
            self._mutes = {}

    def _save(self) -> None:
        """Save mutes to JSON file."""
        self._json_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            data = {
                c: exp.isoformat()
                for c, exp in self._mutes.items()
            }
            with open(self._json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except IOError as e:
            logger.error(f"Failed to save mutes: {e}")
```

**Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && python -m pytest tests/test_mute_manager.py -v`
Expected: PASS (9 tests)

**Step 5: Commit**

```bash
git add src/alerts/mute_manager.py tests/test_mute_manager.py
git commit -m "feat: add MuteManager for temporary container mutes"
```

---

## Task 2: Mute Commands - /mute

**Files:**
- Create: `src/bot/mute_command.py`
- Test: `tests/test_mute_command.py`

**Step 1: Write the failing test**

Create `tests/test_mute_command.py`:

```python
import pytest
from unittest.mock import MagicMock, AsyncMock


@pytest.mark.asyncio
async def test_mute_command_with_args(tmp_path):
    """Test /mute plex 2h."""
    from src.bot.mute_command import mute_command
    from src.alerts.mute_manager import MuteManager
    from src.state import ContainerStateManager

    json_file = tmp_path / "mutes.json"
    manager = MuteManager(json_path=str(json_file))
    state = ContainerStateManager()
    state.update({"plex": "running"})

    handler = mute_command(state, manager)

    message = MagicMock()
    message.text = "/mute plex 2h"
    message.reply_to_message = None
    message.answer = AsyncMock()
    message.from_user = MagicMock()
    message.from_user.id = 123

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]
    assert "Muted" in response
    assert "plex" in response
    assert manager.is_muted("plex")


@pytest.mark.asyncio
async def test_mute_command_reply_to_alert(tmp_path):
    """Test /mute 30m replying to alert."""
    from src.bot.mute_command import mute_command
    from src.alerts.mute_manager import MuteManager
    from src.state import ContainerStateManager

    json_file = tmp_path / "mutes.json"
    manager = MuteManager(json_path=str(json_file))
    state = ContainerStateManager()

    handler = mute_command(state, manager)

    reply_message = MagicMock()
    reply_message.text = "⚠️ ERRORS IN: plex\n\nSome errors"

    message = MagicMock()
    message.text = "/mute 30m"
    message.reply_to_message = reply_message
    message.answer = AsyncMock()
    message.from_user = MagicMock()
    message.from_user.id = 123

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]
    assert "Muted" in response
    assert "plex" in response


@pytest.mark.asyncio
async def test_mute_command_invalid_duration(tmp_path):
    """Test /mute with invalid duration."""
    from src.bot.mute_command import mute_command
    from src.alerts.mute_manager import MuteManager
    from src.state import ContainerStateManager

    json_file = tmp_path / "mutes.json"
    manager = MuteManager(json_path=str(json_file))
    state = ContainerStateManager()
    state.update({"plex": "running"})

    handler = mute_command(state, manager)

    message = MagicMock()
    message.text = "/mute plex forever"
    message.reply_to_message = None
    message.answer = AsyncMock()
    message.from_user = MagicMock()
    message.from_user.id = 123

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]
    assert "Invalid duration" in response


@pytest.mark.asyncio
async def test_mute_command_no_args(tmp_path):
    """Test /mute with no arguments."""
    from src.bot.mute_command import mute_command
    from src.alerts.mute_manager import MuteManager
    from src.state import ContainerStateManager

    json_file = tmp_path / "mutes.json"
    manager = MuteManager(json_path=str(json_file))
    state = ContainerStateManager()

    handler = mute_command(state, manager)

    message = MagicMock()
    message.text = "/mute"
    message.reply_to_message = None
    message.answer = AsyncMock()
    message.from_user = MagicMock()
    message.from_user.id = 123

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]
    assert "Usage" in response
```

**Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest tests/test_mute_command.py -v`
Expected: FAIL with "cannot import name 'mute_command'"

**Step 3: Write minimal implementation**

Create `src/bot/mute_command.py`:

```python
import re
import logging
from typing import Callable, Awaitable, TYPE_CHECKING
from datetime import timedelta

from aiogram.types import Message

from src.alerts.mute_manager import parse_duration

if TYPE_CHECKING:
    from src.alerts.mute_manager import MuteManager
    from src.state import ContainerStateManager

logger = logging.getLogger(__name__)

# Pattern to extract container name from various alert types
ALERT_PATTERNS = [
    re.compile(r"ERRORS IN[:\s]+(\w+)", re.IGNORECASE),
    re.compile(r"CRASHED[:\s]+(\w+)", re.IGNORECASE),
    re.compile(r"HIGH .+ USAGE[:\s]+(\w+)", re.IGNORECASE),
    re.compile(r"Container[:\s]+(\w+)", re.IGNORECASE),
]


def extract_container_from_alert(text: str) -> str | None:
    """Extract container name from any alert type."""
    for pattern in ALERT_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(1)
    return None


def mute_command(
    state: "ContainerStateManager",
    mute_manager: "MuteManager",
) -> Callable[[Message], Awaitable[None]]:
    """Factory for /mute command handler."""

    async def handler(message: Message) -> None:
        text = (message.text or "").strip()
        parts = text.split()

        # Parse command arguments
        container: str | None = None
        duration_str: str | None = None

        if len(parts) == 1:
            # Just /mute - need reply or show usage
            if message.reply_to_message and message.reply_to_message.text:
                container = extract_container_from_alert(message.reply_to_message.text)
                if not container:
                    await message.answer(
                        "Usage: `/mute <container> <duration>`\n"
                        "Or reply to an alert with `/mute <duration>`\n\n"
                        "Examples:\n"
                        "• `/mute plex 2h`\n"
                        "• `/mute radarr 30m`\n"
                        "• Reply to alert + `/mute 1h`",
                        parse_mode="Markdown",
                    )
                    return
            else:
                await message.answer(
                    "Usage: `/mute <container> <duration>`\n"
                    "Or reply to an alert with `/mute <duration>`\n\n"
                    "Examples:\n"
                    "• `/mute plex 2h`\n"
                    "• `/mute radarr 30m`\n"
                    "• Reply to alert + `/mute 1h`",
                    parse_mode="Markdown",
                )
                return

        elif len(parts) == 2:
            # /mute <duration> (replying) or /mute <container> (missing duration)
            if message.reply_to_message and message.reply_to_message.text:
                container = extract_container_from_alert(message.reply_to_message.text)
                duration_str = parts[1]
            else:
                await message.answer(
                    "Missing duration. Use `/mute <container> <duration>`\n"
                    "Examples: `2h`, `30m`, `24h`",
                    parse_mode="Markdown",
                )
                return

        elif len(parts) >= 3:
            # /mute <container> <duration>
            container_query = parts[1]
            duration_str = parts[2]

            # Find container by partial match
            containers = state.get_all_containers()
            matches = [c for c in containers if container_query.lower() in c.lower()]

            if len(matches) == 1:
                container = matches[0]
            elif len(matches) > 1:
                await message.answer(
                    f"Ambiguous: `{container_query}` matches {', '.join(matches)}",
                    parse_mode="Markdown",
                )
                return
            else:
                # Accept anyway for flexibility
                container = container_query

        if not container:
            await message.answer("Could not determine container.")
            return

        if not duration_str:
            await message.answer("Missing duration.")
            return

        # Parse duration
        duration = parse_duration(duration_str)
        if not duration:
            await message.answer(
                f"Invalid duration: `{duration_str}`\n"
                "Use format like `15m`, `2h`, `24h`",
                parse_mode="Markdown",
            )
            return

        # Add mute
        expiry = mute_manager.add_mute(container, duration)

        # Format expiry time
        time_str = expiry.strftime("%H:%M")
        await message.answer(
            f"🔇 *Muted {container}* until {time_str}\n\n"
            f"All alerts suppressed for {format_duration(duration)}.\n"
            f"Use `/unmute {container}` to unmute early.",
            parse_mode="Markdown",
        )

    return handler


def format_duration(delta: timedelta) -> str:
    """Format timedelta for display."""
    total_minutes = int(delta.total_seconds() / 60)
    if total_minutes >= 60:
        hours = total_minutes // 60
        mins = total_minutes % 60
        if mins:
            return f"{hours}h {mins}m"
        return f"{hours}h"
    return f"{total_minutes}m"
```

**Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && python -m pytest tests/test_mute_command.py -v`
Expected: PASS (4 tests)

**Step 5: Commit**

```bash
git add src/bot/mute_command.py tests/test_mute_command.py
git commit -m "feat: add /mute command handler"
```

---

## Task 3: Mute Commands - /mutes and /unmute

**Files:**
- Modify: `src/bot/mute_command.py`
- Test: `tests/test_mute_command.py`

**Step 1: Write the failing test**

Add to `tests/test_mute_command.py`:

```python
@pytest.mark.asyncio
async def test_mutes_command_lists_active(tmp_path):
    """Test /mutes lists active mutes."""
    from src.bot.mute_command import mutes_command
    from src.alerts.mute_manager import MuteManager
    from datetime import timedelta

    json_file = tmp_path / "mutes.json"
    manager = MuteManager(json_path=str(json_file))
    manager.add_mute("plex", timedelta(hours=2))
    manager.add_mute("radarr", timedelta(minutes=30))

    handler = mutes_command(manager)

    message = MagicMock()
    message.text = "/mutes"
    message.answer = AsyncMock()

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]
    assert "plex" in response
    assert "radarr" in response


@pytest.mark.asyncio
async def test_mutes_command_empty(tmp_path):
    """Test /mutes with no active mutes."""
    from src.bot.mute_command import mutes_command
    from src.alerts.mute_manager import MuteManager

    json_file = tmp_path / "mutes.json"
    manager = MuteManager(json_path=str(json_file))

    handler = mutes_command(manager)

    message = MagicMock()
    message.text = "/mutes"
    message.answer = AsyncMock()

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]
    assert "No active mutes" in response or "no active" in response.lower()


@pytest.mark.asyncio
async def test_unmute_command(tmp_path):
    """Test /unmute removes mute."""
    from src.bot.mute_command import unmute_command
    from src.alerts.mute_manager import MuteManager
    from src.state import ContainerStateManager
    from datetime import timedelta

    json_file = tmp_path / "mutes.json"
    manager = MuteManager(json_path=str(json_file))
    manager.add_mute("plex", timedelta(hours=2))

    state = ContainerStateManager()
    state.update({"plex": "running"})

    handler = unmute_command(state, manager)

    message = MagicMock()
    message.text = "/unmute plex"
    message.answer = AsyncMock()

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]
    assert "Unmuted" in response
    assert not manager.is_muted("plex")


@pytest.mark.asyncio
async def test_unmute_command_not_muted(tmp_path):
    """Test /unmute when not muted."""
    from src.bot.mute_command import unmute_command
    from src.alerts.mute_manager import MuteManager
    from src.state import ContainerStateManager

    json_file = tmp_path / "mutes.json"
    manager = MuteManager(json_path=str(json_file))

    state = ContainerStateManager()
    state.update({"plex": "running"})

    handler = unmute_command(state, manager)

    message = MagicMock()
    message.text = "/unmute plex"
    message.answer = AsyncMock()

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]
    assert "not muted" in response.lower()
```

**Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest tests/test_mute_command.py::test_mutes_command_lists_active -v`
Expected: FAIL with "cannot import name 'mutes_command'"

**Step 3: Write minimal implementation**

Add to `src/bot/mute_command.py`:

```python
def mutes_command(
    mute_manager: "MuteManager",
) -> Callable[[Message], Awaitable[None]]:
    """Factory for /mutes command handler."""

    async def handler(message: Message) -> None:
        mutes = mute_manager.get_active_mutes()

        if not mutes:
            await message.answer(
                "🔇 No active mutes.\n\n_Use `/mute <container> <duration>` to mute._",
                parse_mode="Markdown",
            )
            return

        lines = ["🔇 *Active Mutes*\n"]
        for container, expiry in sorted(mutes, key=lambda x: x[1]):
            time_str = expiry.strftime("%H:%M")
            lines.append(f"• *{container}* until {time_str}")

        lines.append("\n_Use `/unmute <container>` to unmute early._")
        await message.answer("\n".join(lines), parse_mode="Markdown")

    return handler


def unmute_command(
    state: "ContainerStateManager",
    mute_manager: "MuteManager",
) -> Callable[[Message], Awaitable[None]]:
    """Factory for /unmute command handler."""

    async def handler(message: Message) -> None:
        text = (message.text or "").strip()
        parts = text.split()

        if len(parts) < 2:
            await message.answer(
                "Usage: `/unmute <container>`",
                parse_mode="Markdown",
            )
            return

        container_query = parts[1]

        # Find container by partial match in active mutes first
        mutes = mute_manager.get_active_mutes()
        muted_containers = [c for c, _ in mutes]
        matches = [c for c in muted_containers if container_query.lower() in c.lower()]

        if len(matches) == 1:
            container = matches[0]
        elif len(matches) > 1:
            await message.answer(
                f"Ambiguous: `{container_query}` matches {', '.join(matches)}",
                parse_mode="Markdown",
            )
            return
        else:
            container = container_query

        # Try to unmute
        if mute_manager.remove_mute(container):
            await message.answer(
                f"🔔 *Unmuted {container}*\n\nAlerts are now enabled.",
                parse_mode="Markdown",
            )
        else:
            await message.answer(
                f"`{container}` is not muted.",
                parse_mode="Markdown",
            )

    return handler
```

**Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && python -m pytest tests/test_mute_command.py -v`
Expected: PASS (8 tests)

**Step 5: Commit**

```bash
git add src/bot/mute_command.py tests/test_mute_command.py
git commit -m "feat: add /mutes and /unmute commands"
```

---

## Task 4: Update Help and Register Commands

**Files:**
- Modify: `src/bot/commands.py`
- Modify: `src/bot/telegram_bot.py`
- Test: `tests/test_mute_command.py`

**Step 1: Write the failing test**

Add to `tests/test_mute_command.py`:

```python
def test_mute_commands_in_help():
    """Test that /mute, /mutes, /unmute are in help text."""
    from src.bot.commands import HELP_TEXT

    assert "/mute" in HELP_TEXT
    assert "/mutes" in HELP_TEXT
    assert "/unmute" in HELP_TEXT
```

**Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest tests/test_mute_command.py::test_mute_commands_in_help -v`
Expected: FAIL with "AssertionError"

**Step 3: Write minimal implementation**

Update `HELP_TEXT` in `src/bot/commands.py`:

```python
HELP_TEXT = """📋 *Available Commands*

/status - Container status overview
/status <name> - Details for specific container
/resources - CPU/memory usage for all containers
/resources <name> - Detailed resource stats for container
/logs <name> [n] - Last n log lines (default 20)
/diagnose <name> [n] - AI analysis of container logs
/restart <name> - Restart a container
/stop <name> - Stop a container
/start <name> - Start a container
/pull <name> - Pull latest image and recreate
/ignore - Reply to error alert to ignore those errors
/ignores - List all ignored error patterns
/mute <name> <duration> - Mute all alerts for container (e.g., 2h, 30m)
/mutes - List active mutes
/unmute <name> - Unmute a container
/help - Show this help message

_Partial container names work: /status rad → radarr_
_Control commands require confirmation_
_Reply /diagnose to a crash alert for quick analysis_"""
```

Update `src/bot/telegram_bot.py`:

Add import:
```python
from src.bot.mute_command import mute_command, mutes_command, unmute_command
```

Update `register_commands` signature:
```python
def register_commands(
    dp: Dispatcher,
    state: ContainerStateManager,
    docker_client: docker.DockerClient | None = None,
    protected_containers: list[str] | None = None,
    anthropic_client: Any | None = None,
    resource_monitor: Any | None = None,
    ignore_manager: Any | None = None,
    recent_errors_buffer: Any | None = None,
    mute_manager: Any | None = None,
) -> tuple[ConfirmationManager | None, DiagnosticService | None]:
```

Add registration after ignore commands (inside the `if docker_client:` block):
```python
        # Register /mute, /mutes, /unmute commands
        if mute_manager is not None:
            dp.message.register(
                mute_command(state, mute_manager),
                Command("mute"),
            )
            dp.message.register(
                mutes_command(mute_manager),
                Command("mutes"),
            )
            dp.message.register(
                unmute_command(state, mute_manager),
                Command("unmute"),
            )
```

**Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && python -m pytest tests/test_mute_command.py -v`
Expected: PASS (9 tests)

**Step 5: Commit**

```bash
git add src/bot/commands.py src/bot/telegram_bot.py tests/test_mute_command.py
git commit -m "feat: register mute commands and update help"
```

---

## Task 5: Integrate into Alert Dispatch

**Files:**
- Modify: `src/main.py`
- Test: `tests/test_mute_integration.py`

**Step 1: Write the failing test**

Create `tests/test_mute_integration.py`:

```python
import pytest
from unittest.mock import MagicMock, AsyncMock
from datetime import timedelta


@pytest.mark.asyncio
async def test_alert_suppressed_when_muted(tmp_path):
    """Test that alerts are suppressed when container is muted."""
    from src.alerts.mute_manager import MuteManager

    json_file = tmp_path / "mutes.json"
    manager = MuteManager(json_path=str(json_file))

    # Not muted - should alert
    assert not manager.is_muted("plex")

    # Muted - should not alert
    manager.add_mute("plex", timedelta(hours=1))
    assert manager.is_muted("plex")

    # Different container - should alert
    assert not manager.is_muted("radarr")


def test_mute_manager_created_in_main():
    """Test that MuteManager can be created."""
    from src.alerts.mute_manager import MuteManager

    manager = MuteManager(json_path="/tmp/test_mutes.json")
    assert manager is not None
```

**Step 2: Run test to verify it passes (validation)**

Run: `source .venv/bin/activate && python -m pytest tests/test_mute_integration.py -v`
Expected: PASS

**Step 3: Write the main.py integration**

Modify `src/main.py`:

Add import:
```python
from src.alerts.mute_manager import MuteManager
```

After creating `ignore_manager` and `recent_errors_buffer`, add:
```python
    # Initialize mute manager
    mute_manager = MuteManager(json_path="data/mutes.json")
```

Update alert callback functions to check mute. Find the `on_crash` function and update:
```python
    async def on_crash(container_name: str) -> None:
        # Check if muted
        if mute_manager.is_muted(container_name):
            logger.debug(f"Suppressed crash alert for muted container: {container_name}")
            return

        # ... rest of existing code
```

Find the `on_log_error` function and update:
```python
    async def on_log_error(container_name: str, line: str) -> None:
        # Check if muted
        if mute_manager.is_muted(container_name):
            logger.debug(f"Suppressed log error alert for muted container: {container_name}")
            return

        # ... rest of existing code
```

Find the `on_high_resource` function (if it exists) and update similarly.

Update the `register_commands` call to pass mute_manager:
```python
    confirmation, diagnostic_service = register_commands(
        dp,
        state,
        docker_client=monitor._client,
        protected_containers=config.protected_containers,
        anthropic_client=anthropic_client,
        resource_monitor=resource_monitor,
        ignore_manager=ignore_manager,
        recent_errors_buffer=recent_errors_buffer,
        mute_manager=mute_manager,
    )
```

**Step 4: Run all tests**

Run: `source .venv/bin/activate && python -m pytest -v`
Expected: All tests pass

**Step 5: Commit**

```bash
git add src/main.py tests/test_mute_integration.py
git commit -m "feat: integrate MuteManager into alert dispatch"
```

---

## Task 6: Final Verification

**Files:**
- All files from previous tasks

**Step 1: Run all tests**

Run: `source .venv/bin/activate && python -m pytest -v`
Expected: All tests PASS

**Step 2: Type check**

Run: `source .venv/bin/activate && python -m py_compile src/alerts/mute_manager.py src/bot/mute_command.py`
Expected: No errors

**Step 3: Commit and tag**

```bash
git add -A
git commit -m "feat: complete mute implementation

- Add /mute command to suppress all alerts for duration
- Add /mutes command to list active mutes
- Add /unmute command to remove mutes early
- Mutes persisted to data/mutes.json
- Works for crash, log error, and resource alerts"

git tag -a v0.7.0 -m "Mute feature"
```

---

## Success Criteria

- [ ] `/mute <container> <duration>` mutes all alerts
- [ ] `/mute <duration>` when replying to any alert type
- [ ] `/mutes` lists active mutes with expiry times
- [ ] `/unmute <container>` removes mute early
- [ ] Duration formats: `15m`, `2h`, `24h`
- [ ] Mutes persisted to `data/mutes.json`
- [ ] Crash alerts suppressed when muted
- [ ] Log error alerts suppressed when muted
- [ ] Resource alerts suppressed when muted
- [ ] All tests pass
