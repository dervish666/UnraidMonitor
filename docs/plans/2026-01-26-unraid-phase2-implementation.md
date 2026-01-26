# Unraid Phase 2: Array Monitoring Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add proactive array monitoring with disk health alerts and dedicated array/disk commands.

**Architecture:** New ArrayMonitor polls array status, checks thresholds, triggers alerts. New commands expose disk-level details. Separate mute control for array alerts.

**Tech Stack:** Python, aiogram, existing UnraidClientWrapper GraphQL client.

---

### Task 1: Add Array Thresholds to Config

**Files:**
- Modify: `src/config.py`
- Modify: `config/config.yaml`
- Test: `tests/test_config.py`

**Step 1: Write the failing test**

```python
def test_unraid_array_thresholds():
    """Test array threshold config loading."""
    from src.config import load_config

    config = load_config("config/config.yaml")

    assert config.unraid.thresholds.disk_temp == 50
    assert config.unraid.thresholds.array_usage == 85
    assert config.unraid.poll_array_seconds == 300
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py::test_unraid_array_thresholds -v`
Expected: FAIL with AttributeError (disk_temp not defined)

**Step 3: Add thresholds to UnraidThresholds dataclass**

In `src/config.py`, add to `UnraidThresholds`:
```python
disk_temp: int = 50
array_usage: int = 85
```

Add to `UnraidConfig`:
```python
poll_array_seconds: int = 300
```

**Step 4: Update config.yaml with defaults**

```yaml
unraid:
  poll_array_seconds: 300
  thresholds:
    disk_temp: 50
    array_usage: 85
```

**Step 5: Run test to verify it passes**

Run: `pytest tests/test_config.py::test_unraid_array_thresholds -v`
Expected: PASS

**Step 6: Commit**

```bash
git add src/config.py config/config.yaml tests/test_config.py
git commit -m "feat: add array monitoring thresholds to config"
```

---

### Task 2: Create ArrayMuteManager

**Files:**
- Create: `src/alerts/array_mute_manager.py`
- Test: `tests/test_array_mute_manager.py`

**Step 1: Write the failing test**

```python
import pytest
from datetime import timedelta

def test_array_mute_manager():
    """Test array mute manager basic functionality."""
    from src.alerts.array_mute_manager import ArrayMuteManager

    manager = ArrayMuteManager(json_path="/tmp/test_array_mutes.json")

    assert not manager.is_array_muted()

    manager.mute_array(timedelta(hours=2))
    assert manager.is_array_muted()

    manager.unmute_array()
    assert not manager.is_array_muted()
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_array_mute_manager.py -v`
Expected: FAIL with ModuleNotFoundError

**Step 3: Implement ArrayMuteManager**

Create `src/alerts/array_mute_manager.py` - similar to ServerMuteManager:
```python
"""Array alert mute manager."""

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


class ArrayMuteManager:
    """Manages muting of array/disk alerts separately from server alerts."""

    def __init__(self, json_path: str):
        self._json_path = Path(json_path)
        self._mute_until: datetime | None = None
        self._load()

    def is_array_muted(self) -> bool:
        """Check if array alerts are currently muted."""
        if self._mute_until is None:
            return False
        if datetime.now(timezone.utc) >= self._mute_until:
            self._mute_until = None
            self._save()
            return False
        return True

    def mute_array(self, duration: timedelta) -> datetime:
        """Mute array alerts for duration. Returns expiry time."""
        self._mute_until = datetime.now(timezone.utc) + duration
        self._save()
        return self._mute_until

    def unmute_array(self) -> bool:
        """Unmute array alerts. Returns True if was muted."""
        was_muted = self._mute_until is not None
        self._mute_until = None
        self._save()
        return was_muted

    def get_mute_expiry(self) -> datetime | None:
        """Get mute expiry time, or None if not muted."""
        if self.is_array_muted():
            return self._mute_until
        return None

    def _load(self) -> None:
        if not self._json_path.exists():
            return
        try:
            with open(self._json_path, encoding="utf-8") as f:
                data = json.load(f)
                if exp := data.get("mute_until"):
                    self._mute_until = datetime.fromisoformat(exp)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Failed to load array mutes: {e}")

    def _save(self) -> None:
        self._json_path.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        if self._mute_until:
            data["mute_until"] = self._mute_until.isoformat()
        try:
            with open(self._json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except IOError as e:
            logger.error(f"Failed to save array mutes: {e}")
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_array_mute_manager.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/alerts/array_mute_manager.py tests/test_array_mute_manager.py
git commit -m "feat: add ArrayMuteManager for array alert muting"
```

---

### Task 3: Create ArrayMonitor

**Files:**
- Create: `src/unraid/monitors/array_monitor.py`
- Test: `tests/test_array_monitor.py`

**Step 1: Write the failing test**

```python
import pytest
from unittest.mock import MagicMock, AsyncMock

@pytest.mark.asyncio
async def test_array_monitor_alerts_on_high_disk_temp():
    """Test array monitor alerts when disk temp exceeds threshold."""
    from src.unraid.monitors.array_monitor import ArrayMonitor

    mock_client = MagicMock()
    mock_client.get_array_status = AsyncMock(return_value={
        "state": "STARTED",
        "disks": [
            {"name": "disk1", "temp": 55, "status": "DISK_OK"},
        ],
        "parities": [],
        "caches": [],
        "capacity": {"kilobytes": {"used": "1000", "total": "10000", "free": "9000"}},
    })

    mock_config = MagicMock()
    mock_config.disk_temp_threshold = 50
    mock_config.array_usage_threshold = 85
    mock_config.poll_array_seconds = 300

    mock_mute = MagicMock()
    mock_mute.is_array_muted.return_value = False

    alerts = []
    async def capture_alert(**kwargs):
        alerts.append(kwargs)

    monitor = ArrayMonitor(mock_client, mock_config, capture_alert, mock_mute)
    await monitor.check_once()

    assert len(alerts) == 1
    assert "disk1" in alerts[0]["message"]
    assert "55" in alerts[0]["message"]
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_array_monitor.py::test_array_monitor_alerts_on_high_disk_temp -v`
Expected: FAIL with ModuleNotFoundError

**Step 3: Implement ArrayMonitor**

Create `src/unraid/monitors/array_monitor.py`:
```python
"""Unraid array monitor for disk health and capacity monitoring."""

import asyncio
import logging
from typing import Callable, Awaitable, TYPE_CHECKING

if TYPE_CHECKING:
    from src.config import UnraidConfig
    from src.unraid.client import UnraidClientWrapper
    from src.alerts.array_mute_manager import ArrayMuteManager

logger = logging.getLogger(__name__)


class ArrayMonitor:
    """Monitors Unraid array health and triggers alerts."""

    def __init__(
        self,
        client: "UnraidClientWrapper",
        config: "UnraidConfig",
        on_alert: Callable[..., Awaitable[None]],
        mute_manager: "ArrayMuteManager",
    ):
        self._client = client
        self._config = config
        self._on_alert = on_alert
        self._mute_manager = mute_manager
        self._running = False
        self._task: asyncio.Task | None = None
        self._alerted_disks: set[str] = set()  # Track alerted disks to avoid spam

    async def start(self) -> None:
        """Start the monitoring loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._monitor_loop())
        logger.info("Unraid array monitor started")

    async def stop(self) -> None:
        """Stop the monitoring loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Unraid array monitor stopped")

    async def _monitor_loop(self) -> None:
        """Main monitoring loop."""
        while self._running:
            try:
                await self.check_once()
            except Exception as e:
                logger.error(f"Error in array monitor: {e}")
            await asyncio.sleep(self._config.poll_array_seconds)

    async def check_once(self) -> dict | None:
        """Check array status once and alert if needed."""
        try:
            array = await self._client.get_array_status()
        except Exception as e:
            logger.error(f"Failed to get array status: {e}")
            return None

        if self._mute_manager.is_array_muted():
            logger.debug("Array alerts muted, skipping checks")
            return array

        # Check disk temperatures
        for disk in array.get("disks", []):
            await self._check_disk(disk, "Array Disk")

        # Check parity temperatures
        for parity in array.get("parities", []):
            await self._check_disk(parity, "Parity")

        # Check cache temperatures
        for cache in array.get("caches", []):
            await self._check_disk(cache, "Cache")

        # Check array capacity
        await self._check_capacity(array)

        return array

    async def _check_disk(self, disk: dict, disk_type: str) -> None:
        """Check a single disk for issues."""
        name = disk.get("name", "unknown")
        temp = disk.get("temp", 0)
        status = disk.get("status", "")

        # Check temperature
        if temp and temp > self._config.thresholds.disk_temp:
            alert_key = f"temp_{name}"
            if alert_key not in self._alerted_disks:
                self._alerted_disks.add(alert_key)
                await self._on_alert(
                    title=f"💾 {disk_type} High Temperature",
                    message=f"*{name}*: {temp}°C (threshold: {self._config.thresholds.disk_temp}°C)",
                    alert_type="array",
                )

        # Check status (not DISK_OK)
        if status and status != "DISK_OK":
            alert_key = f"status_{name}"
            if alert_key not in self._alerted_disks:
                self._alerted_disks.add(alert_key)
                status_display = status.replace("DISK_", "")
                await self._on_alert(
                    title=f"💾 {disk_type} Problem",
                    message=f"*{name}*: Status is {status_display}\nCheck disk health immediately.",
                    alert_type="array",
                )

    async def _check_capacity(self, array: dict) -> None:
        """Check array capacity usage."""
        capacity = array.get("capacity", {}).get("kilobytes", {})
        used = float(capacity.get("used", 0))
        total = float(capacity.get("total", 0))

        if total > 0:
            usage_pct = (used / total) * 100
            if usage_pct > self._config.thresholds.array_usage:
                alert_key = "capacity"
                if alert_key not in self._alerted_disks:
                    self._alerted_disks.add(alert_key)
                    used_tb = used / (1024 * 1024 * 1024)
                    total_tb = total / (1024 * 1024 * 1024)
                    free_tb = (total - used) / (1024 * 1024 * 1024)
                    await self._on_alert(
                        title="💾 Array Capacity Warning",
                        message=f"Usage: {usage_pct:.1f}% (threshold: {self._config.thresholds.array_usage}%)\n"
                                f"Used: {used_tb:.1f} TB / {total_tb:.1f} TB ({free_tb:.1f} TB free)",
                        alert_type="array",
                    )

    def clear_alert_state(self) -> None:
        """Clear alerted disks (e.g., after unmute)."""
        self._alerted_disks.clear()
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_array_monitor.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/unraid/monitors/array_monitor.py tests/test_array_monitor.py
git commit -m "feat: add ArrayMonitor for disk health and capacity alerts"
```

---

### Task 4: Add /array Command

**Files:**
- Modify: `src/bot/unraid_commands.py`
- Modify: `src/bot/commands.py` (HELP_TEXT)
- Test: `tests/test_unraid_commands.py`

**Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_array_command():
    """Test /array shows array status."""
    from src.bot.unraid_commands import array_command

    mock_monitor = MagicMock()
    mock_monitor.get_array_status = AsyncMock(return_value={
        "state": "STARTED",
        "capacity": {"kilobytes": {"used": "34729066496", "total": "46205820928", "free": "11476754432"}},
        "disks": [
            {"name": "disk1", "temp": 35, "status": "DISK_OK"},
            {"name": "disk2", "temp": 37, "status": "DISK_OK"},
        ],
        "parities": [{"name": "parity", "temp": 33, "status": "DISK_OK"}],
        "caches": [{"name": "cache", "temp": 38, "status": "DISK_OK"}],
    })

    handler = array_command(mock_monitor)

    message = MagicMock()
    message.text = "/array"
    message.answer = AsyncMock()

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]

    assert "STARTED" in response
    assert "disk1" in response or "2 disks" in response
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_unraid_commands.py::test_array_command -v`
Expected: FAIL with ImportError (array_command not found)

**Step 3: Implement array_command**

Add to `src/bot/unraid_commands.py`:
```python
def array_command(
    system_monitor: "UnraidSystemMonitor",
) -> Callable[[Message], Awaitable[None]]:
    """Factory for /array command handler."""

    async def handler(message: Message) -> None:
        array = await system_monitor.get_array_status()

        if not array:
            await message.answer("💾 Array status unavailable.")
            return

        state = array.get("state", "Unknown")

        # Capacity
        capacity_kb = array.get("capacity", {}).get("kilobytes", {})
        kb_to_tb = 1024 * 1024 * 1024
        used_tb = float(capacity_kb.get("used", 0)) / kb_to_tb
        total_tb = float(capacity_kb.get("total", 0)) / kb_to_tb
        free_tb = float(capacity_kb.get("free", 0)) / kb_to_tb
        usage_pct = (used_tb / total_tb * 100) if total_tb > 0 else 0

        # Disk counts
        disks = array.get("disks", [])
        parities = array.get("parities", [])
        caches = array.get("caches", [])

        lines = [
            f"💾 *Array Status*\n",
            f"*State:* {state}",
            f"*Storage:* {used_tb:.1f} / {total_tb:.1f} TB ({usage_pct:.0f}% used)",
            f"*Free:* {free_tb:.1f} TB",
            f"\n*Devices:*",
            f"  Data disks: {len(disks)}",
            f"  Parity: {len(parities)}",
            f"  Cache: {len(caches)}",
        ]

        # Show any problems
        problems = []
        for disk in disks + parities + caches:
            status = disk.get("status", "")
            if status and status != "DISK_OK":
                problems.append(f"  ⚠️ {disk.get('name')}: {status.replace('DISK_', '')}")

        if problems:
            lines.append("\n*Issues:*")
            lines.extend(problems)

        await message.answer("\n".join(lines), parse_mode="Markdown")

    return handler
```

**Step 4: Add to HELP_TEXT in commands.py**

Add `/array` to the help text.

**Step 5: Run test to verify it passes**

Run: `pytest tests/test_unraid_commands.py::test_array_command -v`
Expected: PASS

**Step 6: Commit**

```bash
git add src/bot/unraid_commands.py src/bot/commands.py tests/test_unraid_commands.py
git commit -m "feat: add /array command for array status"
```

---

### Task 5: Add /disks Command

**Files:**
- Modify: `src/bot/unraid_commands.py`
- Modify: `src/bot/commands.py` (HELP_TEXT)
- Test: `tests/test_unraid_commands.py`

**Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_disks_command():
    """Test /disks lists all disks."""
    from src.bot.unraid_commands import disks_command

    mock_monitor = MagicMock()
    mock_monitor.get_array_status = AsyncMock(return_value={
        "state": "STARTED",
        "disks": [
            {"name": "disk1", "temp": 35, "status": "DISK_OK", "size": 4000000000000},
            {"name": "disk2", "temp": 37, "status": "DISK_OK", "size": 8000000000000},
        ],
        "parities": [{"name": "parity", "temp": 33, "status": "DISK_OK", "size": 8000000000000}],
        "caches": [{"name": "cache", "temp": 38, "status": "DISK_OK", "size": 1000000000000}],
    })

    handler = disks_command(mock_monitor)

    message = MagicMock()
    message.text = "/disks"
    message.answer = AsyncMock()

    await handler(message)

    response = message.answer.call_args[0][0]

    assert "disk1" in response
    assert "disk2" in response
    assert "35°C" in response or "35" in response
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_unraid_commands.py::test_disks_command -v`
Expected: FAIL

**Step 3: Implement disks_command**

Add to `src/bot/unraid_commands.py`:
```python
def disks_command(
    system_monitor: "UnraidSystemMonitor",
) -> Callable[[Message], Awaitable[None]]:
    """Factory for /disks command handler."""

    async def handler(message: Message) -> None:
        array = await system_monitor.get_array_status()

        if not array:
            await message.answer("💾 Disk status unavailable.")
            return

        lines = ["💾 *Disk Status*\n"]

        # Parity disks
        parities = array.get("parities", [])
        if parities:
            lines.append("*Parity:*")
            for disk in parities:
                lines.append(_format_disk_line(disk))

        # Data disks
        disks = array.get("disks", [])
        if disks:
            lines.append("\n*Data Disks:*")
            for disk in disks:
                lines.append(_format_disk_line(disk))

        # Cache disks
        caches = array.get("caches", [])
        if caches:
            lines.append("\n*Cache:*")
            for disk in caches:
                lines.append(_format_disk_line(disk))

        await message.answer("\n".join(lines), parse_mode="Markdown")

    return handler


def _format_disk_line(disk: dict) -> str:
    """Format a single disk for display."""
    name = disk.get("name", "unknown")
    temp = disk.get("temp", 0)
    status = disk.get("status", "").replace("DISK_", "")
    size = disk.get("size", 0)

    # Convert size to TB
    size_tb = size / (1000 * 1000 * 1000 * 1000) if size else 0

    status_icon = "✅" if status == "OK" else "⚠️"

    if size_tb > 0:
        return f"  {status_icon} {name}: {size_tb:.1f}TB • {temp}°C • {status}"
    return f"  {status_icon} {name}: {temp}°C • {status}"
```

**Step 4: Add to HELP_TEXT**

**Step 5: Run test to verify it passes**

Run: `pytest tests/test_unraid_commands.py::test_disks_command -v`
Expected: PASS

**Step 6: Commit**

```bash
git add src/bot/unraid_commands.py src/bot/commands.py tests/test_unraid_commands.py
git commit -m "feat: add /disks command to list all disks"
```

---

### Task 6: Add /mute-array and /unmute-array Commands

**Files:**
- Modify: `src/bot/unraid_commands.py`
- Modify: `src/bot/commands.py` (HELP_TEXT)
- Test: `tests/test_unraid_commands.py`

**Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_mute_array_command(tmp_path):
    """Test /mute-array mutes array alerts."""
    from src.bot.unraid_commands import mute_array_command
    from src.alerts.array_mute_manager import ArrayMuteManager

    json_file = tmp_path / "array_mutes.json"
    mute_manager = ArrayMuteManager(json_path=str(json_file))

    handler = mute_array_command(mute_manager)

    message = MagicMock()
    message.text = "/mute-array 2h"
    message.answer = AsyncMock()

    await handler(message)

    response = message.answer.call_args[0][0]
    assert "Muted" in response
    assert mute_manager.is_array_muted()
```

**Step 2: Run test to verify it fails**

Expected: FAIL

**Step 3: Implement mute_array_command and unmute_array_command**

Add to `src/bot/unraid_commands.py`:
```python
def mute_array_command(
    mute_manager: "ArrayMuteManager",
) -> Callable[[Message], Awaitable[None]]:
    """Factory for /mute-array command handler."""

    async def handler(message: Message) -> None:
        text = (message.text or "").strip()
        parts = text.split()

        if len(parts) < 2:
            await message.answer(
                "Usage: `/mute-array <duration>`\n\n"
                "Examples: `2h`, `30m`, `24h`\n\n"
                "This mutes array/disk alerts only.",
                parse_mode="Markdown",
            )
            return

        duration_str = parts[1]
        duration = parse_duration(duration_str)

        if not duration:
            await message.answer(
                f"Invalid duration: `{duration_str}`\n"
                "Use format like `15m`, `2h`, `24h`",
                parse_mode="Markdown",
            )
            return

        expiry = mute_manager.mute_array(duration)
        time_str = expiry.strftime("%H:%M")

        await message.answer(
            f"🔇 *Muted array alerts* until {time_str}\n\n"
            f"Disk health and capacity alerts suppressed.\n"
            f"Use `/unmute-array` to unmute early.",
            parse_mode="Markdown",
        )

    return handler


def unmute_array_command(
    mute_manager: "ArrayMuteManager",
) -> Callable[[Message], Awaitable[None]]:
    """Factory for /unmute-array command handler."""

    async def handler(message: Message) -> None:
        if mute_manager.unmute_array():
            await message.answer(
                "🔔 *Unmuted array alerts*\n\n"
                "Disk health and capacity alerts are now enabled.",
                parse_mode="Markdown",
            )
        else:
            await message.answer("Array alerts are not currently muted.")

    return handler
```

**Step 4: Add to HELP_TEXT**

**Step 5: Run tests**

Run: `pytest tests/test_unraid_commands.py -v`
Expected: PASS

**Step 6: Commit**

```bash
git add src/bot/unraid_commands.py src/bot/commands.py tests/test_unraid_commands.py
git commit -m "feat: add /mute-array and /unmute-array commands"
```

---

### Task 7: Integrate ArrayMonitor into Main

**Files:**
- Modify: `src/main.py`
- Test: Manual integration test

**Step 1: Import and create ArrayMonitor**

In `src/main.py`, after creating ServerMuteManager:
```python
from src.alerts.array_mute_manager import ArrayMuteManager
from src.unraid.monitors.array_monitor import ArrayMonitor

# Create array mute manager
array_mute_manager = ArrayMuteManager(
    json_path=str(data_dir / "array_mutes.json")
)

# Create array monitor
array_monitor = ArrayMonitor(
    client=unraid_client,
    config=config.unraid,
    on_alert=send_alert,
    mute_manager=array_mute_manager,
)
```

**Step 2: Register new commands**

```python
from src.bot.unraid_commands import array_command, disks_command, mute_array_command, unmute_array_command

dp.message.register(array_command(system_monitor), Command("array"))
dp.message.register(disks_command(system_monitor), Command("disks"))
dp.message.register(mute_array_command(array_mute_manager), Command("mute-array"))
dp.message.register(unmute_array_command(array_mute_manager), Command("unmute-array"))
```

**Step 3: Start array monitor in startup**

```python
await array_monitor.start()
```

**Step 4: Stop array monitor in shutdown**

```python
await array_monitor.stop()
```

**Step 5: Test manually**

Deploy and verify:
- `/array` shows array status
- `/disks` lists all disks
- `/mute-array 1h` mutes alerts
- `/unmute-array` unmutes

**Step 6: Commit**

```bash
git add src/main.py
git commit -m "feat: integrate ArrayMonitor and register array commands"
```

---

### Task 8: Update /mutes to Show Array Mutes

**Files:**
- Modify: `src/bot/commands.py` (mutes command)
- Test: `tests/test_commands.py`

**Step 1: Update mutes_command to accept array_mute_manager**

Modify the mutes command factory to also show array mute status.

**Step 2: Test and commit**

```bash
git add src/bot/commands.py tests/test_commands.py
git commit -m "feat: show array mutes in /mutes command"
```

---

### Task 9: Final Integration Test and Tag

**Step 1: Run all tests**

```bash
pytest tests/ -v
```

**Step 2: Deploy and verify**

- Test all new commands work
- Simulate a disk temp alert (lower threshold temporarily)
- Verify muting works

**Step 3: Tag release**

```bash
git tag -a v0.9.0 -m "Phase 2: Array monitoring with disk alerts"
git push origin master --tags
```
