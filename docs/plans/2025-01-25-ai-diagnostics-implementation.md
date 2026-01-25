# AI Diagnostics Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add `/diagnose` command that uses Claude API to analyze container logs and provide actionable fix suggestions.

**Architecture:** DiagnosticService gathers logs + metadata from Docker, sends to Claude Haiku API, returns brief summary. DetailsManager tracks pending follow-ups per user for "Want more details?" flow.

**Tech Stack:** Python 3.11+, anthropic SDK, aiogram 3.x, docker SDK (existing)

---

## Task 1: Add Anthropic SDK Dependency

**Files:**
- Modify: `requirements.txt`

**Step 1: Add anthropic to requirements**

Update `requirements.txt` to add:

```
anthropic>=0.40.0
```

**Step 2: Install dependency**

Run: `source .venv/bin/activate && pip install anthropic>=0.40.0`
Expected: Successfully installed anthropic

**Step 3: Commit**

```bash
git add requirements.txt
git commit -m "deps: add anthropic SDK for AI diagnostics"
```

---

## Task 2: DiagnosticContext Data Class

**Files:**
- Create: `src/services/diagnostic.py`
- Create: `tests/test_diagnostic.py`

**Step 1: Write the failing test**

Create `tests/test_diagnostic.py`:

```python
import pytest
from datetime import datetime


def test_diagnostic_context_creation():
    """Test DiagnosticContext dataclass creation."""
    from src.services.diagnostic import DiagnosticContext

    context = DiagnosticContext(
        container_name="overseerr",
        logs="Error: connection refused",
        exit_code=1,
        image="linuxserver/overseerr:latest",
        uptime_seconds=3600,
        restart_count=2,
        brief_summary="Container crashed due to database connection failure.",
    )

    assert context.container_name == "overseerr"
    assert context.exit_code == 1
    assert context.restart_count == 2
    assert "database" in context.brief_summary
```

**Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && pytest tests/test_diagnostic.py::test_diagnostic_context_creation -v`
Expected: FAIL with "No module named 'src.services.diagnostic'"

**Step 3: Write implementation**

Create `src/services/diagnostic.py`:

```python
"""AI-powered container diagnostics service."""

import logging
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class DiagnosticContext:
    """Context for a diagnostic request."""

    container_name: str
    logs: str
    exit_code: int | None
    image: str
    uptime_seconds: int | None
    restart_count: int
    brief_summary: str | None = None
    created_at: datetime | None = None

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now()
```

**Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && pytest tests/test_diagnostic.py::test_diagnostic_context_creation -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/services/diagnostic.py tests/test_diagnostic.py
git commit -m "feat: add DiagnosticContext dataclass"
```

---

## Task 3: DiagnosticService - Gather Container Context

**Files:**
- Modify: `src/services/diagnostic.py`
- Modify: `tests/test_diagnostic.py`

**Step 1: Write the failing test**

Add to `tests/test_diagnostic.py`:

```python
from unittest.mock import MagicMock
from datetime import datetime, timezone


def test_diagnostic_service_gathers_context():
    """Test gathering container context from Docker."""
    from src.services.diagnostic import DiagnosticService

    # Mock Docker container
    mock_container = MagicMock()
    mock_container.logs.return_value = b"Error: connection refused\nRetrying..."
    mock_container.attrs = {
        "State": {
            "ExitCode": 1,
            "StartedAt": "2025-01-25T10:00:00Z",
        },
        "RestartCount": 2,
    }
    mock_container.image.tags = ["linuxserver/overseerr:latest"]

    mock_client = MagicMock()
    mock_client.containers.get.return_value = mock_container

    service = DiagnosticService(docker_client=mock_client, anthropic_client=None)

    context = service.gather_context("overseerr", lines=50)

    assert context.container_name == "overseerr"
    assert context.exit_code == 1
    assert context.restart_count == 2
    assert "Error: connection refused" in context.logs
    assert context.image == "linuxserver/overseerr:latest"


def test_diagnostic_service_handles_missing_container():
    """Test handling container not found."""
    import docker
    from src.services.diagnostic import DiagnosticService

    mock_client = MagicMock()
    mock_client.containers.get.side_effect = docker.errors.NotFound("not found")

    service = DiagnosticService(docker_client=mock_client, anthropic_client=None)

    context = service.gather_context("nonexistent", lines=50)

    assert context is None
```

**Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && pytest tests/test_diagnostic.py::test_diagnostic_service_gathers_context -v`
Expected: FAIL with "cannot import name 'DiagnosticService'"

**Step 3: Write implementation**

Add to `src/services/diagnostic.py`:

```python
import docker
from datetime import datetime, timezone


def _parse_docker_timestamp(ts: str) -> datetime | None:
    """Parse Docker timestamp string to datetime."""
    if not ts or ts == "0001-01-01T00:00:00Z":
        return None
    try:
        # Handle Docker's timestamp format
        ts = ts.replace("Z", "+00:00")
        if "." in ts:
            # Truncate nanoseconds to microseconds
            parts = ts.split(".")
            fraction = parts[1].split("+")[0].split("-")[0][:6]
            tz_part = "+" + parts[1].split("+")[1] if "+" in parts[1] else "-" + parts[1].split("-")[1]
            ts = f"{parts[0]}.{fraction}{tz_part}"
        return datetime.fromisoformat(ts)
    except Exception:
        return None


class DiagnosticService:
    """AI-powered container diagnostics."""

    def __init__(self, docker_client: docker.DockerClient, anthropic_client):
        self._docker = docker_client
        self._anthropic = anthropic_client
        self._pending: dict[int, DiagnosticContext] = {}

    def gather_context(self, container_name: str, lines: int = 50) -> DiagnosticContext | None:
        """Gather diagnostic context from a container.

        Args:
            container_name: Name of the container to diagnose.
            lines: Number of log lines to retrieve.

        Returns:
            DiagnosticContext with container info, or None if container not found.
        """
        try:
            container = self._docker.containers.get(container_name)
        except docker.errors.NotFound:
            return None

        # Get logs
        log_bytes = container.logs(tail=lines, timestamps=False)
        logs = log_bytes.decode("utf-8", errors="replace")

        # Get container state
        attrs = container.attrs
        state = attrs.get("State", {})
        exit_code = state.get("ExitCode")
        started_at = _parse_docker_timestamp(state.get("StartedAt", ""))
        restart_count = attrs.get("RestartCount", 0)

        # Calculate uptime
        uptime_seconds = None
        if started_at:
            now = datetime.now(timezone.utc)
            uptime_seconds = int((now - started_at).total_seconds())

        # Get image
        image_tags = container.image.tags
        image = image_tags[0] if image_tags else "unknown"

        return DiagnosticContext(
            container_name=container_name,
            logs=logs,
            exit_code=exit_code,
            image=image,
            uptime_seconds=uptime_seconds,
            restart_count=restart_count,
        )
```

**Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && pytest tests/test_diagnostic.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add src/services/diagnostic.py tests/test_diagnostic.py
git commit -m "feat: add DiagnosticService.gather_context method"
```

---

## Task 4: DiagnosticService - Call Claude API

**Files:**
- Modify: `src/services/diagnostic.py`
- Modify: `tests/test_diagnostic.py`

**Step 1: Write the failing test**

Add to `tests/test_diagnostic.py`:

```python
@pytest.mark.asyncio
async def test_diagnostic_service_analyzes_with_claude():
    """Test calling Claude API for analysis."""
    from src.services.diagnostic import DiagnosticService, DiagnosticContext
    from unittest.mock import AsyncMock

    mock_client = MagicMock()

    # Mock Anthropic client
    mock_anthropic = MagicMock()
    mock_message = MagicMock()
    mock_message.content = [MagicMock(text="The container crashed due to OOM. Increase memory limits.")]
    mock_anthropic.messages.create = MagicMock(return_value=mock_message)

    service = DiagnosticService(docker_client=mock_client, anthropic_client=mock_anthropic)

    context = DiagnosticContext(
        container_name="overseerr",
        logs="Error: JavaScript heap out of memory",
        exit_code=137,
        image="linuxserver/overseerr:latest",
        uptime_seconds=3600,
        restart_count=2,
    )

    result = await service.analyze(context)

    assert "OOM" in result or "memory" in result.lower()
    mock_anthropic.messages.create.assert_called_once()
```

**Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && pytest tests/test_diagnostic.py::test_diagnostic_service_analyzes_with_claude -v`
Expected: FAIL with "'DiagnosticService' object has no attribute 'analyze'"

**Step 3: Write implementation**

Add to `src/services/diagnostic.py` in the `DiagnosticService` class:

```python
    async def analyze(self, context: DiagnosticContext) -> str:
        """Analyze container issue using Claude API.

        Args:
            context: DiagnosticContext with container info.

        Returns:
            Brief analysis summary.
        """
        if not self._anthropic:
            return "❌ Anthropic API not configured. Set ANTHROPIC_API_KEY in .env"

        uptime_str = self._format_uptime(context.uptime_seconds) if context.uptime_seconds else "unknown"

        prompt = f"""You are a Docker container diagnostics assistant. Analyze this container issue and provide a brief, actionable summary.

Container: {context.container_name}
Image: {context.image}
Exit Code: {context.exit_code}
Uptime before exit: {uptime_str}
Restart Count: {context.restart_count}

Last log lines:
```
{context.logs}
```

Respond with 2-3 sentences: What happened, the likely cause, and how to fix it. Be specific and actionable. If you see a clear command to run, include it."""

        try:
            message = self._anthropic.messages.create(
                model="claude-3-haiku-20240307",
                max_tokens=300,
                messages=[{"role": "user", "content": prompt}],
            )
            return message.content[0].text
        except Exception as e:
            logger.error(f"Claude API error: {e}")
            return f"❌ Error analyzing container: {e}"

    def _format_uptime(self, seconds: int) -> str:
        """Format uptime in human-readable form."""
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        if hours > 0:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"
```

**Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && pytest tests/test_diagnostic.py::test_diagnostic_service_analyzes_with_claude -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/services/diagnostic.py tests/test_diagnostic.py
git commit -m "feat: add DiagnosticService.analyze method for Claude API"
```

---

## Task 5: DiagnosticService - Follow-up Details

**Files:**
- Modify: `src/services/diagnostic.py`
- Modify: `tests/test_diagnostic.py`

**Step 1: Write the failing test**

Add to `tests/test_diagnostic.py`:

```python
@pytest.mark.asyncio
async def test_diagnostic_service_stores_and_retrieves_context():
    """Test storing context for follow-up."""
    from src.services.diagnostic import DiagnosticService, DiagnosticContext

    mock_client = MagicMock()
    mock_anthropic = MagicMock()
    mock_message = MagicMock()
    mock_message.content = [MagicMock(text="Detailed analysis: The root cause is...")]
    mock_anthropic.messages.create = MagicMock(return_value=mock_message)

    service = DiagnosticService(docker_client=mock_client, anthropic_client=mock_anthropic)

    context = DiagnosticContext(
        container_name="overseerr",
        logs="Error log",
        exit_code=1,
        image="linuxserver/overseerr:latest",
        uptime_seconds=3600,
        restart_count=0,
        brief_summary="Container crashed.",
    )

    # Store context for user
    service.store_context(user_id=123, context=context)

    # Check pending
    assert service.has_pending(123) is True
    assert service.has_pending(456) is False

    # Get details
    details = await service.get_details(123)

    assert details is not None
    assert "root cause" in details.lower() or "Detailed" in details

    # Context should be cleared after retrieval
    assert service.has_pending(123) is False
```

**Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && pytest tests/test_diagnostic.py::test_diagnostic_service_stores_and_retrieves_context -v`
Expected: FAIL with "'DiagnosticService' object has no attribute 'store_context'"

**Step 3: Write implementation**

Add to `src/services/diagnostic.py` in the `DiagnosticService` class:

```python
    def store_context(self, user_id: int, context: DiagnosticContext) -> None:
        """Store diagnostic context for potential follow-up.

        Args:
            user_id: Telegram user ID.
            context: DiagnosticContext to store.
        """
        self._pending[user_id] = context

    def has_pending(self, user_id: int) -> bool:
        """Check if user has pending diagnostic context.

        Args:
            user_id: Telegram user ID.

        Returns:
            True if user has pending context less than 10 minutes old.
        """
        context = self._pending.get(user_id)
        if context is None:
            return False

        # Check if context is stale (> 10 minutes)
        if context.created_at:
            age = (datetime.now() - context.created_at).total_seconds()
            if age > 600:  # 10 minutes
                del self._pending[user_id]
                return False

        return True

    async def get_details(self, user_id: int) -> str | None:
        """Get detailed analysis for user's pending context.

        Args:
            user_id: Telegram user ID.

        Returns:
            Detailed analysis or None if no pending context.
        """
        if not self.has_pending(user_id):
            return None

        context = self._pending.pop(user_id)

        if not self._anthropic:
            return "❌ Anthropic API not configured."

        prompt = f"""Based on your previous analysis, provide detailed help:

Container: {context.container_name}
Your brief analysis: {context.brief_summary}

Logs:
```
{context.logs}
```

Provide:
1. Detailed root cause analysis
2. Step-by-step fix instructions
3. How to prevent this in future

Be specific and actionable."""

        try:
            message = self._anthropic.messages.create(
                model="claude-3-haiku-20240307",
                max_tokens=800,
                messages=[{"role": "user", "content": prompt}],
            )
            return message.content[0].text
        except Exception as e:
            logger.error(f"Claude API error: {e}")
            return f"❌ Error getting details: {e}"
```

**Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && pytest tests/test_diagnostic.py::test_diagnostic_service_stores_and_retrieves_context -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/services/diagnostic.py tests/test_diagnostic.py
git commit -m "feat: add context storage and detailed follow-up for diagnostics"
```

---

## Task 6: Diagnose Command Handler

**Files:**
- Create: `src/bot/diagnose_command.py`
- Create: `tests/test_diagnose_command.py`

**Step 1: Write the failing test**

Create `tests/test_diagnose_command.py`:

```python
import pytest
from unittest.mock import MagicMock, AsyncMock


@pytest.mark.asyncio
async def test_diagnose_command_with_container_name():
    """Test /diagnose with explicit container name."""
    from src.bot.diagnose_command import diagnose_command
    from src.state import ContainerStateManager
    from src.models import ContainerInfo
    from src.services.diagnostic import DiagnosticService, DiagnosticContext

    state = ContainerStateManager()
    state.update(ContainerInfo("overseerr", "exited", None, "linuxserver/overseerr:latest", None))

    mock_context = DiagnosticContext(
        container_name="overseerr",
        logs="Error log",
        exit_code=1,
        image="linuxserver/overseerr:latest",
        uptime_seconds=3600,
        restart_count=0,
    )

    mock_service = MagicMock(spec=DiagnosticService)
    mock_service.gather_context.return_value = mock_context
    mock_service.analyze = AsyncMock(return_value="Container crashed due to DB error.")

    handler = diagnose_command(state, mock_service)

    message = MagicMock()
    message.text = "/diagnose overseerr"
    message.from_user.id = 123
    message.reply_to_message = None
    message.answer = AsyncMock()

    await handler(message)

    mock_service.gather_context.assert_called_once_with("overseerr", lines=50)
    mock_service.analyze.assert_called_once()
    response = message.answer.call_args[0][0]
    assert "Diagnosis" in response
    assert "DB error" in response or "crashed" in response


@pytest.mark.asyncio
async def test_diagnose_command_container_not_found():
    """Test /diagnose with non-existent container."""
    from src.bot.diagnose_command import diagnose_command
    from src.state import ContainerStateManager
    from src.services.diagnostic import DiagnosticService

    state = ContainerStateManager()

    mock_service = MagicMock(spec=DiagnosticService)

    handler = diagnose_command(state, mock_service)

    message = MagicMock()
    message.text = "/diagnose nonexistent"
    message.from_user.id = 123
    message.reply_to_message = None
    message.answer = AsyncMock()

    await handler(message)

    response = message.answer.call_args[0][0]
    assert "No container found" in response
```

**Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && pytest tests/test_diagnose_command.py::test_diagnose_command_with_container_name -v`
Expected: FAIL with "No module named 'src.bot.diagnose_command'"

**Step 3: Write implementation**

Create `src/bot/diagnose_command.py`:

```python
"""Diagnose command handler for AI-powered container analysis."""

import logging
import re
from typing import Callable, Awaitable

from aiogram.types import Message

from src.state import ContainerStateManager
from src.services.diagnostic import DiagnosticService

logger = logging.getLogger(__name__)

# Pattern to extract container name from crash alert
CRASH_ALERT_PATTERN = re.compile(r"\*CONTAINER CRASHED:\*\s+(\w+)")


def _extract_container_from_reply(reply_message: Message) -> str | None:
    """Extract container name from a crash alert message."""
    if not reply_message or not reply_message.text:
        return None

    match = CRASH_ALERT_PATTERN.search(reply_message.text)
    if match:
        return match.group(1)
    return None


def diagnose_command(
    state: ContainerStateManager,
    diagnostic_service: DiagnosticService,
) -> Callable[[Message], Awaitable[None]]:
    """Factory for /diagnose command handler."""

    async def handler(message: Message) -> None:
        text = message.text or ""
        parts = text.strip().split()
        user_id = message.from_user.id

        container_name = None
        lines = 50

        # Check for explicit container name in command
        if len(parts) >= 2:
            container_name = parts[1]

            # Check for optional line count
            if len(parts) >= 3:
                try:
                    lines = int(parts[2])
                    lines = min(lines, 500)  # Cap at 500
                except ValueError:
                    pass

        # If no container name, try to extract from reply
        if not container_name and message.reply_to_message:
            container_name = _extract_container_from_reply(message.reply_to_message)

        # If still no container name, show usage
        if not container_name:
            await message.answer(
                "Usage: `/diagnose <container> [lines]`\n\n"
                "Or reply to a crash alert with `/diagnose`",
                parse_mode="Markdown",
            )
            return

        # Find container in state
        matches = state.find_by_name(container_name)
        if not matches:
            await message.answer(f"❌ No container found matching '{container_name}'")
            return

        if len(matches) > 1:
            names = ", ".join(m.name for m in matches)
            await message.answer(
                f"Multiple matches found: {names}\n\n_Be more specific_",
                parse_mode="Markdown",
            )
            return

        actual_name = matches[0].name

        await message.answer(f"🔍 Analyzing {actual_name}...")

        # Gather context
        context = diagnostic_service.gather_context(actual_name, lines=lines)
        if not context:
            await message.answer(f"❌ Could not get container info for '{actual_name}'")
            return

        # Analyze with Claude
        analysis = await diagnostic_service.analyze(context)

        # Store context for follow-up
        context.brief_summary = analysis
        diagnostic_service.store_context(user_id, context)

        response = f"""🔍 *Diagnosis: {actual_name}*

{analysis}

_Want more details?_"""

        await message.answer(response, parse_mode="Markdown")

    return handler
```

**Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && pytest tests/test_diagnose_command.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add src/bot/diagnose_command.py tests/test_diagnose_command.py
git commit -m "feat: add /diagnose command handler"
```

---

## Task 7: Details Filter and Handler

**Files:**
- Modify: `src/bot/telegram_bot.py`
- Create: `tests/test_details_handler.py`

**Step 1: Write the failing test**

Create `tests/test_details_handler.py`:

```python
import pytest
from unittest.mock import MagicMock, AsyncMock


@pytest.mark.asyncio
async def test_details_filter_matches_yes():
    """Test DetailsFilter matches 'yes' and variants."""
    from src.bot.telegram_bot import DetailsFilter
    from aiogram.types import Message

    filter_instance = DetailsFilter()

    for text in ["yes", "Yes", "YES", "more", "details", "More Details", " yes "]:
        message = MagicMock(spec=Message)
        message.text = text
        result = await filter_instance(message)
        assert result is True, f"Expected True for '{text}'"

    for text in ["no", "help", "/status", "yess", None]:
        message = MagicMock(spec=Message)
        message.text = text
        result = await filter_instance(message)
        assert result is False, f"Expected False for '{text}'"


@pytest.mark.asyncio
async def test_details_handler_returns_detailed_analysis():
    """Test details handler returns detailed analysis."""
    from src.bot.telegram_bot import create_details_handler
    from src.services.diagnostic import DiagnosticService

    mock_service = MagicMock(spec=DiagnosticService)
    mock_service.has_pending.return_value = True
    mock_service.get_details = AsyncMock(return_value="Detailed analysis: root cause is...")

    handler = create_details_handler(mock_service)

    message = MagicMock()
    message.text = "yes"
    message.from_user.id = 123
    message.answer = AsyncMock()

    await handler(message)

    mock_service.get_details.assert_called_once_with(123)
    response = message.answer.call_args[0][0]
    assert "Detailed" in response


@pytest.mark.asyncio
async def test_details_handler_ignores_when_no_pending():
    """Test details handler ignores when no pending context."""
    from src.bot.telegram_bot import create_details_handler
    from src.services.diagnostic import DiagnosticService

    mock_service = MagicMock(spec=DiagnosticService)
    mock_service.has_pending.return_value = False

    handler = create_details_handler(mock_service)

    message = MagicMock()
    message.text = "yes"
    message.from_user.id = 123
    message.answer = AsyncMock()

    await handler(message)

    # Should not respond when no pending context
    message.answer.assert_not_called()
```

**Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && pytest tests/test_details_handler.py::test_details_filter_matches_yes -v`
Expected: FAIL with "cannot import name 'DetailsFilter'"

**Step 3: Write implementation**

Add to `src/bot/telegram_bot.py`:

After the existing imports, add:

```python
from src.services.diagnostic import DiagnosticService
```

After the `YesFilter` class, add:

```python
class DetailsFilter(Filter):
    """Filter for 'yes', 'more', 'details' follow-up messages."""

    TRIGGERS = {"yes", "more", "details", "more details", "tell me more", "expand"}

    async def __call__(self, message: Message) -> bool:
        if not message.text:
            return False
        return message.text.strip().lower() in self.TRIGGERS


def create_details_handler(
    diagnostic_service: DiagnosticService,
) -> Callable[[Message], Awaitable[None]]:
    """Factory for details follow-up handler."""

    async def handler(message: Message) -> None:
        user_id = message.from_user.id

        if not diagnostic_service.has_pending(user_id):
            # No pending context - don't respond (might be unrelated)
            return

        details = await diagnostic_service.get_details(user_id)
        if details:
            response = f"📋 *Detailed Analysis*\n\n{details}"
            await message.answer(response, parse_mode="Markdown")

    return handler
```

Also add the import at the top:

```python
from typing import Any, Awaitable, Callable
```

**Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && pytest tests/test_details_handler.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add src/bot/telegram_bot.py tests/test_details_handler.py
git commit -m "feat: add DetailsFilter and handler for follow-up analysis"
```

---

## Task 8: Register Diagnose Command and Update Help

**Files:**
- Modify: `src/bot/telegram_bot.py`
- Modify: `src/bot/commands.py`

**Step 1: Update HELP_TEXT**

In `src/bot/commands.py`, update `HELP_TEXT`:

```python
HELP_TEXT = """📋 *Available Commands*

/status - Container status overview
/status <name> - Details for specific container
/logs <name> [n] - Last n log lines (default 20)
/diagnose <name> [n] - AI analysis of container logs
/restart <name> - Restart a container
/stop <name> - Stop a container
/start <name> - Start a container
/pull <name> - Pull latest image and recreate
/help - Show this help message

_Partial container names work: /status rad → radarr_
_Control commands require confirmation_
_Reply /diagnose to a crash alert for quick analysis_"""
```

**Step 2: Update register_commands**

In `src/bot/telegram_bot.py`, update the imports:

```python
from src.bot.diagnose_command import diagnose_command
```

Update the `register_commands` function signature:

```python
def register_commands(
    dp: Dispatcher,
    state: ContainerStateManager,
    docker_client: docker.DockerClient | None = None,
    protected_containers: list[str] | None = None,
    anthropic_client=None,
) -> tuple[ConfirmationManager | None, DiagnosticService | None]:
```

Add inside the `if docker_client:` block, after the control commands registration:

```python
        # Set up diagnostic service
        diagnostic_service = DiagnosticService(docker_client, anthropic_client)

        dp.message.register(
            diagnose_command(state, diagnostic_service),
            Command("diagnose"),
        )

        # Register details follow-up handler
        dp.message.register(
            create_details_handler(diagnostic_service),
            DetailsFilter(),
        )
```

Update the return statement:

```python
    return confirmation, diagnostic_service if docker_client else None
```

And at the end of the function when no docker_client:

```python
    return None, None
```

**Step 3: Run all tests**

Run: `source .venv/bin/activate && pytest --tb=short`
Expected: All tests PASS

**Step 4: Commit**

```bash
git add src/bot/telegram_bot.py src/bot/commands.py
git commit -m "feat: register /diagnose command and update help text"
```

---

## Task 9: Main Integration

**Files:**
- Modify: `src/main.py`

**Step 1: Update main.py**

Add import near the top:

```python
import anthropic
```

After `config = AppConfig(settings)`, add:

```python
    # Initialize Anthropic client if API key is configured
    anthropic_client = None
    if config.anthropic_api_key:
        anthropic_client = anthropic.Anthropic(api_key=config.anthropic_api_key)
        logger.info("Anthropic client initialized for AI diagnostics")
    else:
        logger.warning("ANTHROPIC_API_KEY not set - /diagnose command will be disabled")
```

Update the `register_commands` call:

```python
    confirmation, diagnostic_service = register_commands(
        dp,
        state,
        docker_client=monitor._client,
        protected_containers=config.protected_containers,
        anthropic_client=anthropic_client,
    )
```

**Step 2: Run all tests**

Run: `source .venv/bin/activate && pytest --tb=short`
Expected: All tests PASS

**Step 3: Commit**

```bash
git add src/main.py
git commit -m "feat: integrate Anthropic client for AI diagnostics"
```

---

## Task 10: Integration Tests

**Files:**
- Create: `tests/test_diagnose_integration.py`

**Step 1: Write integration tests**

Create `tests/test_diagnose_integration.py`:

```python
"""Integration tests for AI diagnostics feature."""

import pytest
from unittest.mock import MagicMock, AsyncMock


@pytest.mark.asyncio
async def test_full_diagnose_flow():
    """Test full diagnose flow: command -> analysis -> follow-up."""
    from src.state import ContainerStateManager
    from src.models import ContainerInfo
    from src.bot.diagnose_command import diagnose_command
    from src.bot.telegram_bot import create_details_handler
    from src.services.diagnostic import DiagnosticService, DiagnosticContext

    # Setup
    state = ContainerStateManager()
    state.update(ContainerInfo("overseerr", "exited", None, "linuxserver/overseerr:latest", None))

    mock_container = MagicMock()
    mock_container.logs.return_value = b"Error: SQLITE_BUSY"
    mock_container.attrs = {"State": {"ExitCode": 1, "StartedAt": ""}, "RestartCount": 0}
    mock_container.image.tags = ["linuxserver/overseerr:latest"]

    mock_docker = MagicMock()
    mock_docker.containers.get.return_value = mock_container

    mock_anthropic = MagicMock()
    mock_message = MagicMock()
    mock_message.content = [MagicMock(text="Database locked. Restart MariaDB.")]
    mock_anthropic.messages.create = MagicMock(return_value=mock_message)

    service = DiagnosticService(mock_docker, mock_anthropic)

    # Step 1: User sends /diagnose overseerr
    diagnose_handler = diagnose_command(state, service)
    msg1 = MagicMock()
    msg1.text = "/diagnose overseerr"
    msg1.from_user.id = 123
    msg1.reply_to_message = None
    msg1.answer = AsyncMock()

    await diagnose_handler(msg1)

    # Should show brief analysis
    response1 = msg1.answer.call_args_list[-1][0][0]
    assert "Diagnosis" in response1
    assert "Want more details" in response1

    # Step 2: User sends "yes"
    mock_anthropic.messages.create.return_value.content = [
        MagicMock(text="Detailed: The root cause is SQLite database locking...")
    ]

    details_handler = create_details_handler(service)
    msg2 = MagicMock()
    msg2.text = "yes"
    msg2.from_user.id = 123
    msg2.answer = AsyncMock()

    await details_handler(msg2)

    # Should show detailed analysis
    response2 = msg2.answer.call_args[0][0]
    assert "Detailed" in response2


@pytest.mark.asyncio
async def test_diagnose_reply_to_crash_alert():
    """Test replying /diagnose to a crash alert."""
    from src.state import ContainerStateManager
    from src.models import ContainerInfo
    from src.bot.diagnose_command import diagnose_command
    from src.services.diagnostic import DiagnosticService

    state = ContainerStateManager()
    state.update(ContainerInfo("overseerr", "exited", None, "linuxserver/overseerr:latest", None))

    mock_container = MagicMock()
    mock_container.logs.return_value = b"Error: crash"
    mock_container.attrs = {"State": {"ExitCode": 1, "StartedAt": ""}, "RestartCount": 0}
    mock_container.image.tags = ["linuxserver/overseerr:latest"]

    mock_docker = MagicMock()
    mock_docker.containers.get.return_value = mock_container

    mock_anthropic = MagicMock()
    mock_message = MagicMock()
    mock_message.content = [MagicMock(text="Analysis result.")]
    mock_anthropic.messages.create = MagicMock(return_value=mock_message)

    service = DiagnosticService(mock_docker, mock_anthropic)

    handler = diagnose_command(state, service)

    # Simulate reply to crash alert
    reply_msg = MagicMock()
    reply_msg.text = """🔴 *CONTAINER CRASHED:* overseerr

Exit code: 1
Image: linuxserver/overseerr:latest"""

    message = MagicMock()
    message.text = "/diagnose"
    message.from_user.id = 123
    message.reply_to_message = reply_msg
    message.answer = AsyncMock()

    await handler(message)

    # Should extract container from reply and analyze
    response = message.answer.call_args_list[-1][0][0]
    assert "Diagnosis" in response
    assert "overseerr" in response
```

**Step 2: Run integration tests**

Run: `source .venv/bin/activate && pytest tests/test_diagnose_integration.py -v`
Expected: All tests PASS

**Step 3: Commit**

```bash
git add tests/test_diagnose_integration.py
git commit -m "test: add AI diagnostics integration tests"
```

---

## Task 11: Final Verification

**Step 1: Run full test suite**

Run: `source .venv/bin/activate && pytest -v --tb=short`
Expected: All tests pass

**Step 2: Tag release**

```bash
git tag -a v0.4.0 -m "Phase 4: AI-powered container diagnostics

Features:
- /diagnose <name> - AI analysis of container logs
- Reply /diagnose to crash alerts
- Configurable log line count
- Follow-up detailed analysis with 'yes'/'more'
- Uses Claude Haiku for fast, cheap analysis"
```

---

## Success Criteria Checklist

- [ ] `/diagnose <name>` returns brief AI analysis
- [ ] `/diagnose <name> <lines>` respects custom line count
- [ ] Reply `/diagnose` to crash alert extracts container name
- [ ] "Want more details?" follow-up works
- [ ] Handles container not found gracefully
- [ ] Handles missing API key gracefully
- [ ] Handles API errors gracefully
- [ ] Help text updated with new command
- [ ] All tests pass
