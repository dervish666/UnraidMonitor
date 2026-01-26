# Unraid Phase 1: Foundation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add Unraid server monitoring foundation - client wrapper, system metrics monitoring, `/server` command, and server mute support.

**Architecture:** UnraidClient wraps the `unraid-api` library with connection handling. SystemMonitor polls CPU/memory/temp and triggers alerts via callbacks. ServerMuteManager extends existing mute patterns for server-level alerts.

**Tech Stack:** Python 3.11+, unraid-api library, aiogram, asyncio

---

## Task 1: Add unraid-api dependency

**Files:**
- Modify: `pyproject.toml`

**Step 1: Add the dependency**

Add `unraid-api` to the dependencies in `pyproject.toml`:

```toml
dependencies = [
    "aiogram>=3.0.0",
    "docker>=7.0.0",
    "pyyaml>=6.0",
    "pydantic>=2.0.0",
    "pydantic-settings>=2.0.0",
    "anthropic>=0.18.0",
    "unraid-api>=0.1.0",
]
```

**Step 2: Install dependencies**

Run: `source .venv/bin/activate && pip install unraid-api`

**Step 3: Verify installation**

Run: `source .venv/bin/activate && python -c "from unraid_api import UnraidClient; print('OK')"`
Expected: `OK`

**Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "chore: add unraid-api dependency"
```

---

## Task 2: UnraidConfig dataclass

**Files:**
- Modify: `src/config.py`
- Test: `tests/test_unraid_config.py`

**Step 1: Write the failing test**

Create `tests/test_unraid_config.py`:

```python
import pytest


def test_unraid_config_from_dict():
    """Test UnraidConfig parses from YAML dict."""
    from src.config import UnraidConfig

    data = {
        "enabled": True,
        "host": "192.168.1.100",
        "port": 443,
        "polling": {
            "system": 30,
            "array": 300,
            "ups": 60,
        },
        "thresholds": {
            "cpu_temp": 80,
            "cpu_usage": 95,
            "memory_usage": 90,
        },
    }

    config = UnraidConfig.from_dict(data)

    assert config.enabled is True
    assert config.host == "192.168.1.100"
    assert config.port == 443
    assert config.poll_system_seconds == 30
    assert config.poll_array_seconds == 300
    assert config.cpu_temp_threshold == 80
    assert config.cpu_usage_threshold == 95
    assert config.memory_usage_threshold == 90


def test_unraid_config_defaults():
    """Test UnraidConfig has sensible defaults."""
    from src.config import UnraidConfig

    config = UnraidConfig.from_dict({})

    assert config.enabled is False
    assert config.host == ""
    assert config.port == 443
    assert config.poll_system_seconds == 30
    assert config.cpu_temp_threshold == 80
    assert config.memory_usage_threshold == 90


def test_unraid_config_disabled():
    """Test UnraidConfig when explicitly disabled."""
    from src.config import UnraidConfig

    config = UnraidConfig.from_dict({"enabled": False})

    assert config.enabled is False
```

**Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest tests/test_unraid_config.py -v`
Expected: FAIL with "cannot import name 'UnraidConfig'"

**Step 3: Write minimal implementation**

Add to `src/config.py` after `ResourceConfig` class:

```python
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
```

**Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && python -m pytest tests/test_unraid_config.py -v`
Expected: PASS (3 tests)

**Step 5: Commit**

```bash
git add src/config.py tests/test_unraid_config.py
git commit -m "feat: add UnraidConfig dataclass"
```

---

## Task 3: Add UNRAID_API_KEY to Settings and AppConfig

**Files:**
- Modify: `src/config.py`
- Test: `tests/test_unraid_config.py`

**Step 1: Write the failing test**

Add to `tests/test_unraid_config.py`:

```python
def test_settings_has_unraid_api_key(tmp_path):
    """Test Settings reads UNRAID_API_KEY from environment."""
    import os
    from unittest.mock import patch

    config_file = tmp_path / "config.yaml"
    config_file.write_text("log_watching:\n  containers: []\n")

    with patch.dict(os.environ, {
        "TELEGRAM_BOT_TOKEN": "test",
        "TELEGRAM_ALLOWED_USERS": "123",
        "UNRAID_API_KEY": "my-secret-key",
    }):
        from src.config import Settings

        settings = Settings(config_path=str(config_file))
        assert settings.unraid_api_key == "my-secret-key"


def test_settings_unraid_api_key_optional(tmp_path):
    """Test UNRAID_API_KEY is optional."""
    import os
    from unittest.mock import patch

    config_file = tmp_path / "config.yaml"
    config_file.write_text("log_watching:\n  containers: []\n")

    with patch.dict(os.environ, {
        "TELEGRAM_BOT_TOKEN": "test",
        "TELEGRAM_ALLOWED_USERS": "123",
    }, clear=True):
        from src.config import Settings

        settings = Settings(config_path=str(config_file))
        assert settings.unraid_api_key is None


def test_app_config_unraid_property(tmp_path):
    """Test AppConfig has unraid property."""
    import os
    from unittest.mock import patch

    config_file = tmp_path / "config.yaml"
    config_file.write_text("""
unraid:
  enabled: true
  host: "192.168.1.100"
  thresholds:
    cpu_temp: 75
""")

    with patch.dict(os.environ, {
        "TELEGRAM_BOT_TOKEN": "test",
        "TELEGRAM_ALLOWED_USERS": "123",
    }):
        from src.config import Settings, AppConfig

        settings = Settings(config_path=str(config_file))
        config = AppConfig(settings)

        assert config.unraid.enabled is True
        assert config.unraid.host == "192.168.1.100"
        assert config.unraid.cpu_temp_threshold == 75
```

**Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest tests/test_unraid_config.py::test_settings_has_unraid_api_key -v`
Expected: FAIL with "has no attribute 'unraid_api_key'"

**Step 3: Write minimal implementation**

In `src/config.py`, update the `Settings` class to add:

```python
    unraid_api_key: str | None = None
```

In the `AppConfig` class, add a property:

```python
    @property
    def unraid(self) -> UnraidConfig:
        """Get Unraid configuration."""
        return UnraidConfig.from_dict(self._yaml_config.get("unraid", {}))
```

**Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && python -m pytest tests/test_unraid_config.py -v`
Expected: PASS (6 tests)

**Step 5: Commit**

```bash
git add src/config.py tests/test_unraid_config.py
git commit -m "feat: add UNRAID_API_KEY to Settings and unraid property to AppConfig"
```

---

## Task 4: UnraidClient wrapper

**Files:**
- Create: `src/unraid/__init__.py`
- Create: `src/unraid/client.py`
- Test: `tests/test_unraid_client.py`

**Step 1: Write the failing test**

Create `tests/test_unraid_client.py`:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_unraid_client_connect():
    """Test UnraidClient connects successfully."""
    from src.unraid.client import UnraidClientWrapper

    with patch("src.unraid.client.UnraidClient") as MockClient:
        mock_instance = AsyncMock()
        MockClient.return_value = mock_instance

        wrapper = UnraidClientWrapper(
            host="192.168.1.100",
            api_key="test-key",
            port=443,
        )

        await wrapper.connect()

        MockClient.assert_called_once_with("192.168.1.100", "test-key", port=443)
        mock_instance.__aenter__.assert_called_once()


@pytest.mark.asyncio
async def test_unraid_client_disconnect():
    """Test UnraidClient disconnects properly."""
    from src.unraid.client import UnraidClientWrapper

    with patch("src.unraid.client.UnraidClient") as MockClient:
        mock_instance = AsyncMock()
        MockClient.return_value = mock_instance

        wrapper = UnraidClientWrapper(
            host="192.168.1.100",
            api_key="test-key",
        )

        await wrapper.connect()
        await wrapper.disconnect()

        mock_instance.__aexit__.assert_called_once()


@pytest.mark.asyncio
async def test_unraid_client_get_system_metrics():
    """Test getting system metrics."""
    from src.unraid.client import UnraidClientWrapper

    with patch("src.unraid.client.UnraidClient") as MockClient:
        mock_instance = AsyncMock()
        mock_instance.get_system_metrics = AsyncMock(return_value={
            "cpu_percent": 25.5,
            "cpu_temperature": 45.0,
            "memory_percent": 60.0,
            "memory_used": 1024 * 1024 * 1024 * 32,
            "uptime": "5 days, 3 hours",
        })
        MockClient.return_value = mock_instance

        wrapper = UnraidClientWrapper(
            host="192.168.1.100",
            api_key="test-key",
        )
        await wrapper.connect()

        metrics = await wrapper.get_system_metrics()

        assert metrics["cpu_percent"] == 25.5
        assert metrics["cpu_temperature"] == 45.0
        assert metrics["memory_percent"] == 60.0


@pytest.mark.asyncio
async def test_unraid_client_not_connected():
    """Test error when calling methods without connecting."""
    from src.unraid.client import UnraidClientWrapper, UnraidConnectionError

    wrapper = UnraidClientWrapper(
        host="192.168.1.100",
        api_key="test-key",
    )

    with pytest.raises(UnraidConnectionError):
        await wrapper.get_system_metrics()
```

**Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest tests/test_unraid_client.py -v`
Expected: FAIL with "cannot import name 'UnraidClientWrapper'"

**Step 3: Write minimal implementation**

Create `src/unraid/__init__.py`:

```python
"""Unraid server integration."""

from src.unraid.client import UnraidClientWrapper, UnraidConnectionError

__all__ = ["UnraidClientWrapper", "UnraidConnectionError"]
```

Create `src/unraid/client.py`:

```python
import logging
from typing import Any

from unraid_api import UnraidClient

logger = logging.getLogger(__name__)


class UnraidConnectionError(Exception):
    """Raised when Unraid client is not connected."""
    pass


class UnraidClientWrapper:
    """Wrapper around UnraidClient with connection management."""

    def __init__(self, host: str, api_key: str, port: int = 443):
        """Initialize the wrapper.

        Args:
            host: Unraid server hostname or IP.
            api_key: API key for authentication.
            port: API port (default 443).
        """
        self._host = host
        self._api_key = api_key
        self._port = port
        self._client: UnraidClient | None = None
        self._connected = False

    @property
    def is_connected(self) -> bool:
        """Check if client is connected."""
        return self._connected

    async def connect(self) -> None:
        """Establish connection to Unraid server."""
        self._client = UnraidClient(self._host, self._api_key, port=self._port)
        await self._client.__aenter__()
        self._connected = True
        logger.info(f"Connected to Unraid server at {self._host}")

    async def disconnect(self) -> None:
        """Close connection to Unraid server."""
        if self._client and self._connected:
            await self._client.__aexit__(None, None, None)
            self._connected = False
            logger.info("Disconnected from Unraid server")

    def _ensure_connected(self) -> None:
        """Raise error if not connected."""
        if not self._connected or self._client is None:
            raise UnraidConnectionError("Not connected to Unraid server")

    async def get_system_metrics(self) -> dict[str, Any]:
        """Get system metrics (CPU, memory, temp, uptime).

        Returns:
            Dict with cpu_percent, cpu_temperature, memory_percent, etc.
        """
        self._ensure_connected()
        return await self._client.get_system_metrics()

    async def get_array_status(self) -> dict[str, Any]:
        """Get array status (disks, parity, capacity).

        Returns:
            Dict with state, capacity, disks, etc.
        """
        self._ensure_connected()
        return await self._client.get_array_status()

    async def get_vms(self) -> list[dict[str, Any]]:
        """Get list of virtual machines.

        Returns:
            List of VM dicts with name, id, state.
        """
        self._ensure_connected()
        return await self._client.get_vms()

    async def get_ups_status(self) -> list[dict[str, Any]]:
        """Get UPS status.

        Returns:
            List of UPS device dicts.
        """
        self._ensure_connected()
        return await self._client.get_ups_status()
```

**Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && python -m pytest tests/test_unraid_client.py -v`
Expected: PASS (4 tests)

**Step 5: Commit**

```bash
git add src/unraid/__init__.py src/unraid/client.py tests/test_unraid_client.py
git commit -m "feat: add UnraidClientWrapper with connection management"
```

---

## Task 5: ServerMuteManager

**Files:**
- Create: `src/alerts/server_mute_manager.py`
- Test: `tests/test_server_mute_manager.py`

**Step 1: Write the failing test**

Create `tests/test_server_mute_manager.py`:

```python
import pytest
from datetime import timedelta


def test_server_mute_manager_mute_all(tmp_path):
    """Test muting all server alerts."""
    from src.alerts.server_mute_manager import ServerMuteManager

    json_file = tmp_path / "server_mutes.json"
    manager = ServerMuteManager(json_path=str(json_file))

    manager.mute_server(timedelta(hours=2))

    assert manager.is_server_muted()
    assert manager.is_array_muted()
    assert manager.is_ups_muted()


def test_server_mute_manager_mute_array_only(tmp_path):
    """Test muting just array alerts."""
    from src.alerts.server_mute_manager import ServerMuteManager

    json_file = tmp_path / "server_mutes.json"
    manager = ServerMuteManager(json_path=str(json_file))

    manager.mute_array(timedelta(hours=4))

    assert not manager.is_server_muted()
    assert manager.is_array_muted()
    assert not manager.is_ups_muted()


def test_server_mute_manager_mute_ups_only(tmp_path):
    """Test muting just UPS alerts."""
    from src.alerts.server_mute_manager import ServerMuteManager

    json_file = tmp_path / "server_mutes.json"
    manager = ServerMuteManager(json_path=str(json_file))

    manager.mute_ups(timedelta(hours=1))

    assert not manager.is_server_muted()
    assert not manager.is_array_muted()
    assert manager.is_ups_muted()


def test_server_mute_manager_unmute(tmp_path):
    """Test unmuting."""
    from src.alerts.server_mute_manager import ServerMuteManager

    json_file = tmp_path / "server_mutes.json"
    manager = ServerMuteManager(json_path=str(json_file))

    manager.mute_server(timedelta(hours=2))
    assert manager.is_server_muted()

    manager.unmute_server()
    assert not manager.is_server_muted()


def test_server_mute_manager_persistence(tmp_path):
    """Test mutes persist across restarts."""
    from src.alerts.server_mute_manager import ServerMuteManager

    json_file = tmp_path / "server_mutes.json"

    manager1 = ServerMuteManager(json_path=str(json_file))
    manager1.mute_array(timedelta(hours=4))

    manager2 = ServerMuteManager(json_path=str(json_file))
    assert manager2.is_array_muted()


def test_server_mute_manager_get_active_mutes(tmp_path):
    """Test getting active mutes list."""
    from src.alerts.server_mute_manager import ServerMuteManager

    json_file = tmp_path / "server_mutes.json"
    manager = ServerMuteManager(json_path=str(json_file))

    manager.mute_server(timedelta(hours=2))
    manager.mute_array(timedelta(hours=4))

    mutes = manager.get_active_mutes()

    assert len(mutes) == 2
    categories = {m[0] for m in mutes}
    assert "server" in categories
    assert "array" in categories
```

**Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest tests/test_server_mute_manager.py -v`
Expected: FAIL with "cannot import name 'ServerMuteManager'"

**Step 3: Write minimal implementation**

Create `src/alerts/server_mute_manager.py`:

```python
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


class ServerMuteManager:
    """Manages mutes for Unraid server alerts (separate from container mutes)."""

    CATEGORIES = ("server", "array", "ups")

    def __init__(self, json_path: str):
        """Initialize ServerMuteManager.

        Args:
            json_path: Path to JSON file for persistence.
        """
        self._json_path = Path(json_path)
        self._mutes: dict[str, datetime] = {}
        self._load()

    def is_server_muted(self) -> bool:
        """Check if server (system) alerts are muted."""
        return self._is_muted("server")

    def is_array_muted(self) -> bool:
        """Check if array/disk alerts are muted."""
        return self._is_muted("array")

    def is_ups_muted(self) -> bool:
        """Check if UPS alerts are muted."""
        return self._is_muted("ups")

    def mute_server(self, duration: timedelta) -> datetime:
        """Mute all server alerts (system, array, UPS)."""
        expiry = datetime.now() + duration
        for cat in self.CATEGORIES:
            self._mutes[cat] = expiry
        self._save()
        logger.info(f"Muted all server alerts until {expiry}")
        return expiry

    def mute_array(self, duration: timedelta) -> datetime:
        """Mute just array/disk alerts."""
        expiry = datetime.now() + duration
        self._mutes["array"] = expiry
        self._save()
        logger.info(f"Muted array alerts until {expiry}")
        return expiry

    def mute_ups(self, duration: timedelta) -> datetime:
        """Mute just UPS alerts."""
        expiry = datetime.now() + duration
        self._mutes["ups"] = expiry
        self._save()
        logger.info(f"Muted UPS alerts until {expiry}")
        return expiry

    def unmute_server(self) -> bool:
        """Unmute all server alerts."""
        removed = False
        for cat in self.CATEGORIES:
            if cat in self._mutes:
                del self._mutes[cat]
                removed = True
        if removed:
            self._save()
            logger.info("Unmuted all server alerts")
        return removed

    def unmute_array(self) -> bool:
        """Unmute array alerts."""
        return self._unmute("array")

    def unmute_ups(self) -> bool:
        """Unmute UPS alerts."""
        return self._unmute("ups")

    def get_active_mutes(self) -> list[tuple[str, datetime]]:
        """Get list of active mutes.

        Returns:
            List of (category, expiry) tuples.
        """
        self._clean_expired()
        return [(cat, exp) for cat, exp in self._mutes.items()]

    def _is_muted(self, category: str) -> bool:
        """Check if a category is currently muted."""
        if category not in self._mutes:
            return False

        expiry = self._mutes[category]
        if datetime.now() >= expiry:
            del self._mutes[category]
            self._save()
            return False

        return True

    def _unmute(self, category: str) -> bool:
        """Unmute a specific category."""
        if category not in self._mutes:
            return False

        del self._mutes[category]
        self._save()
        logger.info(f"Unmuted {category} alerts")
        return True

    def _clean_expired(self) -> None:
        """Remove expired mutes."""
        now = datetime.now()
        expired = [cat for cat, exp in self._mutes.items() if now >= exp]
        for cat in expired:
            del self._mutes[cat]
        if expired:
            self._save()

    def _load(self) -> None:
        """Load mutes from JSON file."""
        if not self._json_path.exists():
            self._mutes = {}
            return

        try:
            with open(self._json_path, encoding="utf-8") as f:
                data = json.load(f)
                self._mutes = {
                    cat: datetime.fromisoformat(exp)
                    for cat, exp in data.items()
                }
        except (json.JSONDecodeError, IOError, ValueError) as e:
            logger.warning(f"Failed to load server mutes: {e}")
            self._mutes = {}

    def _save(self) -> None:
        """Save mutes to JSON file."""
        self._json_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            data = {
                cat: exp.isoformat()
                for cat, exp in self._mutes.items()
            }
            with open(self._json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except IOError as e:
            logger.error(f"Failed to save server mutes: {e}")
```

**Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && python -m pytest tests/test_server_mute_manager.py -v`
Expected: PASS (6 tests)

**Step 5: Commit**

```bash
git add src/alerts/server_mute_manager.py tests/test_server_mute_manager.py
git commit -m "feat: add ServerMuteManager for Unraid alert mutes"
```

---

## Task 6: SystemMonitor

**Files:**
- Create: `src/unraid/monitors/__init__.py`
- Create: `src/unraid/monitors/system_monitor.py`
- Test: `tests/test_unraid_system_monitor.py`

**Step 1: Write the failing test**

Create `tests/test_unraid_system_monitor.py`:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import timedelta


@pytest.mark.asyncio
async def test_system_monitor_triggers_temp_alert():
    """Test alert triggered when CPU temp exceeds threshold."""
    from src.unraid.monitors.system_monitor import UnraidSystemMonitor
    from src.config import UnraidConfig

    config = UnraidConfig(
        enabled=True,
        host="192.168.1.100",
        cpu_temp_threshold=80,
    )

    mock_client = AsyncMock()
    mock_client.get_system_metrics = AsyncMock(return_value={
        "cpu_percent": 50.0,
        "cpu_temperature": 85.0,  # Above threshold
        "memory_percent": 60.0,
        "memory_used": 1024 * 1024 * 1024 * 32,
        "uptime": "5 days",
    })

    alert_callback = AsyncMock()
    mute_manager = MagicMock()
    mute_manager.is_server_muted.return_value = False

    monitor = UnraidSystemMonitor(
        client=mock_client,
        config=config,
        on_alert=alert_callback,
        mute_manager=mute_manager,
    )

    await monitor.check_once()

    alert_callback.assert_called_once()
    call_args = alert_callback.call_args
    assert "CPU Temperature" in call_args[1]["title"]
    assert "85" in call_args[1]["message"]


@pytest.mark.asyncio
async def test_system_monitor_no_alert_when_muted():
    """Test no alert when server is muted."""
    from src.unraid.monitors.system_monitor import UnraidSystemMonitor
    from src.config import UnraidConfig

    config = UnraidConfig(
        enabled=True,
        host="192.168.1.100",
        cpu_temp_threshold=80,
    )

    mock_client = AsyncMock()
    mock_client.get_system_metrics = AsyncMock(return_value={
        "cpu_percent": 50.0,
        "cpu_temperature": 85.0,  # Above threshold
        "memory_percent": 60.0,
        "memory_used": 1024 * 1024 * 1024 * 32,
        "uptime": "5 days",
    })

    alert_callback = AsyncMock()
    mute_manager = MagicMock()
    mute_manager.is_server_muted.return_value = True  # Muted!

    monitor = UnraidSystemMonitor(
        client=mock_client,
        config=config,
        on_alert=alert_callback,
        mute_manager=mute_manager,
    )

    await monitor.check_once()

    alert_callback.assert_not_called()


@pytest.mark.asyncio
async def test_system_monitor_memory_alert():
    """Test alert triggered when memory exceeds threshold."""
    from src.unraid.monitors.system_monitor import UnraidSystemMonitor
    from src.config import UnraidConfig

    config = UnraidConfig(
        enabled=True,
        host="192.168.1.100",
        memory_usage_threshold=90,
    )

    mock_client = AsyncMock()
    mock_client.get_system_metrics = AsyncMock(return_value={
        "cpu_percent": 50.0,
        "cpu_temperature": 45.0,
        "memory_percent": 95.0,  # Above threshold
        "memory_used": 1024 * 1024 * 1024 * 60,
        "uptime": "5 days",
    })

    alert_callback = AsyncMock()
    mute_manager = MagicMock()
    mute_manager.is_server_muted.return_value = False

    monitor = UnraidSystemMonitor(
        client=mock_client,
        config=config,
        on_alert=alert_callback,
        mute_manager=mute_manager,
    )

    await monitor.check_once()

    alert_callback.assert_called_once()
    call_args = alert_callback.call_args
    assert "Memory" in call_args[1]["title"]


@pytest.mark.asyncio
async def test_system_monitor_no_alert_under_threshold():
    """Test no alert when metrics are under thresholds."""
    from src.unraid.monitors.system_monitor import UnraidSystemMonitor
    from src.config import UnraidConfig

    config = UnraidConfig(
        enabled=True,
        host="192.168.1.100",
        cpu_temp_threshold=80,
        memory_usage_threshold=90,
    )

    mock_client = AsyncMock()
    mock_client.get_system_metrics = AsyncMock(return_value={
        "cpu_percent": 50.0,
        "cpu_temperature": 45.0,  # Under threshold
        "memory_percent": 60.0,  # Under threshold
        "memory_used": 1024 * 1024 * 1024 * 32,
        "uptime": "5 days",
    })

    alert_callback = AsyncMock()
    mute_manager = MagicMock()
    mute_manager.is_server_muted.return_value = False

    monitor = UnraidSystemMonitor(
        client=mock_client,
        config=config,
        on_alert=alert_callback,
        mute_manager=mute_manager,
    )

    await monitor.check_once()

    alert_callback.assert_not_called()
```

**Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest tests/test_unraid_system_monitor.py -v`
Expected: FAIL with "cannot import name 'UnraidSystemMonitor'"

**Step 3: Write minimal implementation**

Create `src/unraid/monitors/__init__.py`:

```python
"""Unraid monitoring components."""

from src.unraid.monitors.system_monitor import UnraidSystemMonitor

__all__ = ["UnraidSystemMonitor"]
```

Create `src/unraid/monitors/system_monitor.py`:

```python
import asyncio
import logging
from typing import Callable, Awaitable, TYPE_CHECKING

if TYPE_CHECKING:
    from src.config import UnraidConfig
    from src.unraid.client import UnraidClientWrapper
    from src.alerts.server_mute_manager import ServerMuteManager

logger = logging.getLogger(__name__)


class UnraidSystemMonitor:
    """Monitors Unraid system metrics and triggers alerts."""

    def __init__(
        self,
        client: "UnraidClientWrapper",
        config: "UnraidConfig",
        on_alert: Callable[..., Awaitable[None]],
        mute_manager: "ServerMuteManager",
    ):
        """Initialize system monitor.

        Args:
            client: Connected UnraidClientWrapper.
            config: Unraid configuration with thresholds.
            on_alert: Async callback for sending alerts.
            mute_manager: Server mute manager.
        """
        self._client = client
        self._config = config
        self._on_alert = on_alert
        self._mute_manager = mute_manager
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the monitoring loop."""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._monitor_loop())
        logger.info("Unraid system monitor started")

    async def stop(self) -> None:
        """Stop the monitoring loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Unraid system monitor stopped")

    async def _monitor_loop(self) -> None:
        """Main monitoring loop."""
        while self._running:
            try:
                await self.check_once()
            except Exception as e:
                logger.error(f"Error in system monitor: {e}")

            await asyncio.sleep(self._config.poll_system_seconds)

    async def check_once(self) -> dict | None:
        """Check system metrics once and alert if needed.

        Returns:
            The metrics dict, or None on error.
        """
        try:
            metrics = await self._client.get_system_metrics()
        except Exception as e:
            logger.error(f"Failed to get system metrics: {e}")
            return None

        # Check if muted
        if self._mute_manager.is_server_muted():
            logger.debug("Server alerts muted, skipping checks")
            return metrics

        # Check CPU temperature
        cpu_temp = metrics.get("cpu_temperature", 0)
        if cpu_temp and cpu_temp > self._config.cpu_temp_threshold:
            await self._on_alert(
                title="High CPU Temperature",
                message=f"Temperature: {cpu_temp:.1f}°C (threshold: {self._config.cpu_temp_threshold}°C)\n"
                        f"Current load: {metrics.get('cpu_percent', 0):.1f}%",
                alert_type="server",
            )

        # Check CPU usage
        cpu_percent = metrics.get("cpu_percent", 0)
        if cpu_percent > self._config.cpu_usage_threshold:
            await self._on_alert(
                title="High CPU Usage",
                message=f"Usage: {cpu_percent:.1f}% (threshold: {self._config.cpu_usage_threshold}%)\n"
                        f"Temperature: {cpu_temp:.1f}°C",
                alert_type="server",
            )

        # Check memory usage
        memory_percent = metrics.get("memory_percent", 0)
        if memory_percent > self._config.memory_usage_threshold:
            memory_gb = metrics.get("memory_used", 0) / (1024**3)
            await self._on_alert(
                title="Memory Critical",
                message=f"Usage: {memory_percent:.1f}% (threshold: {self._config.memory_usage_threshold}%)\n"
                        f"Used: {memory_gb:.1f} GB",
                alert_type="server",
            )

        return metrics

    async def get_current_metrics(self) -> dict | None:
        """Get current metrics without alerting (for commands).

        Returns:
            Metrics dict or None on error.
        """
        try:
            return await self._client.get_system_metrics()
        except Exception as e:
            logger.error(f"Failed to get system metrics: {e}")
            return None
```

**Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && python -m pytest tests/test_unraid_system_monitor.py -v`
Expected: PASS (4 tests)

**Step 5: Commit**

```bash
git add src/unraid/monitors/__init__.py src/unraid/monitors/system_monitor.py tests/test_unraid_system_monitor.py
git commit -m "feat: add UnraidSystemMonitor for CPU/memory/temp monitoring"
```

---

## Task 7: /server command

**Files:**
- Create: `src/bot/unraid_commands.py`
- Test: `tests/test_unraid_commands.py`

**Step 1: Write the failing test**

Create `tests/test_unraid_commands.py`:

```python
import pytest
from unittest.mock import MagicMock, AsyncMock


@pytest.mark.asyncio
async def test_server_command_shows_metrics():
    """Test /server shows system metrics."""
    from src.bot.unraid_commands import server_command

    mock_monitor = MagicMock()
    mock_monitor.get_current_metrics = AsyncMock(return_value={
        "cpu_percent": 25.5,
        "cpu_temperature": 45.0,
        "memory_percent": 60.0,
        "memory_used": 1024 * 1024 * 1024 * 32,
        "uptime": "5 days, 3 hours",
    })

    handler = server_command(mock_monitor)

    message = MagicMock()
    message.text = "/server"
    message.answer = AsyncMock()

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]

    assert "25.5%" in response or "25.5" in response  # CPU
    assert "45" in response  # Temp
    assert "60" in response  # Memory
    assert "5 days" in response  # Uptime


@pytest.mark.asyncio
async def test_server_command_detailed():
    """Test /server detailed shows more info."""
    from src.bot.unraid_commands import server_command

    mock_monitor = MagicMock()
    mock_monitor.get_current_metrics = AsyncMock(return_value={
        "cpu_percent": 25.5,
        "cpu_temperature": 45.0,
        "cpu_power": 55.0,
        "memory_percent": 60.0,
        "memory_used": 1024 * 1024 * 1024 * 32,
        "swap_percent": 5.0,
        "uptime": "5 days, 3 hours",
    })

    handler = server_command(mock_monitor)

    message = MagicMock()
    message.text = "/server detailed"
    message.answer = AsyncMock()

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]

    assert "Swap" in response or "swap" in response


@pytest.mark.asyncio
async def test_server_command_not_connected():
    """Test /server when Unraid not connected."""
    from src.bot.unraid_commands import server_command

    mock_monitor = MagicMock()
    mock_monitor.get_current_metrics = AsyncMock(return_value=None)

    handler = server_command(mock_monitor)

    message = MagicMock()
    message.text = "/server"
    message.answer = AsyncMock()

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]

    assert "unavailable" in response.lower() or "error" in response.lower()
```

**Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest tests/test_unraid_commands.py -v`
Expected: FAIL with "cannot import name 'server_command'"

**Step 3: Write minimal implementation**

Create `src/bot/unraid_commands.py`:

```python
import logging
from typing import Callable, Awaitable, TYPE_CHECKING

from aiogram.types import Message

if TYPE_CHECKING:
    from src.unraid.monitors.system_monitor import UnraidSystemMonitor

logger = logging.getLogger(__name__)


def server_command(
    system_monitor: "UnraidSystemMonitor",
) -> Callable[[Message], Awaitable[None]]:
    """Factory for /server command handler."""

    async def handler(message: Message) -> None:
        text = (message.text or "").strip()
        detailed = "detailed" in text.lower()

        metrics = await system_monitor.get_current_metrics()

        if not metrics:
            await message.answer("🖥️ Unraid server unavailable or not configured.")
            return

        cpu = metrics.get("cpu_percent", 0)
        temp = metrics.get("cpu_temperature", 0)
        memory = metrics.get("memory_percent", 0)
        memory_gb = metrics.get("memory_used", 0) / (1024**3)
        uptime = metrics.get("uptime", "Unknown")

        if detailed:
            swap = metrics.get("swap_percent", 0)
            power = metrics.get("cpu_power", 0)

            lines = [
                "🖥️ *Unraid Server Status*\n",
                f"*CPU:* {cpu:.1f}%",
                f"*CPU Temp:* {temp:.1f}°C",
            ]

            if power:
                lines.append(f"*CPU Power:* {power:.1f}W")

            lines.extend([
                f"\n*Memory:* {memory:.1f}% ({memory_gb:.1f} GB)",
                f"*Swap:* {swap:.1f}%",
                f"\n*Uptime:* {uptime}",
            ])

            await message.answer("\n".join(lines), parse_mode="Markdown")
        else:
            # Compact summary
            response = (
                f"🖥️ *Unraid Server*\n\n"
                f"CPU: {cpu:.1f}% ({temp:.1f}°C) • "
                f"RAM: {memory:.1f}%\n"
                f"Uptime: {uptime}"
            )
            await message.answer(response, parse_mode="Markdown")

    return handler
```

**Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && python -m pytest tests/test_unraid_commands.py -v`
Expected: PASS (3 tests)

**Step 5: Commit**

```bash
git add src/bot/unraid_commands.py tests/test_unraid_commands.py
git commit -m "feat: add /server command for Unraid system status"
```

---

## Task 8: /mute-server command

**Files:**
- Modify: `src/bot/unraid_commands.py`
- Test: `tests/test_unraid_commands.py`

**Step 1: Write the failing test**

Add to `tests/test_unraid_commands.py`:

```python
@pytest.mark.asyncio
async def test_mute_server_command(tmp_path):
    """Test /mute-server mutes all server alerts."""
    from src.bot.unraid_commands import mute_server_command
    from src.alerts.server_mute_manager import ServerMuteManager

    json_file = tmp_path / "server_mutes.json"
    mute_manager = ServerMuteManager(json_path=str(json_file))

    handler = mute_server_command(mute_manager)

    message = MagicMock()
    message.text = "/mute-server 2h"
    message.answer = AsyncMock()

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]

    assert "Muted" in response
    assert mute_manager.is_server_muted()


@pytest.mark.asyncio
async def test_mute_server_command_no_duration(tmp_path):
    """Test /mute-server without duration shows usage."""
    from src.bot.unraid_commands import mute_server_command
    from src.alerts.server_mute_manager import ServerMuteManager

    json_file = tmp_path / "server_mutes.json"
    mute_manager = ServerMuteManager(json_path=str(json_file))

    handler = mute_server_command(mute_manager)

    message = MagicMock()
    message.text = "/mute-server"
    message.answer = AsyncMock()

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]

    assert "Usage" in response


@pytest.mark.asyncio
async def test_unmute_server_command(tmp_path):
    """Test /unmute-server unmutes all server alerts."""
    from src.bot.unraid_commands import unmute_server_command
    from src.alerts.server_mute_manager import ServerMuteManager
    from datetime import timedelta

    json_file = tmp_path / "server_mutes.json"
    mute_manager = ServerMuteManager(json_path=str(json_file))
    mute_manager.mute_server(timedelta(hours=2))

    handler = unmute_server_command(mute_manager)

    message = MagicMock()
    message.text = "/unmute-server"
    message.answer = AsyncMock()

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]

    assert "Unmuted" in response
    assert not mute_manager.is_server_muted()
```

**Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest tests/test_unraid_commands.py::test_mute_server_command -v`
Expected: FAIL with "cannot import name 'mute_server_command'"

**Step 3: Write minimal implementation**

Add to `src/bot/unraid_commands.py`:

```python
from src.alerts.mute_manager import parse_duration

if TYPE_CHECKING:
    from src.alerts.server_mute_manager import ServerMuteManager


def mute_server_command(
    mute_manager: "ServerMuteManager",
) -> Callable[[Message], Awaitable[None]]:
    """Factory for /mute-server command handler."""

    async def handler(message: Message) -> None:
        text = (message.text or "").strip()
        parts = text.split()

        if len(parts) < 2:
            await message.answer(
                "Usage: `/mute-server <duration>`\n\n"
                "Examples: `2h`, `30m`, `24h`\n\n"
                "This mutes ALL server alerts (system, array, UPS).",
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

        expiry = mute_manager.mute_server(duration)
        time_str = expiry.strftime("%H:%M")

        await message.answer(
            f"🔇 *Muted all server alerts* until {time_str}\n\n"
            f"System, array, and UPS alerts suppressed.\n"
            f"Use `/unmute-server` to unmute early.",
            parse_mode="Markdown",
        )

    return handler


def unmute_server_command(
    mute_manager: "ServerMuteManager",
) -> Callable[[Message], Awaitable[None]]:
    """Factory for /unmute-server command handler."""

    async def handler(message: Message) -> None:
        if mute_manager.unmute_server():
            await message.answer(
                "🔔 *Unmuted all server alerts*\n\n"
                "System, array, and UPS alerts are now enabled.",
                parse_mode="Markdown",
            )
        else:
            await message.answer("Server alerts are not currently muted.")

    return handler
```

**Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && python -m pytest tests/test_unraid_commands.py -v`
Expected: PASS (6 tests)

**Step 5: Commit**

```bash
git add src/bot/unraid_commands.py tests/test_unraid_commands.py
git commit -m "feat: add /mute-server and /unmute-server commands"
```

---

## Task 9: Register Unraid commands and update help

**Files:**
- Modify: `src/bot/commands.py`
- Modify: `src/bot/telegram_bot.py`
- Test: `tests/test_unraid_commands.py`

**Step 1: Write the failing test**

Add to `tests/test_unraid_commands.py`:

```python
def test_unraid_commands_in_help():
    """Test that Unraid commands are in help text."""
    from src.bot.commands import HELP_TEXT

    assert "/server" in HELP_TEXT
    assert "/mute-server" in HELP_TEXT
    assert "/unmute-server" in HELP_TEXT
```

**Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest tests/test_unraid_commands.py::test_unraid_commands_in_help -v`
Expected: FAIL with AssertionError

**Step 3: Write minimal implementation**

Update `HELP_TEXT` in `src/bot/commands.py` - add after the container mute commands:

```python
/server - Unraid system status (CPU, memory, temp)
/server detailed - Full system metrics
/mute-server <duration> - Mute all server alerts
/unmute-server - Unmute server alerts
```

Update `src/bot/telegram_bot.py`:

Add import:
```python
from src.bot.unraid_commands import server_command, mute_server_command, unmute_server_command
```

Update `register_commands` signature to add parameters:
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
    unraid_system_monitor: Any | None = None,
    server_mute_manager: Any | None = None,
) -> tuple[ConfirmationManager | None, DiagnosticService | None]:
```

Add registration at the end of the function (before `return`):
```python
    # Register Unraid commands
    if unraid_system_monitor is not None:
        dp.message.register(
            server_command(unraid_system_monitor),
            Command("server"),
        )

    if server_mute_manager is not None:
        dp.message.register(
            mute_server_command(server_mute_manager),
            Command("mute-server"),
        )
        dp.message.register(
            unmute_server_command(server_mute_manager),
            Command("unmute-server"),
        )
```

**Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && python -m pytest tests/test_unraid_commands.py -v`
Expected: PASS (7 tests)

**Step 5: Commit**

```bash
git add src/bot/commands.py src/bot/telegram_bot.py tests/test_unraid_commands.py
git commit -m "feat: register Unraid commands and update help"
```

---

## Task 10: Main integration

**Files:**
- Modify: `src/main.py`
- Test: `tests/test_unraid_integration.py`

**Step 1: Write the failing test**

Create `tests/test_unraid_integration.py`:

```python
import pytest


def test_unraid_components_can_be_created():
    """Test that Unraid components can be instantiated."""
    from src.config import UnraidConfig
    from src.alerts.server_mute_manager import ServerMuteManager

    config = UnraidConfig(enabled=True, host="192.168.1.100")
    assert config.enabled

    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".json") as f:
        manager = ServerMuteManager(json_path=f.name)
        assert manager is not None
```

**Step 2: Run test**

Run: `source .venv/bin/activate && python -m pytest tests/test_unraid_integration.py -v`
Expected: PASS (validation test)

**Step 3: Update main.py**

Add imports:
```python
from src.config import UnraidConfig
from src.alerts.server_mute_manager import ServerMuteManager
from src.unraid.client import UnraidClientWrapper
from src.unraid.monitors.system_monitor import UnraidSystemMonitor
```

After initializing `mute_manager`, add:
```python
    # Initialize Unraid components if configured
    unraid_client = None
    unraid_system_monitor = None
    server_mute_manager = None

    unraid_config = config.unraid
    if unraid_config.enabled and config.settings.unraid_api_key:
        logger.info("Initializing Unraid monitoring...")

        server_mute_manager = ServerMuteManager(json_path="data/server_mutes.json")

        unraid_client = UnraidClientWrapper(
            host=unraid_config.host,
            api_key=config.settings.unraid_api_key,
            port=unraid_config.port,
        )

        # Alert callback for Unraid
        async def on_server_alert(title: str, message: str, alert_type: str) -> None:
            chat_id = chat_id_store.get_chat_id()
            if chat_id:
                alert_text = f"🖥️ SERVER ALERT: {title}\n\n{message}"
                await bot.send_message(chat_id, alert_text)
            else:
                logger.warning("No chat ID yet, cannot send server alert")

        unraid_system_monitor = UnraidSystemMonitor(
            client=unraid_client,
            config=unraid_config,
            on_alert=on_server_alert,
            mute_manager=server_mute_manager,
        )
    else:
        if not unraid_config.enabled:
            logger.info("Unraid monitoring disabled in config")
        elif not config.settings.unraid_api_key:
            logger.warning("UNRAID_API_KEY not set - Unraid monitoring disabled")
```

Update the `register_commands` call to pass Unraid components:
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
    )
```

In the main try block, after starting other monitors, add:
```python
        # Connect to Unraid and start monitoring
        if unraid_client:
            try:
                await unraid_client.connect()
                if unraid_system_monitor:
                    await unraid_system_monitor.start()
            except Exception as e:
                logger.error(f"Failed to connect to Unraid: {e}")
```

In the finally block, add cleanup:
```python
        if unraid_system_monitor:
            await unraid_system_monitor.stop()
        if unraid_client:
            await unraid_client.disconnect()
```

**Step 4: Run all tests**

Run: `source .venv/bin/activate && python -m pytest -v`
Expected: All tests pass

**Step 5: Commit**

```bash
git add src/main.py tests/test_unraid_integration.py
git commit -m "feat: integrate Unraid monitoring into main application"
```

---

## Task 11: Final verification and v0.8.0 tag

**Step 1: Run all tests**

Run: `source .venv/bin/activate && python -m pytest -v`
Expected: All tests pass

**Step 2: Type check**

Run: `source .venv/bin/activate && python -m py_compile src/unraid/client.py src/unraid/monitors/system_monitor.py src/bot/unraid_commands.py src/alerts/server_mute_manager.py`
Expected: No errors

**Step 3: Commit and tag**

```bash
git add -A
git commit -m "feat: complete Unraid Phase 1 - system monitoring

- Add UnraidClientWrapper for API connection
- Add UnraidSystemMonitor for CPU/memory/temp alerts
- Add /server command for system status
- Add ServerMuteManager for server alert mutes
- Add /mute-server and /unmute-server commands"

git tag -a v0.8.0 -m "Unraid Phase 1: System Monitoring"
```

---

## Success Criteria

- [ ] `unraid-api` dependency installed
- [ ] UnraidConfig parses from config.yaml
- [ ] UNRAID_API_KEY read from environment
- [ ] UnraidClientWrapper connects and fetches metrics
- [ ] UnraidSystemMonitor alerts on CPU temp/usage, memory
- [ ] ServerMuteManager persists server mutes separately
- [ ] `/server` shows system metrics
- [ ] `/mute-server` and `/unmute-server` work
- [ ] Help text updated
- [ ] All tests pass
- [ ] Graceful degradation when Unraid unavailable
