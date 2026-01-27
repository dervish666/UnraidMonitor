# Phase 6 System Improvements Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add memory pressure management, smart ignore pattern generation with Haiku, and persistent storage with bind volumes.

**Architecture:** Three independent features that can be implemented in sequence. Memory monitor follows existing ResourceMonitor pattern. Smart ignore extends IgnoreManager with regex support and Haiku analysis. Persistent storage is primarily configuration changes.

**Tech Stack:** Python 3.11+, aiogram 3.x, anthropic SDK, Docker, psutil (new dependency for system memory)

---

## Feature 1: Memory Pressure Management

### Task 1.1: Add Memory Management Configuration

**Files:**
- Modify: `src/config.py`
- Test: `tests/test_memory_config.py` (new)

**Step 1: Write the failing test**

Create `tests/test_memory_config.py`:

```python
"""Tests for memory management configuration."""

import pytest
from src.config import MemoryConfig


class TestMemoryConfig:
    def test_from_dict_with_all_fields(self):
        data = {
            "enabled": True,
            "warning_threshold": 90,
            "critical_threshold": 95,
            "safe_threshold": 80,
            "kill_delay_seconds": 60,
            "stabilization_wait": 180,
            "priority_containers": ["plex", "mariadb"],
            "killable_containers": ["bitmagnet", "obsidian"],
        }
        config = MemoryConfig.from_dict(data)

        assert config.enabled is True
        assert config.warning_threshold == 90
        assert config.critical_threshold == 95
        assert config.safe_threshold == 80
        assert config.kill_delay_seconds == 60
        assert config.stabilization_wait == 180
        assert config.priority_containers == ["plex", "mariadb"]
        assert config.killable_containers == ["bitmagnet", "obsidian"]

    def test_from_dict_with_defaults(self):
        config = MemoryConfig.from_dict({})

        assert config.enabled is False
        assert config.warning_threshold == 90
        assert config.critical_threshold == 95
        assert config.safe_threshold == 80
        assert config.kill_delay_seconds == 60
        assert config.stabilization_wait == 180
        assert config.priority_containers == []
        assert config.killable_containers == []

    def test_from_dict_disabled(self):
        config = MemoryConfig.from_dict({"enabled": False})
        assert config.enabled is False
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_memory_config.py -v`
Expected: FAIL with "cannot import name 'MemoryConfig'"

**Step 3: Write minimal implementation**

Add to `src/config.py` after `ResourceConfig` class:

```python
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
        return cls(
            enabled=data.get("enabled", False),
            warning_threshold=data.get("warning_threshold", 90),
            critical_threshold=data.get("critical_threshold", 95),
            safe_threshold=data.get("safe_threshold", 80),
            kill_delay_seconds=data.get("kill_delay_seconds", 60),
            stabilization_wait=data.get("stabilization_wait", 180),
            priority_containers=data.get("priority_containers", []),
            killable_containers=data.get("killable_containers", []),
        )
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_memory_config.py -v`
Expected: PASS

**Step 5: Add memory config to AppConfig**

Add test to `tests/test_memory_config.py`:

```python
class TestAppConfigMemory:
    def test_app_config_has_memory_management(self):
        from unittest.mock import MagicMock
        from src.config import AppConfig

        settings = MagicMock()
        settings.config_path = "config/config.yaml"

        config = AppConfig(settings)
        assert hasattr(config, "memory_management")
        assert isinstance(config.memory_management, MemoryConfig)
```

Run: `pytest tests/test_memory_config.py::TestAppConfigMemory -v`
Expected: FAIL

**Step 6: Add property to AppConfig**

In `src/config.py`, add to `AppConfig` class:

```python
@property
def memory_management(self) -> MemoryConfig:
    """Get memory management configuration."""
    return MemoryConfig.from_dict(self._raw_config.get("memory_management", {}))
```

**Step 7: Run all memory config tests**

Run: `pytest tests/test_memory_config.py -v`
Expected: PASS

**Step 8: Commit**

```bash
git add src/config.py tests/test_memory_config.py
git commit -m "feat: add MemoryConfig for memory pressure management"
```

---

### Task 1.2: Create MemoryMonitor Core

**Files:**
- Create: `src/monitors/memory_monitor.py`
- Test: `tests/test_memory_monitor.py` (new)

**Step 1: Write the failing test for basic structure**

Create `tests/test_memory_monitor.py`:

```python
"""Tests for memory pressure monitor."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.monitors.memory_monitor import MemoryMonitor, MemoryState
from src.config import MemoryConfig


@pytest.fixture
def memory_config():
    return MemoryConfig(
        enabled=True,
        warning_threshold=90,
        critical_threshold=95,
        safe_threshold=80,
        kill_delay_seconds=60,
        stabilization_wait=180,
        priority_containers=["plex"],
        killable_containers=["bitmagnet", "obsidian"],
    )


@pytest.fixture
def mock_docker_client():
    return MagicMock()


@pytest.fixture
def mock_on_alert():
    return AsyncMock()


@pytest.fixture
def mock_on_ask_restart():
    return AsyncMock()


class TestMemoryMonitor:
    def test_init(self, memory_config, mock_docker_client, mock_on_alert, mock_on_ask_restart):
        monitor = MemoryMonitor(
            docker_client=mock_docker_client,
            config=memory_config,
            on_alert=mock_on_alert,
            on_ask_restart=mock_on_ask_restart,
        )

        assert monitor._config == memory_config
        assert monitor._state == MemoryState.NORMAL
        assert monitor._killed_containers == []
        assert not monitor._running

    def test_is_enabled(self, memory_config, mock_docker_client, mock_on_alert, mock_on_ask_restart):
        monitor = MemoryMonitor(
            docker_client=mock_docker_client,
            config=memory_config,
            on_alert=mock_on_alert,
            on_ask_restart=mock_on_ask_restart,
        )
        assert monitor.is_enabled() is True

    def test_is_disabled(self, mock_docker_client, mock_on_alert, mock_on_ask_restart):
        config = MemoryConfig.from_dict({"enabled": False})
        monitor = MemoryMonitor(
            docker_client=mock_docker_client,
            config=config,
            on_alert=mock_on_alert,
            on_ask_restart=mock_on_ask_restart,
        )
        assert monitor.is_enabled() is False
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_memory_monitor.py::TestMemoryMonitor::test_init -v`
Expected: FAIL with "No module named 'src.monitors.memory_monitor'"

**Step 3: Write minimal implementation**

Create `src/monitors/memory_monitor.py`:

```python
"""Memory pressure monitor for system-wide memory management."""

import asyncio
import logging
from enum import Enum, auto
from typing import Callable, Awaitable

import docker

from src.config import MemoryConfig

logger = logging.getLogger(__name__)


class MemoryState(Enum):
    """Current memory pressure state."""

    NORMAL = auto()
    WARNING = auto()  # Above warning threshold
    CRITICAL = auto()  # Above critical threshold
    KILLING = auto()  # Kill pending (countdown active)
    RECOVERING = auto()  # Killed containers, waiting for safe level


class MemoryMonitor:
    """Monitors system memory and manages container lifecycle under pressure."""

    def __init__(
        self,
        docker_client: docker.DockerClient,
        config: MemoryConfig,
        on_alert: Callable[[str, str], Awaitable[None]],
        on_ask_restart: Callable[[str], Awaitable[None]],
    ):
        """Initialize memory monitor.

        Args:
            docker_client: Docker client for container control.
            config: Memory management configuration.
            on_alert: Callback for sending alerts (title, message).
            on_ask_restart: Callback for asking to restart a container.
        """
        self._docker = docker_client
        self._config = config
        self._on_alert = on_alert
        self._on_ask_restart = on_ask_restart
        self._state = MemoryState.NORMAL
        self._killed_containers: list[str] = []
        self._running = False
        self._pending_kill: str | None = None
        self._kill_cancelled = False

    def is_enabled(self) -> bool:
        """Check if memory monitoring is enabled."""
        return self._config.enabled
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_memory_monitor.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/monitors/memory_monitor.py tests/test_memory_monitor.py
git commit -m "feat: add MemoryMonitor skeleton with state management"
```

---

### Task 1.3: Add System Memory Reading

**Files:**
- Modify: `src/monitors/memory_monitor.py`
- Modify: `tests/test_memory_monitor.py`
- Modify: `requirements.txt`

**Step 1: Add psutil dependency**

Add to `requirements.txt`:

```
psutil>=5.9.0
```

Run: `pip install psutil`

**Step 2: Write the failing test**

Add to `tests/test_memory_monitor.py`:

```python
class TestMemoryReading:
    @patch("src.monitors.memory_monitor.psutil")
    def test_get_memory_percent(
        self, mock_psutil, memory_config, mock_docker_client, mock_on_alert, mock_on_ask_restart
    ):
        mock_psutil.virtual_memory.return_value = MagicMock(percent=85.5)

        monitor = MemoryMonitor(
            docker_client=mock_docker_client,
            config=memory_config,
            on_alert=mock_on_alert,
            on_ask_restart=mock_on_ask_restart,
        )

        percent = monitor.get_memory_percent()
        assert percent == 85.5
        mock_psutil.virtual_memory.assert_called_once()
```

**Step 3: Run test to verify it fails**

Run: `pytest tests/test_memory_monitor.py::TestMemoryReading -v`
Expected: FAIL

**Step 4: Implement get_memory_percent**

Add import at top of `src/monitors/memory_monitor.py`:

```python
import psutil
```

Add method to `MemoryMonitor` class:

```python
def get_memory_percent(self) -> float:
    """Get current system memory usage percentage."""
    return psutil.virtual_memory().percent
```

**Step 5: Run test to verify it passes**

Run: `pytest tests/test_memory_monitor.py::TestMemoryReading -v`
Expected: PASS

**Step 6: Commit**

```bash
git add src/monitors/memory_monitor.py tests/test_memory_monitor.py requirements.txt
git commit -m "feat: add system memory reading via psutil"
```

---

### Task 1.4: Add Container Stopping Logic

**Files:**
- Modify: `src/monitors/memory_monitor.py`
- Modify: `tests/test_memory_monitor.py`

**Step 1: Write the failing test**

Add to `tests/test_memory_monitor.py`:

```python
class TestContainerControl:
    def test_get_next_killable_returns_first_running(
        self, memory_config, mock_docker_client, mock_on_alert, mock_on_ask_restart
    ):
        # Mock running containers
        container1 = MagicMock()
        container1.name = "bitmagnet"
        container1.status = "running"

        container2 = MagicMock()
        container2.name = "obsidian"
        container2.status = "running"

        mock_docker_client.containers.list.return_value = [container1, container2]

        monitor = MemoryMonitor(
            docker_client=mock_docker_client,
            config=memory_config,
            on_alert=mock_on_alert,
            on_ask_restart=mock_on_ask_restart,
        )

        # bitmagnet is first in killable list
        result = monitor._get_next_killable()
        assert result == "bitmagnet"

    def test_get_next_killable_skips_already_killed(
        self, memory_config, mock_docker_client, mock_on_alert, mock_on_ask_restart
    ):
        container1 = MagicMock()
        container1.name = "bitmagnet"
        container1.status = "exited"  # Already killed

        container2 = MagicMock()
        container2.name = "obsidian"
        container2.status = "running"

        mock_docker_client.containers.list.return_value = [container2]
        mock_docker_client.containers.get.return_value = container1

        monitor = MemoryMonitor(
            docker_client=mock_docker_client,
            config=memory_config,
            on_alert=mock_on_alert,
            on_ask_restart=mock_on_ask_restart,
        )
        monitor._killed_containers = ["bitmagnet"]

        result = monitor._get_next_killable()
        assert result == "obsidian"

    def test_get_next_killable_returns_none_when_exhausted(
        self, memory_config, mock_docker_client, mock_on_alert, mock_on_ask_restart
    ):
        mock_docker_client.containers.list.return_value = []

        monitor = MemoryMonitor(
            docker_client=mock_docker_client,
            config=memory_config,
            on_alert=mock_on_alert,
            on_ask_restart=mock_on_ask_restart,
        )
        monitor._killed_containers = ["bitmagnet", "obsidian"]

        result = monitor._get_next_killable()
        assert result is None

    @pytest.mark.asyncio
    async def test_stop_container(
        self, memory_config, mock_docker_client, mock_on_alert, mock_on_ask_restart
    ):
        container = MagicMock()
        container.name = "bitmagnet"
        mock_docker_client.containers.get.return_value = container

        monitor = MemoryMonitor(
            docker_client=mock_docker_client,
            config=memory_config,
            on_alert=mock_on_alert,
            on_ask_restart=mock_on_ask_restart,
        )

        await monitor._stop_container("bitmagnet")

        container.stop.assert_called_once()
        assert "bitmagnet" in monitor._killed_containers
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_memory_monitor.py::TestContainerControl -v`
Expected: FAIL

**Step 3: Implement container control methods**

Add to `MemoryMonitor` class in `src/monitors/memory_monitor.py`:

```python
def _get_next_killable(self) -> str | None:
    """Get the next container to kill from the killable list.

    Returns the first running container from killable_containers
    that hasn't already been killed in this pressure event.
    """
    running_names = {c.name for c in self._docker.containers.list()}

    for name in self._config.killable_containers:
        if name in self._killed_containers:
            continue
        if name in running_names:
            return name

    return None

async def _stop_container(self, name: str) -> None:
    """Stop a container and record it as killed."""
    try:
        container = self._docker.containers.get(name)
        container.stop()
        self._killed_containers.append(name)
        logger.info(f"Stopped container {name} due to memory pressure")
    except docker.errors.NotFound:
        logger.warning(f"Container {name} not found when trying to stop")
    except Exception as e:
        logger.error(f"Failed to stop container {name}: {e}")
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_memory_monitor.py::TestContainerControl -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/monitors/memory_monitor.py tests/test_memory_monitor.py
git commit -m "feat: add container stopping logic for memory monitor"
```

---

### Task 1.5: Add Memory Pressure State Machine

**Files:**
- Modify: `src/monitors/memory_monitor.py`
- Modify: `tests/test_memory_monitor.py`

**Step 1: Write tests for state transitions**

Add to `tests/test_memory_monitor.py`:

```python
class TestStateMachine:
    @pytest.mark.asyncio
    @patch("src.monitors.memory_monitor.psutil")
    async def test_normal_to_warning(
        self, mock_psutil, memory_config, mock_docker_client, mock_on_alert, mock_on_ask_restart
    ):
        mock_psutil.virtual_memory.return_value = MagicMock(percent=91.0)

        monitor = MemoryMonitor(
            docker_client=mock_docker_client,
            config=memory_config,
            on_alert=mock_on_alert,
            on_ask_restart=mock_on_ask_restart,
        )

        await monitor._check_memory()

        assert monitor._state == MemoryState.WARNING
        mock_on_alert.assert_called_once()
        assert "91" in mock_on_alert.call_args[0][1]

    @pytest.mark.asyncio
    @patch("src.monitors.memory_monitor.psutil")
    async def test_warning_to_critical(
        self, mock_psutil, memory_config, mock_docker_client, mock_on_alert, mock_on_ask_restart
    ):
        mock_psutil.virtual_memory.return_value = MagicMock(percent=96.0)
        mock_docker_client.containers.list.return_value = []

        monitor = MemoryMonitor(
            docker_client=mock_docker_client,
            config=memory_config,
            on_alert=mock_on_alert,
            on_ask_restart=mock_on_ask_restart,
        )
        monitor._state = MemoryState.WARNING

        await monitor._check_memory()

        assert monitor._state == MemoryState.CRITICAL
        mock_on_alert.assert_called()

    @pytest.mark.asyncio
    @patch("src.monitors.memory_monitor.psutil")
    async def test_returns_to_normal_below_warning(
        self, mock_psutil, memory_config, mock_docker_client, mock_on_alert, mock_on_ask_restart
    ):
        mock_psutil.virtual_memory.return_value = MagicMock(percent=85.0)

        monitor = MemoryMonitor(
            docker_client=mock_docker_client,
            config=memory_config,
            on_alert=mock_on_alert,
            on_ask_restart=mock_on_ask_restart,
        )
        monitor._state = MemoryState.WARNING

        await monitor._check_memory()

        assert monitor._state == MemoryState.NORMAL

    @pytest.mark.asyncio
    @patch("src.monitors.memory_monitor.psutil")
    async def test_recovering_asks_restart_when_safe(
        self, mock_psutil, memory_config, mock_docker_client, mock_on_alert, mock_on_ask_restart
    ):
        mock_psutil.virtual_memory.return_value = MagicMock(percent=75.0)

        monitor = MemoryMonitor(
            docker_client=mock_docker_client,
            config=memory_config,
            on_alert=mock_on_alert,
            on_ask_restart=mock_on_ask_restart,
        )
        monitor._state = MemoryState.RECOVERING
        monitor._killed_containers = ["bitmagnet"]

        await monitor._check_memory()

        mock_on_ask_restart.assert_called_once_with("bitmagnet")
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_memory_monitor.py::TestStateMachine -v`
Expected: FAIL

**Step 3: Implement state machine**

Add to `MemoryMonitor` class:

```python
async def _check_memory(self) -> None:
    """Check memory and handle state transitions."""
    percent = self.get_memory_percent()

    if self._state == MemoryState.NORMAL:
        if percent >= self._config.critical_threshold:
            self._state = MemoryState.CRITICAL
            await self._handle_critical(percent)
        elif percent >= self._config.warning_threshold:
            self._state = MemoryState.WARNING
            await self._handle_warning(percent)

    elif self._state == MemoryState.WARNING:
        if percent >= self._config.critical_threshold:
            self._state = MemoryState.CRITICAL
            await self._handle_critical(percent)
        elif percent < self._config.warning_threshold:
            self._state = MemoryState.NORMAL
            logger.info("Memory returned to normal levels")

    elif self._state == MemoryState.CRITICAL:
        if percent < self._config.warning_threshold:
            if self._killed_containers:
                self._state = MemoryState.RECOVERING
            else:
                self._state = MemoryState.NORMAL

    elif self._state == MemoryState.RECOVERING:
        if percent <= self._config.safe_threshold and self._killed_containers:
            container = self._killed_containers[0]
            await self._on_ask_restart(container)

async def _handle_warning(self, percent: float) -> None:
    """Handle warning state - notify user."""
    killable = ", ".join(self._config.killable_containers) or "none configured"
    message = f"Memory at {percent:.0f}%. Killable containers: {killable}"
    await self._on_alert("Memory Warning", message)

async def _handle_critical(self, percent: float) -> None:
    """Handle critical state - prepare to kill."""
    next_kill = self._get_next_killable()
    if next_kill:
        self._pending_kill = next_kill
        message = (
            f"Memory critical ({percent:.0f}%). "
            f"Will stop {next_kill} in {self._config.kill_delay_seconds} seconds "
            f"to protect priority services. Reply /cancel-kill to abort"
        )
        await self._on_alert("Memory Critical", message)
    else:
        message = f"Memory critical ({percent:.0f}%) but no killable containers available!"
        await self._on_alert("Memory Critical - No Action Available", message)
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_memory_monitor.py::TestStateMachine -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/monitors/memory_monitor.py tests/test_memory_monitor.py
git commit -m "feat: add memory pressure state machine"
```

---

### Task 1.6: Add Kill Countdown and Cancel

**Files:**
- Modify: `src/monitors/memory_monitor.py`
- Modify: `tests/test_memory_monitor.py`

**Step 1: Write tests for kill countdown**

Add to `tests/test_memory_monitor.py`:

```python
class TestKillCountdown:
    @pytest.mark.asyncio
    @patch("src.monitors.memory_monitor.psutil")
    @patch("src.monitors.memory_monitor.asyncio.sleep", new_callable=AsyncMock)
    async def test_kill_after_countdown(
        self, mock_sleep, mock_psutil, memory_config, mock_docker_client, mock_on_alert, mock_on_ask_restart
    ):
        # Memory stays critical
        mock_psutil.virtual_memory.return_value = MagicMock(percent=96.0)

        container = MagicMock()
        container.name = "bitmagnet"
        mock_docker_client.containers.get.return_value = container
        mock_docker_client.containers.list.return_value = [container]

        monitor = MemoryMonitor(
            docker_client=mock_docker_client,
            config=memory_config,
            on_alert=mock_on_alert,
            on_ask_restart=mock_on_ask_restart,
        )
        monitor._state = MemoryState.CRITICAL
        monitor._pending_kill = "bitmagnet"

        await monitor._execute_kill_countdown()

        mock_sleep.assert_called_with(60)  # kill_delay_seconds
        container.stop.assert_called_once()
        assert "bitmagnet" in monitor._killed_containers

    @pytest.mark.asyncio
    @patch("src.monitors.memory_monitor.psutil")
    @patch("src.monitors.memory_monitor.asyncio.sleep", new_callable=AsyncMock)
    async def test_cancel_kill_aborts_countdown(
        self, mock_sleep, mock_psutil, memory_config, mock_docker_client, mock_on_alert, mock_on_ask_restart
    ):
        container = MagicMock()
        mock_docker_client.containers.get.return_value = container

        monitor = MemoryMonitor(
            docker_client=mock_docker_client,
            config=memory_config,
            on_alert=mock_on_alert,
            on_ask_restart=mock_on_ask_restart,
        )
        monitor._pending_kill = "bitmagnet"
        monitor._kill_cancelled = True

        await monitor._execute_kill_countdown()

        container.stop.assert_not_called()
        assert monitor._kill_cancelled is False  # Reset after handling

    def test_cancel_kill_command(
        self, memory_config, mock_docker_client, mock_on_alert, mock_on_ask_restart
    ):
        monitor = MemoryMonitor(
            docker_client=mock_docker_client,
            config=memory_config,
            on_alert=mock_on_alert,
            on_ask_restart=mock_on_ask_restart,
        )
        monitor._pending_kill = "bitmagnet"

        result = monitor.cancel_pending_kill()

        assert result is True
        assert monitor._kill_cancelled is True

    def test_cancel_kill_no_pending(
        self, memory_config, mock_docker_client, mock_on_alert, mock_on_ask_restart
    ):
        monitor = MemoryMonitor(
            docker_client=mock_docker_client,
            config=memory_config,
            on_alert=mock_on_alert,
            on_ask_restart=mock_on_ask_restart,
        )

        result = monitor.cancel_pending_kill()

        assert result is False
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_memory_monitor.py::TestKillCountdown -v`
Expected: FAIL

**Step 3: Implement kill countdown**

Add to `MemoryMonitor` class:

```python
async def _execute_kill_countdown(self) -> None:
    """Execute the kill countdown for pending container."""
    if not self._pending_kill:
        return

    container_name = self._pending_kill

    # Wait for kill delay
    await asyncio.sleep(self._config.kill_delay_seconds)

    # Check if cancelled
    if self._kill_cancelled:
        logger.info(f"Kill of {container_name} was cancelled")
        self._kill_cancelled = False
        self._pending_kill = None
        return

    # Check if memory is still critical
    if self.get_memory_percent() >= self._config.critical_threshold:
        await self._stop_container(container_name)
        percent = self.get_memory_percent()
        await self._on_alert(
            "Container Stopped",
            f"Stopped {container_name} to free memory. Memory now at {percent:.0f}%"
        )
    else:
        logger.info(f"Memory recovered, not killing {container_name}")

    self._pending_kill = None

def cancel_pending_kill(self) -> bool:
    """Cancel a pending kill. Returns True if there was one to cancel."""
    if self._pending_kill:
        self._kill_cancelled = True
        return True
    return False

def get_pending_kill(self) -> str | None:
    """Get the name of the container pending kill, if any."""
    return self._pending_kill
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_memory_monitor.py::TestKillCountdown -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/monitors/memory_monitor.py tests/test_memory_monitor.py
git commit -m "feat: add kill countdown with cancel support"
```

---

### Task 1.7: Add Restart Confirmation Handler

**Files:**
- Modify: `src/monitors/memory_monitor.py`
- Modify: `tests/test_memory_monitor.py`

**Step 1: Write tests for restart handling**

Add to `tests/test_memory_monitor.py`:

```python
class TestRestartHandling:
    @pytest.mark.asyncio
    async def test_confirm_restart_starts_container(
        self, memory_config, mock_docker_client, mock_on_alert, mock_on_ask_restart
    ):
        container = MagicMock()
        mock_docker_client.containers.get.return_value = container

        monitor = MemoryMonitor(
            docker_client=mock_docker_client,
            config=memory_config,
            on_alert=mock_on_alert,
            on_ask_restart=mock_on_ask_restart,
        )
        monitor._killed_containers = ["bitmagnet", "obsidian"]
        monitor._state = MemoryState.RECOVERING

        await monitor.confirm_restart("bitmagnet")

        container.start.assert_called_once()
        assert "bitmagnet" not in monitor._killed_containers
        assert "obsidian" in monitor._killed_containers

    @pytest.mark.asyncio
    async def test_decline_restart_removes_from_list(
        self, memory_config, mock_docker_client, mock_on_alert, mock_on_ask_restart
    ):
        monitor = MemoryMonitor(
            docker_client=mock_docker_client,
            config=memory_config,
            on_alert=mock_on_alert,
            on_ask_restart=mock_on_ask_restart,
        )
        monitor._killed_containers = ["bitmagnet"]
        monitor._state = MemoryState.RECOVERING

        await monitor.decline_restart("bitmagnet")

        assert "bitmagnet" not in monitor._killed_containers
        assert monitor._state == MemoryState.NORMAL

    def test_get_killed_containers(
        self, memory_config, mock_docker_client, mock_on_alert, mock_on_ask_restart
    ):
        monitor = MemoryMonitor(
            docker_client=mock_docker_client,
            config=memory_config,
            on_alert=mock_on_alert,
            on_ask_restart=mock_on_ask_restart,
        )
        monitor._killed_containers = ["bitmagnet", "obsidian"]

        result = monitor.get_killed_containers()

        assert result == ["bitmagnet", "obsidian"]
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_memory_monitor.py::TestRestartHandling -v`
Expected: FAIL

**Step 3: Implement restart handling**

Add to `MemoryMonitor` class:

```python
async def confirm_restart(self, name: str) -> bool:
    """Confirm restart of a killed container.

    Returns True if container was started successfully.
    """
    if name not in self._killed_containers:
        return False

    try:
        container = self._docker.containers.get(name)
        container.start()
        self._killed_containers.remove(name)
        logger.info(f"Restarted container {name}")

        if not self._killed_containers:
            self._state = MemoryState.NORMAL

        return True
    except Exception as e:
        logger.error(f"Failed to restart container {name}: {e}")
        return False

async def decline_restart(self, name: str) -> None:
    """Decline restart of a killed container."""
    if name in self._killed_containers:
        self._killed_containers.remove(name)
        logger.info(f"User declined restart of {name}")

    if not self._killed_containers:
        self._state = MemoryState.NORMAL

def get_killed_containers(self) -> list[str]:
    """Get list of containers killed in this pressure event."""
    return self._killed_containers.copy()
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_memory_monitor.py::TestRestartHandling -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/monitors/memory_monitor.py tests/test_memory_monitor.py
git commit -m "feat: add restart confirmation handling"
```

---

### Task 1.8: Add Polling Loop

**Files:**
- Modify: `src/monitors/memory_monitor.py`
- Modify: `tests/test_memory_monitor.py`

**Step 1: Write test for polling loop**

Add to `tests/test_memory_monitor.py`:

```python
class TestPollingLoop:
    @pytest.mark.asyncio
    @patch("src.monitors.memory_monitor.psutil")
    @patch("src.monitors.memory_monitor.asyncio.sleep", new_callable=AsyncMock)
    async def test_start_polls_memory(
        self, mock_sleep, mock_psutil, memory_config, mock_docker_client, mock_on_alert, mock_on_ask_restart
    ):
        mock_psutil.virtual_memory.return_value = MagicMock(percent=50.0)

        # Make sleep raise after first call to stop loop
        call_count = 0

        async def sleep_side_effect(duration):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise asyncio.CancelledError()

        mock_sleep.side_effect = sleep_side_effect

        monitor = MemoryMonitor(
            docker_client=mock_docker_client,
            config=memory_config,
            on_alert=mock_on_alert,
            on_ask_restart=mock_on_ask_restart,
        )

        with pytest.raises(asyncio.CancelledError):
            await monitor.start()

        assert mock_psutil.virtual_memory.called

    def test_stop_sets_running_false(
        self, memory_config, mock_docker_client, mock_on_alert, mock_on_ask_restart
    ):
        monitor = MemoryMonitor(
            docker_client=mock_docker_client,
            config=memory_config,
            on_alert=mock_on_alert,
            on_ask_restart=mock_on_ask_restart,
        )
        monitor._running = True

        monitor.stop()

        assert monitor._running is False
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_memory_monitor.py::TestPollingLoop -v`
Expected: FAIL

**Step 3: Implement polling loop**

Add to `MemoryMonitor` class:

```python
async def start(self) -> None:
    """Start the memory monitoring loop."""
    if not self.is_enabled():
        logger.info("Memory monitoring disabled")
        return

    self._running = True
    logger.info("Memory monitor started")

    while self._running:
        try:
            await self._check_memory()

            # Handle kill countdown if in critical state with pending kill
            if self._state == MemoryState.CRITICAL and self._pending_kill:
                await self._execute_kill_countdown()

                # After kill, wait for stabilization
                if self._killed_containers:
                    self._state = MemoryState.RECOVERING
                    await asyncio.sleep(self._config.stabilization_wait)
                    continue

            await asyncio.sleep(10)  # Check every 10 seconds

        except asyncio.CancelledError:
            logger.info("Memory monitor cancelled")
            raise
        except Exception as e:
            logger.error(f"Error in memory monitor: {e}")
            await asyncio.sleep(30)

def stop(self) -> None:
    """Stop the memory monitoring loop."""
    self._running = False
    logger.info("Memory monitor stopped")
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_memory_monitor.py::TestPollingLoop -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/monitors/memory_monitor.py tests/test_memory_monitor.py
git commit -m "feat: add memory monitor polling loop"
```

---

### Task 1.9: Add /cancel-kill Command

**Files:**
- Create: `src/bot/memory_commands.py`
- Test: `tests/test_memory_commands.py` (new)

**Step 1: Write the failing test**

Create `tests/test_memory_commands.py`:

```python
"""Tests for memory management bot commands."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from aiogram.types import Message


@pytest.fixture
def mock_message():
    message = MagicMock(spec=Message)
    message.answer = AsyncMock()
    message.from_user = MagicMock()
    message.from_user.id = 12345
    return message


@pytest.fixture
def mock_memory_monitor():
    monitor = MagicMock()
    monitor.cancel_pending_kill = MagicMock(return_value=True)
    monitor.get_pending_kill = MagicMock(return_value="bitmagnet")
    return monitor


class TestCancelKillCommand:
    @pytest.mark.asyncio
    async def test_cancel_kill_success(self, mock_message, mock_memory_monitor):
        from src.bot.memory_commands import cancel_kill_command

        handler = cancel_kill_command(mock_memory_monitor)
        await handler(mock_message)

        mock_memory_monitor.cancel_pending_kill.assert_called_once()
        mock_message.answer.assert_called_once()
        assert "cancelled" in mock_message.answer.call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_cancel_kill_nothing_pending(self, mock_message):
        from src.bot.memory_commands import cancel_kill_command

        monitor = MagicMock()
        monitor.cancel_pending_kill = MagicMock(return_value=False)
        monitor.get_pending_kill = MagicMock(return_value=None)

        handler = cancel_kill_command(monitor)
        await handler(mock_message)

        assert "no pending" in mock_message.answer.call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_cancel_kill_monitor_disabled(self, mock_message):
        from src.bot.memory_commands import cancel_kill_command

        handler = cancel_kill_command(None)
        await handler(mock_message)

        assert "not enabled" in mock_message.answer.call_args[0][0].lower()
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_memory_commands.py -v`
Expected: FAIL

**Step 3: Write the implementation**

Create `src/bot/memory_commands.py`:

```python
"""Bot commands for memory management."""

import logging
from typing import Callable, Awaitable, TYPE_CHECKING

from aiogram.types import Message

if TYPE_CHECKING:
    from src.monitors.memory_monitor import MemoryMonitor

logger = logging.getLogger(__name__)


def cancel_kill_command(
    memory_monitor: "MemoryMonitor | None",
) -> Callable[[Message], Awaitable[None]]:
    """Factory for /cancel-kill command handler."""

    async def handler(message: Message) -> None:
        if memory_monitor is None:
            await message.answer("Memory management is not enabled.")
            return

        pending = memory_monitor.get_pending_kill()
        if memory_monitor.cancel_pending_kill():
            await message.answer(f"Cancelled pending kill of {pending}.")
            logger.info(f"User cancelled kill of {pending}")
        else:
            await message.answer("No pending kill to cancel.")

    return handler
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_memory_commands.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/bot/memory_commands.py tests/test_memory_commands.py
git commit -m "feat: add /cancel-kill command"
```

---

### Task 1.10: Integrate Memory Monitor into Main

**Files:**
- Modify: `src/main.py`
- Modify: `src/bot/telegram_bot.py`
- Modify: `config/config.yaml`

**Step 1: Add memory management section to config.yaml**

Add to `config/config.yaml`:

```yaml
# Memory pressure management
memory_management:
  enabled: false  # Set to true to enable
  warning_threshold: 90
  critical_threshold: 95
  safe_threshold: 80
  kill_delay_seconds: 60
  stabilization_wait: 180
  priority_containers:
    - plex
    - mariadb
    - postgresql14
  killable_containers: []  # Add containers that can be killed, in kill order
```

**Step 2: Register command in telegram_bot.py**

Add import at top of `src/bot/telegram_bot.py`:

```python
from src.bot.memory_commands import cancel_kill_command
```

Modify `register_commands` function to accept memory_monitor parameter and register the command:

```python
def register_commands(
    dp: Dispatcher,
    state_manager: "ContainerStateManager",
    docker_client: docker.DockerClient,
    protected_containers: list[str],
    anthropic_client: "anthropic.Anthropic | None" = None,
    resource_monitor: "ResourceMonitor | None" = None,
    ignore_manager: "IgnoreManager | None" = None,
    recent_errors_buffer: "RecentErrorsBuffer | None" = None,
    mute_manager: "MuteManager | None" = None,
    unraid_system_monitor: "UnraidSystemMonitor | None" = None,
    server_mute_manager: "ServerMuteManager | None" = None,
    array_mute_manager: "ArrayMuteManager | None" = None,
    memory_monitor: "MemoryMonitor | None" = None,  # Add this
) -> tuple["ConfirmationState", "DiagnosticService | None"]:
```

Add registration inside the function:

```python
# Register memory commands
dp.message.register(
    cancel_kill_command(memory_monitor),
    Command("cancel-kill"),
)
```

**Step 3: Initialize in main.py**

Add import:

```python
from src.monitors.memory_monitor import MemoryMonitor
```

Add initialization after resource_monitor setup:

```python
# Initialize memory monitor if enabled
memory_monitor = None
memory_config = config.memory_management
if memory_config.enabled:
    async def on_memory_alert(title: str, message: str) -> None:
        chat_id = chat_id_store.get_chat_id()
        if chat_id:
            alert_text = f"{'🔴' if 'Critical' in title else '⚠️'} *{title}*\n\n{message}"
            await bot.send_message(chat_id, alert_text, parse_mode="Markdown")

    async def on_ask_restart(container: str) -> None:
        chat_id = chat_id_store.get_chat_id()
        if chat_id:
            text = f"💾 Memory now at safe levels. Restart {container}?"
            # TODO: Add inline keyboard with Yes/No buttons
            await bot.send_message(chat_id, text)

    memory_monitor = MemoryMonitor(
        docker_client=monitor._client,
        config=memory_config,
        on_alert=on_memory_alert,
        on_ask_restart=on_ask_restart,
    )
    logger.info("Memory monitoring enabled")
```

Pass to register_commands:

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
    unraid_system_monitor=unraid_system_monitor,
    server_mute_manager=server_mute_manager,
    array_mute_manager=array_mute_manager,
    memory_monitor=memory_monitor,  # Add this
)
```

Start monitor task:

```python
memory_monitor_task = None
if memory_monitor is not None:
    memory_monitor_task = asyncio.create_task(memory_monitor.start())
```

Add cleanup in finally block:

```python
if memory_monitor is not None:
    memory_monitor.stop()
if memory_monitor_task is not None:
    memory_monitor_task.cancel()
    try:
        await memory_monitor_task
    except asyncio.CancelledError:
        pass
```

**Step 4: Run all tests**

Run: `pytest tests/ -v`
Expected: All tests pass

**Step 5: Commit**

```bash
git add src/main.py src/bot/telegram_bot.py config/config.yaml
git commit -m "feat: integrate memory monitor into application"
```

---

## Feature 2: Smart Ignore Pattern Generation

### Task 2.1: Update Ignore Storage Format

**Files:**
- Modify: `src/alerts/ignore_manager.py`
- Modify: `tests/test_ignore_manager.py`

**Step 1: Write failing test for new format**

Add to `tests/test_ignore_manager.py`:

```python
class TestIgnorePatternFormat:
    def test_add_ignore_with_pattern_object(self, tmp_path):
        json_path = tmp_path / "ignores.json"
        manager = IgnoreManager(config_ignores={}, json_path=str(json_path))

        result = manager.add_ignore_pattern(
            container="sonarr",
            pattern="Connection refused to .* on port \\d+",
            match_type="regex",
            explanation="Connection refused errors to any host",
        )

        assert result is True
        ignores = manager.get_all_ignores("sonarr")
        assert len(ignores) == 1
        pattern, source, explanation = ignores[0]
        assert pattern == "Connection refused to .* on port \\d+"
        assert source == "runtime"
        assert explanation == "Connection refused errors to any host"

    def test_is_ignored_with_regex_pattern(self, tmp_path):
        json_path = tmp_path / "ignores.json"
        manager = IgnoreManager(config_ignores={}, json_path=str(json_path))

        manager.add_ignore_pattern(
            container="sonarr",
            pattern="Connection refused to .* on port \\d+",
            match_type="regex",
            explanation="Connection errors",
        )

        # Should match variations
        assert manager.is_ignored("sonarr", "Connection refused to api.example.com on port 443")
        assert manager.is_ignored("sonarr", "Connection refused to localhost on port 8080")
        assert not manager.is_ignored("sonarr", "Some other error")

    def test_backward_compatible_with_old_format(self, tmp_path):
        # Create old format file
        json_path = tmp_path / "ignores.json"
        json_path.write_text('{"sonarr": ["simple error text"]}')

        manager = IgnoreManager(config_ignores={}, json_path=str(json_path))

        # Old format should still work as substring match
        assert manager.is_ignored("sonarr", "This has simple error text in it")
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_ignore_manager.py::TestIgnorePatternFormat -v`
Expected: FAIL

**Step 3: Update IgnoreManager implementation**

Update `src/alerts/ignore_manager.py`:

```python
"""Manages error ignore patterns from config and runtime JSON."""

import json
import re
import logging
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class IgnorePattern:
    """An ignore pattern with metadata."""
    pattern: str
    match_type: str  # "substring" or "regex"
    explanation: str
    added: str  # ISO timestamp


class IgnoreManager:
    """Manages error ignore patterns from config and runtime JSON."""

    def __init__(self, config_ignores: dict[str, list[str]], json_path: str):
        self._config_ignores = config_ignores
        self._json_path = Path(json_path)
        self._runtime_ignores: dict[str, list[IgnorePattern]] = {}
        self._load_runtime_ignores()

    def is_ignored(self, container: str, message: str) -> bool:
        """Check if message should be ignored."""
        message_lower = message.lower()

        # Check config ignores (always substring)
        for pattern in self._config_ignores.get(container, []):
            if pattern.lower() in message_lower:
                return True

        # Check runtime ignores
        for ignore in self._runtime_ignores.get(container, []):
            if ignore.match_type == "regex":
                try:
                    if re.search(ignore.pattern, message, re.IGNORECASE):
                        return True
                except re.error:
                    logger.warning(f"Invalid regex pattern: {ignore.pattern}")
            else:  # substring
                if ignore.pattern.lower() in message_lower:
                    return True

        return False

    def add_ignore(self, container: str, message: str) -> bool:
        """Add a simple substring ignore (backward compatible)."""
        return self.add_ignore_pattern(
            container=container,
            pattern=message,
            match_type="substring",
            explanation="",
        )

    def add_ignore_pattern(
        self,
        container: str,
        pattern: str,
        match_type: str,
        explanation: str,
    ) -> bool:
        """Add a runtime ignore pattern.

        Returns True if added, False if already exists.
        """
        if container not in self._runtime_ignores:
            self._runtime_ignores[container] = []

        # Check if already exists
        for existing in self._runtime_ignores[container]:
            if existing.pattern.lower() == pattern.lower():
                return False

        ignore = IgnorePattern(
            pattern=pattern,
            match_type=match_type,
            explanation=explanation,
            added=datetime.now(timezone.utc).isoformat(),
        )
        self._runtime_ignores[container].append(ignore)
        self._save_runtime_ignores()
        logger.info(f"Added ignore for {container}: {pattern} ({match_type})")
        return True

    def get_all_ignores(self, container: str) -> list[tuple[str, str, str]]:
        """Get all ignores as (pattern, source, explanation) tuples."""
        ignores = []

        for pattern in self._config_ignores.get(container, []):
            ignores.append((pattern, "config", ""))

        for ignore in self._runtime_ignores.get(container, []):
            ignores.append((ignore.pattern, "runtime", ignore.explanation))

        return ignores

    def _load_runtime_ignores(self) -> None:
        """Load runtime ignores from JSON file."""
        if not self._json_path.exists():
            self._runtime_ignores = {}
            return

        try:
            with open(self._json_path, encoding="utf-8") as f:
                data = json.load(f)

            self._runtime_ignores = {}
            for container, patterns in data.items():
                self._runtime_ignores[container] = []
                for item in patterns:
                    if isinstance(item, str):
                        # Old format - convert to new
                        self._runtime_ignores[container].append(
                            IgnorePattern(
                                pattern=item,
                                match_type="substring",
                                explanation="",
                                added="",
                            )
                        )
                    elif isinstance(item, dict):
                        # New format
                        self._runtime_ignores[container].append(
                            IgnorePattern(
                                pattern=item["pattern"],
                                match_type=item.get("match_type", "substring"),
                                explanation=item.get("explanation", ""),
                                added=item.get("added", ""),
                            )
                        )

        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Failed to load runtime ignores: {e}")
            self._runtime_ignores = {}

    def _save_runtime_ignores(self) -> None:
        """Save runtime ignores to JSON file."""
        self._json_path.parent.mkdir(parents=True, exist_ok=True)

        data = {}
        for container, patterns in self._runtime_ignores.items():
            data[container] = [
                {
                    "pattern": p.pattern,
                    "match_type": p.match_type,
                    "explanation": p.explanation,
                    "added": p.added,
                }
                for p in patterns
            ]

        try:
            with open(self._json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except IOError as e:
            logger.error(f"Failed to save runtime ignores: {e}")
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_ignore_manager.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/alerts/ignore_manager.py tests/test_ignore_manager.py
git commit -m "feat: update ignore manager to support regex patterns with explanations"
```

---

### Task 2.2: Create Pattern Analyzer

**Files:**
- Create: `src/analysis/pattern_analyzer.py`
- Test: `tests/test_pattern_analyzer.py` (new)

**Step 1: Write the failing test**

Create `tests/test_pattern_analyzer.py`:

```python
"""Tests for Haiku-based pattern analysis."""

import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture
def mock_anthropic_client():
    client = MagicMock()
    return client


class TestPatternAnalyzer:
    @pytest.mark.asyncio
    @patch("src.analysis.pattern_analyzer.anthropic")
    async def test_analyze_returns_pattern(self, mock_anthropic_module, mock_anthropic_client):
        from src.analysis.pattern_analyzer import PatternAnalyzer

        # Mock Haiku response
        mock_response = MagicMock()
        mock_response.content = [MagicMock()]
        mock_response.content[0].text = '''```json
{
    "pattern": "Connection refused to .* on port \\\\d+",
    "match_type": "regex",
    "explanation": "Connection refused errors to any host on any port"
}
```'''
        mock_anthropic_client.messages.create.return_value = mock_response

        analyzer = PatternAnalyzer(mock_anthropic_client)

        result = await analyzer.analyze_error(
            container="sonarr",
            error_message="Connection refused to api.example.com on port 443",
            recent_logs=["log line 1", "log line 2"],
        )

        assert result is not None
        assert result["pattern"] == "Connection refused to .* on port \\d+"
        assert result["match_type"] == "regex"
        assert "Connection refused" in result["explanation"]

    @pytest.mark.asyncio
    @patch("src.analysis.pattern_analyzer.anthropic")
    async def test_analyze_returns_substring_for_static_errors(
        self, mock_anthropic_module, mock_anthropic_client
    ):
        from src.analysis.pattern_analyzer import PatternAnalyzer

        mock_response = MagicMock()
        mock_response.content = [MagicMock()]
        mock_response.content[0].text = '''```json
{
    "pattern": "Database connection pool exhausted",
    "match_type": "substring",
    "explanation": "Database pool exhaustion errors"
}
```'''
        mock_anthropic_client.messages.create.return_value = mock_response

        analyzer = PatternAnalyzer(mock_anthropic_client)

        result = await analyzer.analyze_error(
            container="app",
            error_message="Database connection pool exhausted",
            recent_logs=[],
        )

        assert result["match_type"] == "substring"

    @pytest.mark.asyncio
    async def test_analyze_returns_none_when_no_client(self):
        from src.analysis.pattern_analyzer import PatternAnalyzer

        analyzer = PatternAnalyzer(None)

        result = await analyzer.analyze_error(
            container="sonarr",
            error_message="Some error",
            recent_logs=[],
        )

        assert result is None
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_pattern_analyzer.py -v`
Expected: FAIL

**Step 3: Write the implementation**

Create `src/analysis/pattern_analyzer.py`:

```python
"""Haiku-based pattern analyzer for generating ignore patterns."""

import json
import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import anthropic

logger = logging.getLogger(__name__)

ANALYSIS_PROMPT = """Analyze this error from a Docker container log and create a pattern to match it and similar variations.

Container: {container}
Error: {error_message}
Recent logs for context:
{recent_logs}

Return ONLY a JSON object (no markdown, no explanation):
{{
    "pattern": "the regex or substring pattern",
    "match_type": "regex" or "substring",
    "explanation": "human-readable description of what this ignores"
}}

Guidelines:
- Prefer simple substrings when the error message is static (no variable parts)
- Use regex only when there are variable parts like timestamps, IPs, file paths, ports, counts
- For regex, use Python regex syntax
- Keep patterns as simple as possible while still matching variations
- The explanation should be concise (under 50 words)"""


class PatternAnalyzer:
    """Uses Claude Haiku to analyze errors and generate ignore patterns."""

    def __init__(self, anthropic_client: "anthropic.Anthropic | None"):
        self._client = anthropic_client

    async def analyze_error(
        self,
        container: str,
        error_message: str,
        recent_logs: list[str],
    ) -> dict | None:
        """Analyze an error and generate an ignore pattern.

        Returns:
            Dict with pattern, match_type, explanation or None if analysis failed.
        """
        if self._client is None:
            logger.warning("No Anthropic client available for pattern analysis")
            return None

        logs_text = "\n".join(recent_logs[-30:]) if recent_logs else "(no recent logs)"

        prompt = ANALYSIS_PROMPT.format(
            container=container,
            error_message=error_message,
            recent_logs=logs_text,
        )

        try:
            response = self._client.messages.create(
                model="claude-3-5-haiku-20241022",
                max_tokens=500,
                messages=[{"role": "user", "content": prompt}],
            )

            text = response.content[0].text

            # Extract JSON from response (may be wrapped in markdown)
            json_match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
            if not json_match:
                logger.error(f"No JSON found in Haiku response: {text}")
                return None

            result = json.loads(json_match.group())

            # Validate required fields
            if not all(k in result for k in ("pattern", "match_type", "explanation")):
                logger.error(f"Missing fields in Haiku response: {result}")
                return None

            # Validate regex if specified
            if result["match_type"] == "regex":
                try:
                    re.compile(result["pattern"])
                except re.error as e:
                    logger.warning(f"Invalid regex from Haiku, falling back to substring: {e}")
                    result["match_type"] = "substring"

            return result

        except Exception as e:
            logger.error(f"Error analyzing pattern with Haiku: {e}")
            return None
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_pattern_analyzer.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/analysis/pattern_analyzer.py tests/test_pattern_analyzer.py
git commit -m "feat: add Haiku-based pattern analyzer for smart ignores"
```

---

### Task 2.3: Update Ignore Command to Use Analyzer

**Files:**
- Modify: `src/bot/ignore_command.py`
- Modify: `tests/test_ignore_command.py`

**Step 1: Write failing test**

Add to `tests/test_ignore_command.py`:

```python
class TestIgnoreWithAnalysis:
    @pytest.mark.asyncio
    async def test_ignore_selection_uses_analyzer(self, tmp_path):
        from src.bot.ignore_command import ignore_selection_handler, IgnoreSelectionState
        from src.alerts.ignore_manager import IgnoreManager
        from unittest.mock import AsyncMock, MagicMock

        # Setup
        ignore_manager = IgnoreManager({}, str(tmp_path / "ignores.json"))
        selection_state = IgnoreSelectionState()
        selection_state.set_pending(123, "sonarr", ["Connection refused to api.example.com on port 443"])

        mock_analyzer = MagicMock()
        mock_analyzer.analyze_error = AsyncMock(return_value={
            "pattern": "Connection refused to .* on port \\d+",
            "match_type": "regex",
            "explanation": "Connection refused errors",
        })

        message = MagicMock()
        message.from_user = MagicMock()
        message.from_user.id = 123
        message.text = "1"
        message.answer = AsyncMock()

        handler = ignore_selection_handler(ignore_manager, selection_state, mock_analyzer)
        await handler(message)

        # Verify analyzer was called
        mock_analyzer.analyze_error.assert_called_once()

        # Verify pattern was added with analysis result
        ignores = ignore_manager.get_all_ignores("sonarr")
        assert len(ignores) == 1
        assert ignores[0][0] == "Connection refused to .* on port \\d+"
        assert ignores[0][2] == "Connection refused errors"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_ignore_command.py::TestIgnoreWithAnalysis -v`
Expected: FAIL

**Step 3: Update ignore_command.py**

Update the `ignore_selection_handler` function to accept and use the analyzer:

```python
def ignore_selection_handler(
    ignore_manager: "IgnoreManager",
    selection_state: "IgnoreSelectionState",
    pattern_analyzer: "PatternAnalyzer | None" = None,
) -> Callable[[Message], Awaitable[None]]:
    """Factory for ignore selection follow-up handler."""

    async def handler(message: Message) -> None:
        user_id = message.from_user.id if message.from_user else 0

        if not selection_state.has_pending(user_id):
            return

        pending = selection_state.get_pending(user_id)
        if not pending:
            return

        container, errors = pending
        text = (message.text or "").strip().lower()

        # Parse selection
        if text == "all":
            indices = list(range(len(errors)))
        else:
            try:
                indices = [int(x.strip()) - 1 for x in text.split(",")]
                if any(i < 0 or i >= len(errors) for i in indices):
                    await message.answer("Invalid selection. Numbers must be from the list.")
                    return
            except ValueError:
                await message.answer("Invalid input. Use numbers like '1,3' or 'all'.")
                return

        selection_state.clear_pending(user_id)

        # Process each selected error
        added = []
        for i in indices:
            error = errors[i]

            # Try to analyze with Haiku
            if pattern_analyzer is not None:
                result = await pattern_analyzer.analyze_error(
                    container=container,
                    error_message=error,
                    recent_logs=[],  # Could pass more context here
                )

                if result:
                    if ignore_manager.add_ignore_pattern(
                        container=container,
                        pattern=result["pattern"],
                        match_type=result["match_type"],
                        explanation=result["explanation"],
                    ):
                        added.append((result["pattern"], result["explanation"]))
                    continue

            # Fallback to simple substring
            if ignore_manager.add_ignore(container, error):
                added.append((error, ""))

        if added:
            lines = [f"✅ *Ignored for {container}:*\n"]
            for pattern, explanation in added:
                display = pattern[:60] + "..." if len(pattern) > 60 else pattern
                lines.append(f"  • `{display}`")
                if explanation:
                    lines.append(f"    _{explanation}_")
            await message.answer("\n".join(lines), parse_mode="Markdown")
        else:
            await message.answer("Those errors are already ignored.")

    return handler
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_ignore_command.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/bot/ignore_command.py tests/test_ignore_command.py
git commit -m "feat: integrate pattern analyzer into ignore command"
```

---

### Task 2.4: Add "Ignore Similar" Button to Alerts

**Files:**
- Modify: `src/alerts/manager.py`
- Modify: `src/bot/telegram_bot.py`
- Test: `tests/test_ignore_button.py` (new)

**Step 1: Write failing test**

Create `tests/test_ignore_button.py`:

```python
"""Tests for ignore similar button on alerts."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestIgnoreSimilarButton:
    @pytest.mark.asyncio
    async def test_log_error_alert_includes_ignore_button(self):
        from src.alerts.manager import AlertManager
        from aiogram.types import InlineKeyboardMarkup

        bot = MagicMock()
        bot.send_message = AsyncMock()

        manager = AlertManager(bot, chat_id=123)

        await manager.send_log_error_alert(
            container_name="sonarr",
            error_line="Connection refused to api.example.com",
            suppressed_count=0,
        )

        # Check that inline keyboard was passed
        call_kwargs = bot.send_message.call_args[1]
        assert "reply_markup" in call_kwargs
        markup = call_kwargs["reply_markup"]
        assert isinstance(markup, InlineKeyboardMarkup)

        # Check button exists
        buttons = markup.inline_keyboard[0]
        assert any("ignore" in b.callback_data.lower() for b in buttons)
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_ignore_button.py -v`
Expected: FAIL

**Step 3: Update AlertManager to include button**

In `src/alerts/manager.py`, add import:

```python
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
```

Update `send_log_error_alert` method:

```python
async def send_log_error_alert(
    self,
    container_name: str,
    error_line: str,
    suppressed_count: int = 0,
) -> None:
    """Send a log error alert with ignore button."""
    total_errors = suppressed_count + 1

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

    # Create inline keyboard with ignore button
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔇 Ignore Similar",
                    callback_data=f"ignore_similar:{container_name}:{error_line[:50]}",
                )
            ]
        ]
    )

    try:
        await self.bot.send_message(
            chat_id=self.chat_id,
            text=text,
            parse_mode="Markdown",
            reply_markup=keyboard,
        )
        logger.info(f"Sent log error alert for {container_name}")
    except Exception as e:
        logger.error(f"Failed to send log error alert: {e}")
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_ignore_button.py -v`
Expected: PASS

**Step 5: Add callback handler for button**

Create callback handler in `src/bot/ignore_command.py`:

```python
def ignore_similar_callback(
    ignore_manager: "IgnoreManager",
    pattern_analyzer: "PatternAnalyzer | None",
    recent_errors_buffer: "RecentErrorsBuffer",
) -> Callable:
    """Factory for ignore similar button callback."""

    async def handler(callback: CallbackQuery) -> None:
        data = callback.data or ""
        parts = data.split(":", 2)
        if len(parts) < 3:
            await callback.answer("Invalid callback data")
            return

        _, container, error_preview = parts

        # Get full error from recent buffer
        recent = recent_errors_buffer.get_recent(container)
        full_error = None
        for error in recent:
            if error.startswith(error_preview):
                full_error = error
                break

        if not full_error:
            full_error = error_preview

        # Analyze with Haiku
        if pattern_analyzer:
            result = await pattern_analyzer.analyze_error(
                container=container,
                error_message=full_error,
                recent_logs=recent,
            )

            if result:
                ignore_manager.add_ignore_pattern(
                    container=container,
                    pattern=result["pattern"],
                    match_type=result["match_type"],
                    explanation=result["explanation"],
                )
                await callback.message.answer(
                    f"✅ Ignoring: {result['explanation']}\n"
                    f"Pattern: `{result['pattern']}`",
                    parse_mode="Markdown",
                )
                await callback.answer("Pattern added")
                return

        # Fallback to substring
        ignore_manager.add_ignore(container, full_error)
        await callback.message.answer(f"✅ Ignoring: `{full_error[:60]}...`", parse_mode="Markdown")
        await callback.answer("Added to ignore list")

    return handler
```

Register in `telegram_bot.py`:

```python
from aiogram import F

# In register_commands:
dp.callback_query.register(
    ignore_similar_callback(ignore_manager, pattern_analyzer, recent_errors_buffer),
    F.data.startswith("ignore_similar:"),
)
```

**Step 6: Commit**

```bash
git add src/alerts/manager.py src/bot/ignore_command.py src/bot/telegram_bot.py tests/test_ignore_button.py
git commit -m "feat: add ignore similar button to error alerts"
```

---

## Feature 3: Persistent Storage with Bind Volumes

### Task 3.1: Update Docker Compose

**Files:**
- Modify: `docker-compose.yml`

**Step 1: Update docker-compose.yml**

```yaml
services:
  unraid-monitor-bot:
    image: unraid-monitor-bot:latest
    container_name: unraid-monitor-bot
    restart: unless-stopped
    environment:
      - TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
      - UNRAID_API_KEY=${UNRAID_API_KEY}
      - TZ=Europe/London
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - /mnt/user/appdata/unraid-monitor/config:/app/config
      - /mnt/user/appdata/unraid-monitor/data:/app/data
    networks:
      - docknet

networks:
  docknet:
    external: true
```

**Step 2: Commit**

```bash
git add docker-compose.yml
git commit -m "feat: update docker-compose for bind mount storage"
```

---

### Task 3.2: Add Default Config Generation

**Files:**
- Modify: `src/config.py`
- Test: `tests/test_default_config.py` (new)

**Step 1: Write failing test**

Create `tests/test_default_config.py`:

```python
"""Tests for default config generation."""

import pytest
from pathlib import Path


class TestDefaultConfigGeneration:
    def test_generate_default_config(self, tmp_path):
        from src.config import generate_default_config

        config_path = tmp_path / "config.yaml"

        generate_default_config(str(config_path))

        assert config_path.exists()
        content = config_path.read_text()

        # Check key sections exist
        assert "monitoring:" in content
        assert "log_watching:" in content
        assert "memory_management:" in content
        assert "unraid:" in content

        # Check it's valid YAML with comments
        assert "#" in content  # Has comments

    def test_generate_default_does_not_overwrite(self, tmp_path):
        from src.config import generate_default_config

        config_path = tmp_path / "config.yaml"
        config_path.write_text("existing: content")

        generate_default_config(str(config_path))

        # Should not overwrite
        assert config_path.read_text() == "existing: content"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_default_config.py -v`
Expected: FAIL

**Step 3: Implement default config generation**

Add to `src/config.py`:

```python
DEFAULT_CONFIG_TEMPLATE = '''# Unraid Monitor Bot Configuration
# Generated automatically on first run

monitoring:
  health_check_interval: 60  # seconds

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
  ignore_patterns:
    - "DeprecationWarning"
    - "DEBUG"
  cooldown_seconds: 900

# Memory pressure management
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
  host: "192.168.0.190"
  port: 443
  use_ssl: true
  verify_ssl: false
  polling:
    system: 30
    array: 300
    ups: 60
  thresholds:
    cpu_temp: 80
    cpu_usage: 95
    memory_usage: 90
    disk_temp: 50
    array_usage: 85
    ups_battery: 30
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
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_default_config.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/config.py tests/test_default_config.py
git commit -m "feat: add default config generation"
```

---

### Task 3.3: Add First-Run Detection in Main

**Files:**
- Modify: `src/main.py`

**Step 1: Update main.py to generate config and notify**

Add near the start of `main()`:

```python
from src.config import Settings, AppConfig, generate_default_config

async def main() -> None:
    # Check for first run and generate default config if needed
    config_path = os.environ.get("CONFIG_PATH", "config/config.yaml")
    first_run = generate_default_config(config_path)
    if first_run:
        logger.info(f"Created default config at {config_path}")

    # Load configuration
    try:
        settings = Settings()
        config = AppConfig(settings)
    except Exception as e:
        logger.error(f"Failed to load configuration: {e}")
        sys.exit(1)

    # ... rest of main ...

    # After bot is ready, send first-run message
    if first_run and config.telegram_bot_token:
        async def send_welcome():
            # Wait a bit for chat_id to be available
            await asyncio.sleep(5)
            chat_id = chat_id_store.get_chat_id()
            if chat_id:
                await bot.send_message(
                    chat_id,
                    "👋 *First run!* Default config created.\n\n"
                    "Edit `/app/config/config.yaml` to customize settings.\n"
                    "Use /help to get started.",
                    parse_mode="Markdown",
                )

        asyncio.create_task(send_welcome())
```

Add import at top:

```python
import os
```

**Step 2: Commit**

```bash
git add src/main.py
git commit -m "feat: add first-run detection and welcome message"
```

---

### Task 3.4: Update Documentation

**Files:**
- Modify: `README.md`

**Step 1: Add storage documentation to README**

Add section to README.md:

```markdown
## Storage

All persistent data is stored in bind-mounted volumes:

```
/mnt/user/appdata/unraid-monitor/
├── config/
│   └── config.yaml          # Main configuration
└── data/
    ├── monitor.db           # Event history database
    ├── ignored_errors.json  # Ignore patterns
    ├── mutes.json           # Container mutes
    ├── server_mutes.json    # Server mutes
    └── array_mutes.json     # Array mutes
```

On first run, a default `config.yaml` is created automatically.

### First Run Setup

1. Create the appdata directory:
   ```bash
   mkdir -p /mnt/user/appdata/unraid-monitor/{config,data}
   ```

2. Start the container - it will create a default config

3. Edit `/mnt/user/appdata/unraid-monitor/config/config.yaml` to:
   - Add containers to watch
   - Configure memory management
   - Enable Unraid monitoring

4. Restart the container to apply changes
```

**Step 2: Commit**

```bash
git add README.md
git commit -m "docs: add storage and first-run documentation"
```

---

## Final Integration

### Task 4.1: Run Full Test Suite

**Step 1: Run all tests**

```bash
pytest tests/ -v --tb=short
```

Expected: All tests pass

**Step 2: Run type checking**

```bash
mypy src/
```

Expected: No errors

**Step 3: Run linting**

```bash
ruff check src/
```

Expected: No errors (or only minor style issues)

---

### Task 4.2: Final Commit and Summary

**Step 1: Create summary commit if needed**

If there are any uncommitted changes:

```bash
git add -A
git commit -m "chore: final cleanup for Phase 6 features"
```

**Step 2: View commit history**

```bash
git log --oneline -20
```

---

## Summary

This plan implements three features:

1. **Memory Pressure Management** (Tasks 1.1-1.10)
   - New `MemoryConfig` and `MemoryMonitor` classes
   - State machine for NORMAL → WARNING → CRITICAL → RECOVERING
   - Kill countdown with /cancel-kill command
   - Restart prompts when memory recovers

2. **Smart Ignore Patterns** (Tasks 2.1-2.4)
   - Updated `IgnoreManager` to support regex patterns with explanations
   - New `PatternAnalyzer` using Claude Haiku
   - "Ignore Similar" button on error alerts
   - Backward compatible with existing ignore files

3. **Persistent Storage** (Tasks 3.1-3.4)
   - Updated docker-compose.yml for bind mounts
   - Default config generation on first run
   - Welcome message for new installations
   - Documentation updates

Each task follows TDD with failing test → implementation → passing test → commit.
