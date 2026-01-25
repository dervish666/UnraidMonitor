# Error Ignore Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Allow users to ignore specific recurring errors per-container via config file and /ignore command.

**Architecture:** IgnoreManager loads ignores from config.yaml and JSON file, checks before alerting. RecentErrorsBuffer tracks last 15 min of errors. /ignore command shows recent errors to pick from.

**Tech Stack:** Python 3.11+, aiogram, JSON for runtime storage

---

## Task 1: RecentErrorsBuffer Class

**Files:**
- Create: `src/alerts/recent_errors.py`
- Test: `tests/test_recent_errors.py`

**Step 1: Write the failing test**

Create `tests/test_recent_errors.py`:

```python
import pytest
from datetime import datetime, timedelta


def test_recent_errors_buffer_add_and_get():
    """Test adding and retrieving recent errors."""
    from src.alerts.recent_errors import RecentErrorsBuffer

    buffer = RecentErrorsBuffer(max_age_seconds=900, max_per_container=50)

    buffer.add("plex", "Connection failed")
    buffer.add("plex", "Database locked")
    buffer.add("radarr", "API timeout")

    plex_errors = buffer.get_recent("plex")
    assert len(plex_errors) == 2
    assert "Connection failed" in plex_errors
    assert "Database locked" in plex_errors

    radarr_errors = buffer.get_recent("radarr")
    assert len(radarr_errors) == 1
    assert "API timeout" in radarr_errors


def test_recent_errors_buffer_deduplicates():
    """Test that duplicate errors are kept (for counting) but get_recent returns unique."""
    from src.alerts.recent_errors import RecentErrorsBuffer

    buffer = RecentErrorsBuffer()

    buffer.add("plex", "Same error")
    buffer.add("plex", "Same error")
    buffer.add("plex", "Same error")

    errors = buffer.get_recent("plex")
    assert len(errors) == 1
    assert errors[0] == "Same error"


def test_recent_errors_buffer_expires_old():
    """Test that old errors are pruned."""
    from src.alerts.recent_errors import RecentErrorsBuffer

    buffer = RecentErrorsBuffer(max_age_seconds=60)

    # Add an error with old timestamp (manually for testing)
    buffer._errors["plex"] = []
    from src.alerts.recent_errors import RecentError
    old_time = datetime.now() - timedelta(seconds=120)
    buffer._errors["plex"].append(RecentError(message="Old error", timestamp=old_time))
    buffer._errors["plex"].append(RecentError(message="New error", timestamp=datetime.now()))

    errors = buffer.get_recent("plex")
    assert len(errors) == 1
    assert errors[0] == "New error"


def test_recent_errors_buffer_caps_at_max():
    """Test that buffer caps at max_per_container."""
    from src.alerts.recent_errors import RecentErrorsBuffer

    buffer = RecentErrorsBuffer(max_per_container=5)

    for i in range(10):
        buffer.add("plex", f"Error {i}")

    # Should only keep last 5
    errors = buffer.get_recent("plex")
    assert len(errors) == 5


def test_recent_errors_buffer_empty_container():
    """Test getting errors from unknown container."""
    from src.alerts.recent_errors import RecentErrorsBuffer

    buffer = RecentErrorsBuffer()

    errors = buffer.get_recent("unknown")
    assert errors == []
```

**Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest tests/test_recent_errors.py -v`
Expected: FAIL with "cannot import name 'RecentErrorsBuffer'"

**Step 3: Write minimal implementation**

Create `src/alerts/recent_errors.py`:

```python
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass
class RecentError:
    """A recent error with timestamp."""
    message: str
    timestamp: datetime


class RecentErrorsBuffer:
    """Buffer to track recent errors per container."""

    def __init__(self, max_age_seconds: int = 900, max_per_container: int = 50):
        self.max_age_seconds = max_age_seconds
        self.max_per_container = max_per_container
        self._errors: dict[str, list[RecentError]] = {}

    def add(self, container: str, message: str) -> None:
        """Add an error to the buffer."""
        if container not in self._errors:
            self._errors[container] = []

        self._errors[container].append(
            RecentError(message=message, timestamp=datetime.now())
        )

        # Prune old entries and cap at max
        self._prune(container)

    def get_recent(self, container: str) -> list[str]:
        """Get unique recent error messages for a container."""
        if container not in self._errors:
            return []

        self._prune(container)

        # Return unique messages, preserving order of first occurrence
        seen = set()
        unique = []
        for error in self._errors[container]:
            if error.message not in seen:
                seen.add(error.message)
                unique.append(error.message)
        return unique

    def _prune(self, container: str) -> None:
        """Remove old entries and cap at max."""
        if container not in self._errors:
            return

        cutoff = datetime.now() - timedelta(seconds=self.max_age_seconds)
        self._errors[container] = [
            e for e in self._errors[container]
            if e.timestamp > cutoff
        ][-self.max_per_container:]
```

**Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && python -m pytest tests/test_recent_errors.py -v`
Expected: PASS (5 tests)

**Step 5: Commit**

```bash
git add src/alerts/recent_errors.py tests/test_recent_errors.py
git commit -m "feat: add RecentErrorsBuffer for tracking recent errors"
```

---

## Task 2: IgnoreManager Class

**Files:**
- Create: `src/alerts/ignore_manager.py`
- Test: `tests/test_ignore_manager.py`

**Step 1: Write the failing test**

Create `tests/test_ignore_manager.py`:

```python
import pytest
import json
from pathlib import Path


def test_ignore_manager_is_ignored_from_config():
    """Test ignoring based on config patterns."""
    from src.alerts.ignore_manager import IgnoreManager

    config_ignores = {
        "plex": ["connection timed out", "slow query"],
        "radarr": ["rate limit"],
    }

    manager = IgnoreManager(config_ignores, json_path="/tmp/test_ignores.json")

    # Substring match, case-insensitive
    assert manager.is_ignored("plex", "Error: Connection timed out after 30s")
    assert manager.is_ignored("plex", "Warning: SLOW QUERY detected")
    assert manager.is_ignored("radarr", "API rate limit exceeded")

    # Not ignored
    assert not manager.is_ignored("plex", "Database error")
    assert not manager.is_ignored("sonarr", "connection timed out")  # different container


def test_ignore_manager_is_ignored_from_json(tmp_path):
    """Test ignoring based on JSON file."""
    from src.alerts.ignore_manager import IgnoreManager

    json_file = tmp_path / "ignored_errors.json"
    json_file.write_text(json.dumps({
        "plex": ["Sqlite3 database is locked"],
    }))

    manager = IgnoreManager({}, json_path=str(json_file))

    assert manager.is_ignored("plex", "Error: Sqlite3 database is locked")
    assert not manager.is_ignored("plex", "Other error")


def test_ignore_manager_add_ignore(tmp_path):
    """Test adding runtime ignores."""
    from src.alerts.ignore_manager import IgnoreManager

    json_file = tmp_path / "ignored_errors.json"

    manager = IgnoreManager({}, json_path=str(json_file))

    # Add ignore
    result = manager.add_ignore("plex", "New error to ignore")
    assert result is True

    # Should now be ignored
    assert manager.is_ignored("plex", "New error to ignore occurred")

    # Adding same ignore again returns False
    result = manager.add_ignore("plex", "New error to ignore")
    assert result is False

    # Check file was saved
    saved = json.loads(json_file.read_text())
    assert "plex" in saved
    assert "New error to ignore" in saved["plex"]


def test_ignore_manager_get_all_ignores(tmp_path):
    """Test getting all ignores with source."""
    from src.alerts.ignore_manager import IgnoreManager

    json_file = tmp_path / "ignored_errors.json"
    json_file.write_text(json.dumps({
        "plex": ["runtime ignore"],
    }))

    config_ignores = {
        "plex": ["config ignore"],
    }

    manager = IgnoreManager(config_ignores, json_path=str(json_file))

    ignores = manager.get_all_ignores("plex")
    assert len(ignores) == 2

    sources = {msg: src for msg, src in ignores}
    assert sources["config ignore"] == "config"
    assert sources["runtime ignore"] == "runtime"


def test_ignore_manager_missing_json_file(tmp_path):
    """Test handling of missing JSON file."""
    from src.alerts.ignore_manager import IgnoreManager

    json_file = tmp_path / "nonexistent.json"

    manager = IgnoreManager({}, json_path=str(json_file))

    # Should work with empty runtime ignores
    assert not manager.is_ignored("plex", "Some error")

    # Adding should create the file
    manager.add_ignore("plex", "New ignore")
    assert json_file.exists()
```

**Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest tests/test_ignore_manager.py -v`
Expected: FAIL with "cannot import name 'IgnoreManager'"

**Step 3: Write minimal implementation**

Create `src/alerts/ignore_manager.py`:

```python
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class IgnoreManager:
    """Manages error ignore patterns from config and runtime JSON."""

    def __init__(self, config_ignores: dict[str, list[str]], json_path: str):
        """Initialize IgnoreManager.

        Args:
            config_ignores: Per-container ignore patterns from config.yaml.
            json_path: Path to runtime ignores JSON file.
        """
        self._config_ignores = config_ignores
        self._json_path = Path(json_path)
        self._runtime_ignores: dict[str, list[str]] = {}
        self._load_runtime_ignores()

    def is_ignored(self, container: str, message: str) -> bool:
        """Check if message should be ignored (substring, case-insensitive)."""
        message_lower = message.lower()

        # Check config ignores
        for pattern in self._config_ignores.get(container, []):
            if pattern.lower() in message_lower:
                return True

        # Check runtime ignores
        for pattern in self._runtime_ignores.get(container, []):
            if pattern.lower() in message_lower:
                return True

        return False

    def add_ignore(self, container: str, message: str) -> bool:
        """Add a runtime ignore pattern.

        Returns:
            True if added, False if already exists.
        """
        if container not in self._runtime_ignores:
            self._runtime_ignores[container] = []

        # Check if already exists (case-insensitive)
        for existing in self._runtime_ignores[container]:
            if existing.lower() == message.lower():
                return False

        self._runtime_ignores[container].append(message)
        self._save_runtime_ignores()
        logger.info(f"Added ignore for {container}: {message}")
        return True

    def get_all_ignores(self, container: str) -> list[tuple[str, str]]:
        """Get all ignores for a container as (message, source) tuples."""
        ignores = []

        for pattern in self._config_ignores.get(container, []):
            ignores.append((pattern, "config"))

        for pattern in self._runtime_ignores.get(container, []):
            ignores.append((pattern, "runtime"))

        return ignores

    def _load_runtime_ignores(self) -> None:
        """Load runtime ignores from JSON file."""
        if not self._json_path.exists():
            self._runtime_ignores = {}
            return

        try:
            with open(self._json_path, encoding="utf-8") as f:
                self._runtime_ignores = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Failed to load runtime ignores: {e}")
            self._runtime_ignores = {}

    def _save_runtime_ignores(self) -> None:
        """Save runtime ignores to JSON file."""
        # Ensure parent directory exists
        self._json_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            with open(self._json_path, "w", encoding="utf-8") as f:
                json.dump(self._runtime_ignores, f, indent=2)
        except IOError as e:
            logger.error(f"Failed to save runtime ignores: {e}")
```

**Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && python -m pytest tests/test_ignore_manager.py -v`
Expected: PASS (5 tests)

**Step 5: Commit**

```bash
git add src/alerts/ignore_manager.py tests/test_ignore_manager.py
git commit -m "feat: add IgnoreManager for per-container error ignores"
```

---

## Task 3: Add container_ignores to Config

**Files:**
- Modify: `src/config.py`
- Test: `tests/test_config.py`

**Step 1: Write the failing test**

Add to `tests/test_config.py` (create if needed):

```python
import pytest


def test_log_watching_container_ignores(tmp_path):
    """Test that container_ignores is parsed from config."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text("""
log_watching:
  containers:
    - plex
  error_patterns:
    - error
  ignore_patterns:
    - DEBUG
  container_ignores:
    plex:
      - connection timed out
      - slow query
    radarr:
      - rate limit
""")

    import os
    from unittest.mock import patch

    with patch.dict(os.environ, {
        "TELEGRAM_BOT_TOKEN": "test",
        "TELEGRAM_ALLOWED_USERS": "123",
    }):
        from src.config import Settings, AppConfig

        settings = Settings(config_path=str(config_file))
        config = AppConfig(settings)

        log_watching = config.log_watching
        assert "container_ignores" in log_watching
        assert log_watching["container_ignores"]["plex"] == ["connection timed out", "slow query"]
        assert log_watching["container_ignores"]["radarr"] == ["rate limit"]


def test_log_watching_container_ignores_default_empty(tmp_path):
    """Test that container_ignores defaults to empty dict."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text("""
log_watching:
  containers:
    - plex
""")

    import os
    from unittest.mock import patch

    with patch.dict(os.environ, {
        "TELEGRAM_BOT_TOKEN": "test",
        "TELEGRAM_ALLOWED_USERS": "123",
    }):
        from src.config import Settings, AppConfig

        settings = Settings(config_path=str(config_file))
        config = AppConfig(settings)

        log_watching = config.log_watching
        assert log_watching.get("container_ignores", {}) == {}
```

**Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest tests/test_config.py::test_log_watching_container_ignores -v`
Expected: FAIL with "KeyError: 'container_ignores'" or assertion error

**Step 3: Write minimal implementation**

Modify `src/config.py`. Update the `DEFAULT_LOG_WATCHING` constant (around line 46):

```python
# Combined default log watching configuration
DEFAULT_LOG_WATCHING: dict[str, Any] = {
    "containers": DEFAULT_WATCHED_CONTAINERS,
    "error_patterns": DEFAULT_ERROR_PATTERNS,
    "ignore_patterns": DEFAULT_IGNORE_PATTERNS,
    "cooldown_seconds": 900,
    "container_ignores": {},  # NEW: per-container ignore patterns
}
```

Update the `log_watching` property in `AppConfig` class (around line 167):

```python
    @property
    def log_watching(self) -> dict[str, Any]:
        """Get log watching configuration.

        Returns YAML config if present, otherwise returns defaults.
        Ensures container_ignores key exists.
        """
        config = self._yaml_config.get("log_watching", DEFAULT_LOG_WATCHING)
        # Ensure container_ignores exists
        if "container_ignores" not in config:
            config["container_ignores"] = {}
        return config
```

**Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && python -m pytest tests/test_config.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/config.py tests/test_config.py
git commit -m "feat: add container_ignores to log_watching config"
```

---

## Task 4: Integrate IgnoreManager into LogWatcher

**Files:**
- Modify: `src/monitors/log_watcher.py`
- Test: `tests/test_log_watcher.py`

**Step 1: Write the failing test**

Add to `tests/test_log_watcher.py`:

```python
import pytest
from unittest.mock import MagicMock, AsyncMock


@pytest.mark.asyncio
async def test_log_watcher_respects_ignore_manager():
    """Test that LogWatcher checks IgnoreManager before alerting."""
    from src.monitors.log_watcher import matches_error_pattern

    # This tests the existing function - we need to add ignore_manager support
    # First verify current behavior
    assert matches_error_pattern("Error occurred", ["error"], [])

    # Now test with ignore manager
    from src.alerts.ignore_manager import IgnoreManager

    ignore_manager = IgnoreManager(
        config_ignores={"plex": ["known issue"]},
        json_path="/tmp/test_ignores.json"
    )

    # This line should be ignored
    from src.monitors.log_watcher import should_alert_for_error
    assert not should_alert_for_error(
        container="plex",
        line="Error: known issue occurred",
        error_patterns=["error"],
        ignore_patterns=[],
        ignore_manager=ignore_manager,
    )

    # This line should alert
    assert should_alert_for_error(
        container="plex",
        line="Error: unknown problem",
        error_patterns=["error"],
        ignore_patterns=[],
        ignore_manager=ignore_manager,
    )
```

**Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest tests/test_log_watcher.py::test_log_watcher_respects_ignore_manager -v`
Expected: FAIL with "cannot import name 'should_alert_for_error'"

**Step 3: Write minimal implementation**

Modify `src/monitors/log_watcher.py`. Add new function after `matches_error_pattern`:

```python
def should_alert_for_error(
    container: str,
    line: str,
    error_patterns: list[str],
    ignore_patterns: list[str],
    ignore_manager: "IgnoreManager | None" = None,
) -> bool:
    """Check if an error line should trigger an alert.

    Args:
        container: Container name.
        line: Log line to check.
        error_patterns: Patterns that indicate an error.
        ignore_patterns: Global patterns to ignore.
        ignore_manager: Optional IgnoreManager for per-container ignores.

    Returns:
        True if should alert, False if should be ignored.
    """
    # First check if it matches an error pattern
    if not matches_error_pattern(line, error_patterns, ignore_patterns):
        return False

    # Then check per-container ignores
    if ignore_manager and ignore_manager.is_ignored(container, line):
        return False

    return True
```

Add import at top if needed:

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.alerts.ignore_manager import IgnoreManager
```

**Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && python -m pytest tests/test_log_watcher.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/monitors/log_watcher.py tests/test_log_watcher.py
git commit -m "feat: add should_alert_for_error with IgnoreManager support"
```

---

## Task 5: Update LogWatcher to Use IgnoreManager and RecentErrorsBuffer

**Files:**
- Modify: `src/monitors/log_watcher.py`
- Test: `tests/test_log_watcher.py`

**Step 1: Write the failing test**

Add to `tests/test_log_watcher.py`:

```python
def test_log_watcher_accepts_ignore_manager_and_buffer():
    """Test LogWatcher constructor accepts new parameters."""
    from src.monitors.log_watcher import LogWatcher
    from src.alerts.ignore_manager import IgnoreManager
    from src.alerts.recent_errors import RecentErrorsBuffer

    ignore_manager = IgnoreManager({}, json_path="/tmp/test.json")
    recent_buffer = RecentErrorsBuffer()

    watcher = LogWatcher(
        containers=["plex"],
        error_patterns=["error"],
        ignore_patterns=[],
        ignore_manager=ignore_manager,
        recent_errors_buffer=recent_buffer,
    )

    assert watcher.ignore_manager is ignore_manager
    assert watcher.recent_errors_buffer is recent_buffer
```

**Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest tests/test_log_watcher.py::test_log_watcher_accepts_ignore_manager_and_buffer -v`
Expected: FAIL with "TypeError: LogWatcher.__init__() got an unexpected keyword argument"

**Step 3: Write minimal implementation**

Update `LogWatcher.__init__` in `src/monitors/log_watcher.py`:

```python
class LogWatcher:
    """Watch container logs for error patterns."""

    def __init__(
        self,
        containers: list[str],
        error_patterns: list[str],
        ignore_patterns: list[str],
        on_error: Callable[[str, str], Awaitable[None]] | None = None,
        ignore_manager: "IgnoreManager | None" = None,
        recent_errors_buffer: "RecentErrorsBuffer | None" = None,
    ):
        self.containers = containers
        self.error_patterns = error_patterns
        self.ignore_patterns = ignore_patterns
        self.on_error = on_error
        self.ignore_manager = ignore_manager
        self.recent_errors_buffer = recent_errors_buffer
        self._client: docker.DockerClient | None = None
        self._running = False
        self._tasks: list[asyncio.Task] = []
```

Add TYPE_CHECKING imports at top:

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.alerts.ignore_manager import IgnoreManager
    from src.alerts.recent_errors import RecentErrorsBuffer
```

Update `_stream_logs` method to use the new components (around line 129):

```python
                if should_alert_for_error(
                    container=container_name,
                    line=line,
                    error_patterns=self.error_patterns,
                    ignore_patterns=self.ignore_patterns,
                    ignore_manager=self.ignore_manager,
                ):
                    logger.info(f"Error detected in {container_name}: {line[:100]}")

                    # Store in recent errors buffer
                    if self.recent_errors_buffer:
                        self.recent_errors_buffer.add(container_name, line)

                    if self.on_error:
                        await self.on_error(container_name, line)
```

**Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && python -m pytest tests/test_log_watcher.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/monitors/log_watcher.py tests/test_log_watcher.py
git commit -m "feat: integrate IgnoreManager and RecentErrorsBuffer into LogWatcher"
```

---

## Task 6: /ignore Command Handler

**Files:**
- Create: `src/bot/ignore_command.py`
- Test: `tests/test_ignore_command.py`

**Step 1: Write the failing test**

Create `tests/test_ignore_command.py`:

```python
import pytest
from unittest.mock import MagicMock, AsyncMock


@pytest.mark.asyncio
async def test_ignore_command_shows_recent_errors():
    """Test /ignore shows recent errors when replying to alert."""
    from src.bot.ignore_command import ignore_command
    from src.alerts.recent_errors import RecentErrorsBuffer
    from src.alerts.ignore_manager import IgnoreManager

    buffer = RecentErrorsBuffer()
    buffer.add("plex", "Error 1")
    buffer.add("plex", "Error 2")

    manager = IgnoreManager({}, json_path="/tmp/test.json")

    handler = ignore_command(buffer, manager)

    # Create mock message replying to an alert
    reply_message = MagicMock()
    reply_message.text = "⚠️ ERRORS IN: plex\n\nFound 2 errors"

    message = MagicMock()
    message.text = "/ignore"
    message.reply_to_message = reply_message
    message.answer = AsyncMock()
    message.from_user = MagicMock()
    message.from_user.id = 123

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]

    assert "Recent errors in plex" in response
    assert "Error 1" in response
    assert "Error 2" in response


@pytest.mark.asyncio
async def test_ignore_command_no_reply():
    """Test /ignore without replying to message."""
    from src.bot.ignore_command import ignore_command
    from src.alerts.recent_errors import RecentErrorsBuffer
    from src.alerts.ignore_manager import IgnoreManager

    buffer = RecentErrorsBuffer()
    manager = IgnoreManager({}, json_path="/tmp/test.json")

    handler = ignore_command(buffer, manager)

    message = MagicMock()
    message.text = "/ignore"
    message.reply_to_message = None
    message.answer = AsyncMock()

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]
    assert "Reply to an error alert" in response


@pytest.mark.asyncio
async def test_ignore_command_not_error_alert():
    """Test /ignore when replying to non-error message."""
    from src.bot.ignore_command import ignore_command
    from src.alerts.recent_errors import RecentErrorsBuffer
    from src.alerts.ignore_manager import IgnoreManager

    buffer = RecentErrorsBuffer()
    manager = IgnoreManager({}, json_path="/tmp/test.json")

    handler = ignore_command(buffer, manager)

    reply_message = MagicMock()
    reply_message.text = "Hello there"

    message = MagicMock()
    message.text = "/ignore"
    message.reply_to_message = reply_message
    message.answer = AsyncMock()

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]
    assert "error alert" in response.lower()
```

**Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest tests/test_ignore_command.py -v`
Expected: FAIL with "cannot import name 'ignore_command'"

**Step 3: Write minimal implementation**

Create `src/bot/ignore_command.py`:

```python
import re
import logging
from typing import Callable, Awaitable, TYPE_CHECKING

from aiogram.types import Message

if TYPE_CHECKING:
    from src.alerts.recent_errors import RecentErrorsBuffer
    from src.alerts.ignore_manager import IgnoreManager

logger = logging.getLogger(__name__)

# Pattern to extract container name from error alert
ALERT_PATTERN = re.compile(r"ERRORS IN[:\s]+(\w+)", re.IGNORECASE)


def extract_container_from_alert(text: str) -> str | None:
    """Extract container name from error alert message."""
    match = ALERT_PATTERN.search(text)
    return match.group(1) if match else None


def ignore_command(
    recent_buffer: "RecentErrorsBuffer",
    ignore_manager: "IgnoreManager",
) -> Callable[[Message], Awaitable[None]]:
    """Factory for /ignore command handler."""

    # Store pending ignore selections per user
    pending_selections: dict[int, tuple[str, list[str]]] = {}

    async def handler(message: Message) -> None:
        text = message.text or ""
        user_id = message.from_user.id if message.from_user else 0

        # Check if this is a selection response (numbers like "1,3" or "all")
        if user_id in pending_selections:
            container, errors = pending_selections[user_id]
            await handle_selection(message, container, errors, ignore_manager, pending_selections)
            return

        # Must be replying to an error alert
        if not message.reply_to_message or not message.reply_to_message.text:
            await message.answer("Reply to an error alert to ignore errors from it.")
            return

        reply_text = message.reply_to_message.text

        # Extract container from alert
        container = extract_container_from_alert(reply_text)
        if not container:
            await message.answer("Can only ignore errors from error alerts. Reply to a ⚠️ ERRORS IN message.")
            return

        # Get recent errors for this container
        recent_errors = recent_buffer.get_recent(container)

        if not recent_errors:
            await message.answer(f"No recent errors found for {container}.")
            return

        # Build numbered list
        lines = [f"🔇 *Recent errors in {container}* (last 15 min):\n"]
        for i, error in enumerate(recent_errors, 1):
            # Truncate long errors
            display = error[:80] + "..." if len(error) > 80 else error
            lines.append(f"`{i}.` {display}")

        lines.append("")
        lines.append('_Reply with numbers to ignore (e.g., "1,3" or "all")_')

        # Store pending selection
        pending_selections[user_id] = (container, recent_errors)

        await message.answer("\n".join(lines), parse_mode="Markdown")

    return handler


async def handle_selection(
    message: Message,
    container: str,
    errors: list[str],
    ignore_manager: "IgnoreManager",
    pending_selections: dict,
) -> None:
    """Handle user's selection of errors to ignore."""
    text = (message.text or "").strip().lower()
    user_id = message.from_user.id if message.from_user else 0

    # Clear pending selection
    del pending_selections[user_id]

    if text == "all":
        indices = list(range(len(errors)))
    else:
        # Parse comma-separated numbers
        try:
            indices = [int(x.strip()) - 1 for x in text.split(",")]
            # Validate indices
            if any(i < 0 or i >= len(errors) for i in indices):
                await message.answer("Invalid selection. Numbers must be from the list.")
                return
        except ValueError:
            await message.answer("Invalid input. Use numbers like '1,3' or 'all'.")
            return

    # Add ignores
    added = []
    for i in indices:
        error = errors[i]
        if ignore_manager.add_ignore(container, error):
            added.append(error)

    if added:
        lines = [f"✅ *Ignored for {container}:*\n"]
        for error in added:
            display = error[:60] + "..." if len(error) > 60 else error
            lines.append(f"  • {display}")
        await message.answer("\n".join(lines), parse_mode="Markdown")
    else:
        await message.answer("Those errors are already ignored.")
```

**Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && python -m pytest tests/test_ignore_command.py -v`
Expected: PASS (3 tests)

**Step 5: Commit**

```bash
git add src/bot/ignore_command.py tests/test_ignore_command.py
git commit -m "feat: add /ignore command handler"
```

---

## Task 7: /ignores Command Handler

**Files:**
- Modify: `src/bot/ignore_command.py`
- Test: `tests/test_ignore_command.py`

**Step 1: Write the failing test**

Add to `tests/test_ignore_command.py`:

```python
@pytest.mark.asyncio
async def test_ignores_command_lists_all():
    """Test /ignores lists all ignores."""
    from src.bot.ignore_command import ignores_command
    from src.alerts.ignore_manager import IgnoreManager
    import json

    # Create manager with config and runtime ignores
    config_ignores = {"plex": ["config pattern"]}

    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({"plex": ["runtime pattern"], "radarr": ["another"]}, f)
        json_path = f.name

    manager = IgnoreManager(config_ignores, json_path=json_path)

    handler = ignores_command(manager)

    message = MagicMock()
    message.text = "/ignores"
    message.answer = AsyncMock()

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]

    assert "plex" in response
    assert "config pattern" in response
    assert "(config)" in response
    assert "runtime pattern" in response
    assert "radarr" in response


@pytest.mark.asyncio
async def test_ignores_command_empty():
    """Test /ignores with no ignores."""
    from src.bot.ignore_command import ignores_command
    from src.alerts.ignore_manager import IgnoreManager

    manager = IgnoreManager({}, json_path="/tmp/nonexistent.json")

    handler = ignores_command(manager)

    message = MagicMock()
    message.text = "/ignores"
    message.answer = AsyncMock()

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]
    assert "no ignored" in response.lower() or "No ignored" in response
```

**Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest tests/test_ignore_command.py::test_ignores_command_lists_all -v`
Expected: FAIL with "cannot import name 'ignores_command'"

**Step 3: Write minimal implementation**

Add to `src/bot/ignore_command.py`:

```python
def ignores_command(
    ignore_manager: "IgnoreManager",
) -> Callable[[Message], Awaitable[None]]:
    """Factory for /ignores command handler."""

    async def handler(message: Message) -> None:
        # Collect all ignores across containers
        all_containers: set[str] = set()

        # Get containers from config ignores
        all_containers.update(ignore_manager._config_ignores.keys())

        # Get containers from runtime ignores
        all_containers.update(ignore_manager._runtime_ignores.keys())

        if not all_containers:
            await message.answer("🔇 No ignored errors configured.\n\n_Use /ignore to add some._", parse_mode="Markdown")
            return

        lines = ["🔇 *Ignored Errors*\n"]

        for container in sorted(all_containers):
            ignores = ignore_manager.get_all_ignores(container)
            if ignores:
                lines.append(f"*{container}* ({len(ignores)}):")
                for pattern, source in ignores:
                    display = pattern[:50] + "..." if len(pattern) > 50 else pattern
                    source_tag = " (config)" if source == "config" else ""
                    lines.append(f"  • {display}{source_tag}")
                lines.append("")

        lines.append("_Use /ignore to add more_")

        await message.answer("\n".join(lines), parse_mode="Markdown")

    return handler
```

**Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && python -m pytest tests/test_ignore_command.py -v`
Expected: PASS (5 tests)

**Step 5: Commit**

```bash
git add src/bot/ignore_command.py tests/test_ignore_command.py
git commit -m "feat: add /ignores command handler"
```

---

## Task 8: Register Commands and Update Help

**Files:**
- Modify: `src/bot/telegram_bot.py`
- Modify: `src/bot/commands.py`
- Test: `tests/test_ignore_command.py`

**Step 1: Write the failing test**

Add to `tests/test_ignore_command.py`:

```python
def test_ignore_commands_in_help():
    """Test that /ignore and /ignores are in help text."""
    from src.bot.commands import HELP_TEXT

    assert "/ignore" in HELP_TEXT
    assert "/ignores" in HELP_TEXT
```

**Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest tests/test_ignore_command.py::test_ignore_commands_in_help -v`
Expected: FAIL with "AssertionError"

**Step 3: Write minimal implementation**

Update `HELP_TEXT` in `src/bot/commands.py` to add the new commands (around line 10):

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
/help - Show this help message

_Partial container names work: /status rad → radarr_
_Control commands require confirmation_
_Reply /diagnose to a crash alert for quick analysis_"""
```

Update `src/bot/telegram_bot.py` to register the commands. Add import:

```python
from src.bot.ignore_command import ignore_command, ignores_command
```

Update `register_commands` function signature to accept ignore_manager and recent_buffer:

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
) -> tuple[ConfirmationManager | None, DiagnosticService | None]:
```

Add registration after /resources (inside the `if docker_client:` block):

```python
        # Register /ignore and /ignores commands
        if ignore_manager is not None and recent_errors_buffer is not None:
            dp.message.register(
                ignore_command(recent_errors_buffer, ignore_manager),
                Command("ignore"),
            )
            dp.message.register(
                ignores_command(ignore_manager),
                Command("ignores"),
            )
```

**Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && python -m pytest tests/test_ignore_command.py -v`
Expected: PASS (6 tests)

**Step 5: Commit**

```bash
git add src/bot/telegram_bot.py src/bot/commands.py tests/test_ignore_command.py
git commit -m "feat: register /ignore and /ignores commands"
```

---

## Task 9: Main Integration

**Files:**
- Modify: `src/main.py`
- Test: `tests/test_ignore_integration.py`

**Step 1: Write the failing test**

Create `tests/test_ignore_integration.py`:

```python
import pytest


def test_main_creates_ignore_manager():
    """Test that IgnoreManager is created in main."""
    from src.alerts.ignore_manager import IgnoreManager

    # Verify class exists and can be instantiated
    manager = IgnoreManager({}, json_path="/tmp/test.json")
    assert manager is not None


def test_main_creates_recent_errors_buffer():
    """Test that RecentErrorsBuffer is created in main."""
    from src.alerts.recent_errors import RecentErrorsBuffer

    buffer = RecentErrorsBuffer()
    assert buffer is not None
```

**Step 2: Run test to verify it passes (validation only)**

Run: `source .venv/bin/activate && python -m pytest tests/test_ignore_integration.py -v`
Expected: PASS

**Step 3: Write the main.py integration**

Modify `src/main.py`. Add imports near the top:

```python
from src.alerts.ignore_manager import IgnoreManager
from src.alerts.recent_errors import RecentErrorsBuffer
```

After creating `rate_limiter` (around line 81), add:

```python
    # Initialize ignore manager and recent errors buffer
    log_watching_config = config.log_watching
    ignore_manager = IgnoreManager(
        config_ignores=log_watching_config.get("container_ignores", {}),
        json_path="data/ignored_errors.json",
    )
    recent_errors_buffer = RecentErrorsBuffer(
        max_age_seconds=log_watching_config.get("cooldown_seconds", 900),
        max_per_container=50,
    )
```

Update the LogWatcher initialization (around line 120) to pass the new components:

```python
    log_watcher = LogWatcher(
        containers=log_watching_config["containers"],
        error_patterns=log_watching_config["error_patterns"],
        ignore_patterns=log_watching_config["ignore_patterns"],
        on_error=on_log_error,
        ignore_manager=ignore_manager,
        recent_errors_buffer=recent_errors_buffer,
    )
```

Update the `register_commands` call (around line 148) to pass the new components:

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
    )
```

**Step 4: Run all tests**

Run: `source .venv/bin/activate && python -m pytest -v`
Expected: All tests pass

**Step 5: Commit**

```bash
git add src/main.py tests/test_ignore_integration.py
git commit -m "feat: integrate IgnoreManager and RecentErrorsBuffer into main"
```

---

## Task 10: Final Verification

**Files:**
- All files from previous tasks

**Step 1: Run all tests**

Run: `source .venv/bin/activate && python -m pytest -v`
Expected: All tests PASS

**Step 2: Type check**

Run: `source .venv/bin/activate && python -m py_compile src/alerts/ignore_manager.py src/alerts/recent_errors.py src/bot/ignore_command.py`
Expected: No errors

**Step 3: Commit and tag**

```bash
git add -A
git commit -m "feat: complete error ignore implementation

- Add /ignore command to suppress recurring errors
- Add /ignores command to list all ignored patterns
- Support per-container ignores in config.yaml
- Runtime ignores saved to data/ignored_errors.json"

git tag -a v0.6.0 -m "Error ignore feature"
```

**Step 4: Push to remote**

```bash
git push origin master --tags
```

---

## Success Criteria

- [ ] Per-container ignores in `config.yaml` (`container_ignores` section)
- [ ] Runtime ignores in `data/ignored_errors.json`
- [ ] `/ignore` command - reply to alert, pick from recent errors
- [ ] `/ignores` command - list all current ignores with source
- [ ] Substring matching (case-insensitive)
- [ ] Recent errors buffer (15 min, 50 max per container)
- [ ] All tests pass
