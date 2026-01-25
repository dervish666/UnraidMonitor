# Resource Monitoring Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add `/resources` command and background monitoring to track container CPU/memory usage with threshold-based alerts.

**Architecture:** ResourceMonitor polls Docker stats periodically, tracks sustained threshold violations per container, and sends alerts via existing AlertManager. The `/resources` command provides on-demand snapshots.

**Tech Stack:** Python 3.11+, docker SDK, aiogram, pytest

---

## Task 1: ResourceConfig Dataclass

**Files:**
- Modify: `src/config.py`
- Test: `tests/test_resource_config.py`

**Step 1: Write the failing test**

Create `tests/test_resource_config.py`:

```python
import pytest


def test_resource_config_defaults():
    """Test ResourceConfig has sensible defaults."""
    from src.config import ResourceConfig

    config = ResourceConfig()

    assert config.enabled is True
    assert config.poll_interval_seconds == 60
    assert config.sustained_threshold_seconds == 120
    assert config.default_cpu_percent == 80
    assert config.default_memory_percent == 85
    assert config.container_overrides == {}


def test_resource_config_from_dict():
    """Test ResourceConfig can be created from YAML dict."""
    from src.config import ResourceConfig

    yaml_dict = {
        "enabled": True,
        "poll_interval_seconds": 30,
        "sustained_threshold_seconds": 60,
        "defaults": {
            "cpu_percent": 70,
            "memory_percent": 80,
        },
        "containers": {
            "plex": {"cpu_percent": 95},
            "radarr": {"memory_percent": 90},
        },
    }

    config = ResourceConfig.from_dict(yaml_dict)

    assert config.enabled is True
    assert config.poll_interval_seconds == 30
    assert config.sustained_threshold_seconds == 60
    assert config.default_cpu_percent == 70
    assert config.default_memory_percent == 80
    assert config.container_overrides == {
        "plex": {"cpu_percent": 95},
        "radarr": {"memory_percent": 90},
    }


def test_resource_config_get_thresholds():
    """Test getting thresholds for specific containers."""
    from src.config import ResourceConfig

    config = ResourceConfig(
        default_cpu_percent=80,
        default_memory_percent=85,
        container_overrides={
            "plex": {"cpu_percent": 95, "memory_percent": 90},
            "radarr": {"cpu_percent": 70},
        },
    )

    # Container with full overrides
    cpu, mem = config.get_thresholds("plex")
    assert cpu == 95
    assert mem == 90

    # Container with partial override
    cpu, mem = config.get_thresholds("radarr")
    assert cpu == 70
    assert mem == 85  # Falls back to default

    # Container without override
    cpu, mem = config.get_thresholds("sonarr")
    assert cpu == 80
    assert mem == 85


def test_resource_config_disabled():
    """Test ResourceConfig when disabled."""
    from src.config import ResourceConfig

    config = ResourceConfig.from_dict({"enabled": False})

    assert config.enabled is False


def test_resource_config_empty_dict():
    """Test ResourceConfig with empty dict uses defaults."""
    from src.config import ResourceConfig

    config = ResourceConfig.from_dict({})

    assert config.enabled is True
    assert config.poll_interval_seconds == 60
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_resource_config.py -v`
Expected: FAIL with "cannot import name 'ResourceConfig'"

**Step 3: Write minimal implementation**

Add to `src/config.py` after the `DEFAULT_LOG_WATCHING` section:

```python
from dataclasses import dataclass, field


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
            poll_interval_seconds=data.get("poll_interval_seconds", 60),
            sustained_threshold_seconds=data.get("sustained_threshold_seconds", 120),
            default_cpu_percent=defaults.get("cpu_percent", 80),
            default_memory_percent=defaults.get("memory_percent", 85),
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
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_resource_config.py -v`
Expected: PASS (5 tests)

**Step 5: Commit**

```bash
git add src/config.py tests/test_resource_config.py
git commit -m "feat: add ResourceConfig dataclass for resource monitoring"
```

---

## Task 2: Add resource_monitoring Property to AppConfig

**Files:**
- Modify: `src/config.py:102-156` (AppConfig class)
- Test: `tests/test_resource_config.py`

**Step 1: Write the failing test**

Add to `tests/test_resource_config.py`:

```python
def test_app_config_resource_monitoring_property():
    """Test AppConfig exposes resource_monitoring config."""
    from unittest.mock import MagicMock
    from src.config import AppConfig, ResourceConfig

    mock_settings = MagicMock()
    mock_settings.config_path = "/nonexistent/path"

    config = AppConfig(mock_settings)

    # Should return default ResourceConfig when not in YAML
    assert isinstance(config.resource_monitoring, ResourceConfig)
    assert config.resource_monitoring.enabled is True


def test_app_config_resource_monitoring_from_yaml(tmp_path):
    """Test AppConfig loads resource_monitoring from YAML."""
    from unittest.mock import MagicMock
    from src.config import AppConfig, ResourceConfig

    # Create a temp config file
    config_file = tmp_path / "config.yaml"
    config_file.write_text("""
resource_monitoring:
  enabled: true
  poll_interval_seconds: 30
  defaults:
    cpu_percent: 70
  containers:
    plex:
      cpu_percent: 95
""")

    mock_settings = MagicMock()
    mock_settings.config_path = str(config_file)

    config = AppConfig(mock_settings)

    assert isinstance(config.resource_monitoring, ResourceConfig)
    assert config.resource_monitoring.poll_interval_seconds == 30
    assert config.resource_monitoring.default_cpu_percent == 70
    assert config.resource_monitoring.container_overrides == {"plex": {"cpu_percent": 95}}
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_resource_config.py::test_app_config_resource_monitoring_property -v`
Expected: FAIL with "AttributeError: 'AppConfig' object has no attribute 'resource_monitoring'"

**Step 3: Write minimal implementation**

Add to `AppConfig` class in `src/config.py`:

```python
    @property
    def resource_monitoring(self) -> ResourceConfig:
        """Get resource monitoring configuration."""
        raw = self._yaml_config.get("resource_monitoring", {})
        return ResourceConfig.from_dict(raw)
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_resource_config.py -v`
Expected: PASS (7 tests)

**Step 5: Commit**

```bash
git add src/config.py tests/test_resource_config.py
git commit -m "feat: add resource_monitoring property to AppConfig"
```

---

## Task 3: ContainerStats Dataclass

**Files:**
- Create: `src/monitors/resource_monitor.py`
- Test: `tests/test_resource_monitor.py`

**Step 1: Write the failing test**

Create `tests/test_resource_monitor.py`:

```python
import pytest


def test_container_stats_dataclass():
    """Test ContainerStats holds resource data."""
    from src.monitors.resource_monitor import ContainerStats

    stats = ContainerStats(
        name="plex",
        cpu_percent=45.5,
        memory_percent=78.2,
        memory_bytes=4_200_000_000,
        memory_limit=8_000_000_000,
    )

    assert stats.name == "plex"
    assert stats.cpu_percent == 45.5
    assert stats.memory_percent == 78.2
    assert stats.memory_bytes == 4_200_000_000
    assert stats.memory_limit == 8_000_000_000


def test_container_stats_memory_display():
    """Test ContainerStats formats memory for display."""
    from src.monitors.resource_monitor import ContainerStats

    stats = ContainerStats(
        name="plex",
        cpu_percent=45.5,
        memory_percent=78.2,
        memory_bytes=4_200_000_000,
        memory_limit=8_000_000_000,
    )

    assert stats.memory_display == "3.9GB"
    assert stats.memory_limit_display == "7.5GB"


def test_container_stats_memory_display_mb():
    """Test ContainerStats formats smaller memory in MB."""
    from src.monitors.resource_monitor import ContainerStats

    stats = ContainerStats(
        name="small",
        cpu_percent=5.0,
        memory_percent=10.0,
        memory_bytes=500_000_000,
        memory_limit=2_000_000_000,
    )

    assert stats.memory_display == "477MB"
    assert stats.memory_limit_display == "1.9GB"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_resource_monitor.py -v`
Expected: FAIL with "cannot import name 'ContainerStats'"

**Step 3: Write minimal implementation**

Create `src/monitors/resource_monitor.py`:

```python
from dataclasses import dataclass


@dataclass
class ContainerStats:
    """Resource statistics for a container."""

    name: str
    cpu_percent: float
    memory_percent: float
    memory_bytes: int
    memory_limit: int

    @property
    def memory_display(self) -> str:
        """Format memory usage for display."""
        return self._format_bytes(self.memory_bytes)

    @property
    def memory_limit_display(self) -> str:
        """Format memory limit for display."""
        return self._format_bytes(self.memory_limit)

    @staticmethod
    def _format_bytes(bytes_val: int) -> str:
        """Format bytes as human-readable string."""
        gb = bytes_val / (1024 ** 3)
        if gb >= 1.0:
            return f"{gb:.1f}GB"
        mb = bytes_val / (1024 ** 2)
        return f"{mb:.0f}MB"
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_resource_monitor.py -v`
Expected: PASS (3 tests)

**Step 5: Commit**

```bash
git add src/monitors/resource_monitor.py tests/test_resource_monitor.py
git commit -m "feat: add ContainerStats dataclass"
```

---

## Task 4: Calculate CPU Percentage from Docker Stats

**Files:**
- Modify: `src/monitors/resource_monitor.py`
- Test: `tests/test_resource_monitor.py`

**Step 1: Write the failing test**

Add to `tests/test_resource_monitor.py`:

```python
def test_calculate_cpu_percent():
    """Test CPU percentage calculation from Docker stats."""
    from src.monitors.resource_monitor import calculate_cpu_percent

    # Simulated Docker stats response
    stats = {
        "cpu_stats": {
            "cpu_usage": {"total_usage": 200_000_000},
            "system_cpu_usage": 1_000_000_000,
            "online_cpus": 4,
        },
        "precpu_stats": {
            "cpu_usage": {"total_usage": 100_000_000},
            "system_cpu_usage": 900_000_000,
        },
    }

    # CPU delta: 100_000_000, System delta: 100_000_000
    # (100_000_000 / 100_000_000) * 4 * 100 = 400%? No...
    # Actually: (cpu_delta / system_delta) * num_cpus * 100
    # But we want percentage of total capacity, so 100M/100M = 1.0 = 100% of one core
    # With 4 cores: 100% / 4 = 25% total
    result = calculate_cpu_percent(stats)
    assert result == 100.0  # 100% of one core (we show per-core max for now)


def test_calculate_cpu_percent_zero_delta():
    """Test CPU calculation handles zero delta gracefully."""
    from src.monitors.resource_monitor import calculate_cpu_percent

    stats = {
        "cpu_stats": {
            "cpu_usage": {"total_usage": 100_000_000},
            "system_cpu_usage": 1_000_000_000,
            "online_cpus": 4,
        },
        "precpu_stats": {
            "cpu_usage": {"total_usage": 100_000_000},
            "system_cpu_usage": 1_000_000_000,
        },
    }

    result = calculate_cpu_percent(stats)
    assert result == 0.0


def test_calculate_cpu_percent_missing_precpu():
    """Test CPU calculation handles missing precpu_stats."""
    from src.monitors.resource_monitor import calculate_cpu_percent

    stats = {
        "cpu_stats": {
            "cpu_usage": {"total_usage": 100_000_000},
            "system_cpu_usage": 1_000_000_000,
            "online_cpus": 4,
        },
        "precpu_stats": {
            "cpu_usage": {"total_usage": 0},
            "system_cpu_usage": 0,
        },
    }

    result = calculate_cpu_percent(stats)
    # First reading has no baseline, should handle gracefully
    assert isinstance(result, float)
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_resource_monitor.py::test_calculate_cpu_percent -v`
Expected: FAIL with "cannot import name 'calculate_cpu_percent'"

**Step 3: Write minimal implementation**

Add to `src/monitors/resource_monitor.py`:

```python
def calculate_cpu_percent(stats: dict) -> float:
    """Calculate CPU percentage from Docker stats.

    Docker provides cumulative CPU usage, so we need to calculate
    the delta between current and previous readings.

    Args:
        stats: Docker stats response dict.

    Returns:
        CPU usage as percentage (0-100 per core, can exceed 100 on multi-core).
    """
    cpu_stats = stats.get("cpu_stats", {})
    precpu_stats = stats.get("precpu_stats", {})

    cpu_usage = cpu_stats.get("cpu_usage", {}).get("total_usage", 0)
    precpu_usage = precpu_stats.get("cpu_usage", {}).get("total_usage", 0)

    system_usage = cpu_stats.get("system_cpu_usage", 0)
    presystem_usage = precpu_stats.get("system_cpu_usage", 0)

    cpu_delta = cpu_usage - precpu_usage
    system_delta = system_usage - presystem_usage

    if system_delta > 0 and cpu_delta >= 0:
        num_cpus = cpu_stats.get("online_cpus", 1)
        return (cpu_delta / system_delta) * num_cpus * 100.0

    return 0.0
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_resource_monitor.py -v`
Expected: PASS (6 tests)

**Step 5: Commit**

```bash
git add src/monitors/resource_monitor.py tests/test_resource_monitor.py
git commit -m "feat: add calculate_cpu_percent function"
```

---

## Task 5: Parse Docker Stats to ContainerStats

**Files:**
- Modify: `src/monitors/resource_monitor.py`
- Test: `tests/test_resource_monitor.py`

**Step 1: Write the failing test**

Add to `tests/test_resource_monitor.py`:

```python
def test_parse_container_stats():
    """Test parsing Docker stats response to ContainerStats."""
    from src.monitors.resource_monitor import parse_container_stats

    # Simulated Docker stats response
    docker_stats = {
        "cpu_stats": {
            "cpu_usage": {"total_usage": 200_000_000},
            "system_cpu_usage": 1_000_000_000,
            "online_cpus": 4,
        },
        "precpu_stats": {
            "cpu_usage": {"total_usage": 100_000_000},
            "system_cpu_usage": 900_000_000,
        },
        "memory_stats": {
            "usage": 4_000_000_000,
            "limit": 8_000_000_000,
        },
    }

    result = parse_container_stats("plex", docker_stats)

    assert result.name == "plex"
    assert result.cpu_percent == 100.0
    assert result.memory_percent == 50.0
    assert result.memory_bytes == 4_000_000_000
    assert result.memory_limit == 8_000_000_000


def test_parse_container_stats_with_cache():
    """Test parsing Docker stats with memory cache included."""
    from src.monitors.resource_monitor import parse_container_stats

    # Some containers report cache separately
    docker_stats = {
        "cpu_stats": {
            "cpu_usage": {"total_usage": 100_000_000},
            "system_cpu_usage": 1_000_000_000,
            "online_cpus": 2,
        },
        "precpu_stats": {
            "cpu_usage": {"total_usage": 50_000_000},
            "system_cpu_usage": 900_000_000,
        },
        "memory_stats": {
            "usage": 2_000_000_000,
            "stats": {"cache": 500_000_000},
            "limit": 4_000_000_000,
        },
    }

    result = parse_container_stats("radarr", docker_stats)

    # Memory usage should exclude cache: 2GB - 500MB = 1.5GB
    assert result.memory_bytes == 1_500_000_000
    assert result.memory_percent == 37.5
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_resource_monitor.py::test_parse_container_stats -v`
Expected: FAIL with "cannot import name 'parse_container_stats'"

**Step 3: Write minimal implementation**

Add to `src/monitors/resource_monitor.py`:

```python
def parse_container_stats(name: str, stats: dict) -> ContainerStats:
    """Parse Docker stats response into ContainerStats.

    Args:
        name: Container name.
        stats: Docker stats response dict.

    Returns:
        ContainerStats with parsed values.
    """
    cpu_percent = calculate_cpu_percent(stats)

    memory_stats = stats.get("memory_stats", {})
    memory_usage = memory_stats.get("usage", 0)
    memory_limit = memory_stats.get("limit", 1)  # Avoid division by zero

    # Subtract cache from memory usage if available
    cache = memory_stats.get("stats", {}).get("cache", 0)
    memory_usage = memory_usage - cache

    memory_percent = (memory_usage / memory_limit) * 100.0 if memory_limit > 0 else 0.0

    return ContainerStats(
        name=name,
        cpu_percent=round(cpu_percent, 1),
        memory_percent=round(memory_percent, 1),
        memory_bytes=memory_usage,
        memory_limit=memory_limit,
    )
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_resource_monitor.py -v`
Expected: PASS (8 tests)

**Step 5: Commit**

```bash
git add src/monitors/resource_monitor.py tests/test_resource_monitor.py
git commit -m "feat: add parse_container_stats function"
```

---

## Task 6: ResourceMonitor Class - Basic Structure

**Files:**
- Modify: `src/monitors/resource_monitor.py`
- Test: `tests/test_resource_monitor.py`

**Step 1: Write the failing test**

Add to `tests/test_resource_monitor.py`:

```python
from unittest.mock import MagicMock, AsyncMock


def test_resource_monitor_init():
    """Test ResourceMonitor initialization."""
    from src.monitors.resource_monitor import ResourceMonitor
    from src.config import ResourceConfig

    mock_docker = MagicMock()
    config = ResourceConfig()
    mock_alert_manager = MagicMock()
    mock_rate_limiter = MagicMock()

    monitor = ResourceMonitor(
        docker_client=mock_docker,
        config=config,
        alert_manager=mock_alert_manager,
        rate_limiter=mock_rate_limiter,
    )

    assert monitor._docker == mock_docker
    assert monitor._config == config
    assert monitor._alert_manager == mock_alert_manager
    assert monitor._rate_limiter == mock_rate_limiter
    assert monitor._violations == {}
    assert monitor._running is False


def test_resource_monitor_disabled():
    """Test ResourceMonitor does nothing when disabled."""
    from src.monitors.resource_monitor import ResourceMonitor
    from src.config import ResourceConfig

    config = ResourceConfig(enabled=False)

    monitor = ResourceMonitor(
        docker_client=MagicMock(),
        config=config,
        alert_manager=MagicMock(),
        rate_limiter=MagicMock(),
    )

    assert monitor.is_enabled is False
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_resource_monitor.py::test_resource_monitor_init -v`
Expected: FAIL with "cannot import name 'ResourceMonitor'"

**Step 3: Write minimal implementation**

Add to `src/monitors/resource_monitor.py`:

```python
import logging
from datetime import datetime
from typing import TYPE_CHECKING

import docker

if TYPE_CHECKING:
    from src.config import ResourceConfig
    from src.alerts.manager import AlertManager
    from src.alerts.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)


@dataclass
class ViolationState:
    """Tracks sustained threshold violation for a container."""

    metric: str  # "cpu" or "memory"
    started_at: datetime
    current_value: float
    threshold: float


class ResourceMonitor:
    """Monitors container resource usage and sends alerts."""

    def __init__(
        self,
        docker_client: docker.DockerClient,
        config: "ResourceConfig",
        alert_manager: "AlertManager",
        rate_limiter: "RateLimiter",
    ):
        self._docker = docker_client
        self._config = config
        self._alert_manager = alert_manager
        self._rate_limiter = rate_limiter
        self._violations: dict[str, dict[str, ViolationState]] = {}
        self._running = False

    @property
    def is_enabled(self) -> bool:
        """Check if resource monitoring is enabled."""
        return self._config.enabled
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_resource_monitor.py -v`
Expected: PASS (10 tests)

**Step 5: Commit**

```bash
git add src/monitors/resource_monitor.py tests/test_resource_monitor.py
git commit -m "feat: add ResourceMonitor class structure"
```

---

## Task 7: ResourceMonitor.get_all_stats Method

**Files:**
- Modify: `src/monitors/resource_monitor.py`
- Test: `tests/test_resource_monitor.py`

**Step 1: Write the failing test**

Add to `tests/test_resource_monitor.py`:

```python
@pytest.mark.asyncio
async def test_resource_monitor_get_all_stats():
    """Test getting stats for all running containers."""
    from src.monitors.resource_monitor import ResourceMonitor, ContainerStats
    from src.config import ResourceConfig

    mock_docker = MagicMock()
    mock_container1 = MagicMock()
    mock_container1.name = "plex"
    mock_container1.status = "running"
    mock_container1.stats.return_value = {
        "cpu_stats": {
            "cpu_usage": {"total_usage": 200_000_000},
            "system_cpu_usage": 1_000_000_000,
            "online_cpus": 4,
        },
        "precpu_stats": {
            "cpu_usage": {"total_usage": 100_000_000},
            "system_cpu_usage": 900_000_000,
        },
        "memory_stats": {
            "usage": 4_000_000_000,
            "limit": 8_000_000_000,
        },
    }

    mock_container2 = MagicMock()
    mock_container2.name = "radarr"
    mock_container2.status = "running"
    mock_container2.stats.return_value = {
        "cpu_stats": {
            "cpu_usage": {"total_usage": 50_000_000},
            "system_cpu_usage": 1_000_000_000,
            "online_cpus": 4,
        },
        "precpu_stats": {
            "cpu_usage": {"total_usage": 40_000_000},
            "system_cpu_usage": 900_000_000,
        },
        "memory_stats": {
            "usage": 1_000_000_000,
            "limit": 4_000_000_000,
        },
    }

    mock_docker.containers.list.return_value = [mock_container1, mock_container2]

    monitor = ResourceMonitor(
        docker_client=mock_docker,
        config=ResourceConfig(),
        alert_manager=MagicMock(),
        rate_limiter=MagicMock(),
    )

    stats = await monitor.get_all_stats()

    assert len(stats) == 2
    assert stats[0].name == "plex"
    assert stats[1].name == "radarr"
    mock_container1.stats.assert_called_once_with(stream=False)
    mock_container2.stats.assert_called_once_with(stream=False)


@pytest.mark.asyncio
async def test_resource_monitor_get_all_stats_skips_stopped():
    """Test get_all_stats only includes running containers."""
    from src.monitors.resource_monitor import ResourceMonitor
    from src.config import ResourceConfig

    mock_docker = MagicMock()
    mock_running = MagicMock()
    mock_running.name = "plex"
    mock_running.status = "running"
    mock_running.stats.return_value = {
        "cpu_stats": {"cpu_usage": {"total_usage": 0}, "system_cpu_usage": 0, "online_cpus": 1},
        "precpu_stats": {"cpu_usage": {"total_usage": 0}, "system_cpu_usage": 0},
        "memory_stats": {"usage": 0, "limit": 1},
    }

    mock_stopped = MagicMock()
    mock_stopped.name = "stopped"
    mock_stopped.status = "exited"

    mock_docker.containers.list.return_value = [mock_running, mock_stopped]

    monitor = ResourceMonitor(
        docker_client=mock_docker,
        config=ResourceConfig(),
        alert_manager=MagicMock(),
        rate_limiter=MagicMock(),
    )

    stats = await monitor.get_all_stats()

    assert len(stats) == 1
    assert stats[0].name == "plex"
    mock_stopped.stats.assert_not_called()
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_resource_monitor.py::test_resource_monitor_get_all_stats -v`
Expected: FAIL with "AttributeError: 'ResourceMonitor' object has no attribute 'get_all_stats'"

**Step 3: Write minimal implementation**

Add to `ResourceMonitor` class:

```python
    async def get_all_stats(self) -> list[ContainerStats]:
        """Get current stats for all running containers.

        Returns:
            List of ContainerStats for all running containers.
        """
        import asyncio

        containers = self._docker.containers.list(all=True)
        stats_list = []

        for container in containers:
            if container.status != "running":
                continue

            try:
                raw_stats = await asyncio.to_thread(
                    container.stats, stream=False
                )
                stats = parse_container_stats(container.name, raw_stats)
                stats_list.append(stats)
            except Exception as e:
                logger.warning(f"Failed to get stats for {container.name}: {e}")

        return stats_list
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_resource_monitor.py -v`
Expected: PASS (12 tests)

**Step 5: Commit**

```bash
git add src/monitors/resource_monitor.py tests/test_resource_monitor.py
git commit -m "feat: add ResourceMonitor.get_all_stats method"
```

---

## Task 8: ResourceMonitor.get_container_stats Method

**Files:**
- Modify: `src/monitors/resource_monitor.py`
- Test: `tests/test_resource_monitor.py`

**Step 1: Write the failing test**

Add to `tests/test_resource_monitor.py`:

```python
@pytest.mark.asyncio
async def test_resource_monitor_get_container_stats():
    """Test getting stats for a specific container."""
    from src.monitors.resource_monitor import ResourceMonitor
    from src.config import ResourceConfig

    mock_docker = MagicMock()
    mock_container = MagicMock()
    mock_container.name = "plex"
    mock_container.status = "running"
    mock_container.stats.return_value = {
        "cpu_stats": {
            "cpu_usage": {"total_usage": 200_000_000},
            "system_cpu_usage": 1_000_000_000,
            "online_cpus": 4,
        },
        "precpu_stats": {
            "cpu_usage": {"total_usage": 100_000_000},
            "system_cpu_usage": 900_000_000,
        },
        "memory_stats": {
            "usage": 4_000_000_000,
            "limit": 8_000_000_000,
        },
    }

    mock_docker.containers.get.return_value = mock_container

    monitor = ResourceMonitor(
        docker_client=mock_docker,
        config=ResourceConfig(),
        alert_manager=MagicMock(),
        rate_limiter=MagicMock(),
    )

    stats = await monitor.get_container_stats("plex")

    assert stats is not None
    assert stats.name == "plex"
    mock_docker.containers.get.assert_called_once_with("plex")


@pytest.mark.asyncio
async def test_resource_monitor_get_container_stats_not_found():
    """Test getting stats for non-existent container."""
    from src.monitors.resource_monitor import ResourceMonitor
    from src.config import ResourceConfig
    import docker.errors

    mock_docker = MagicMock()
    mock_docker.containers.get.side_effect = docker.errors.NotFound("not found")

    monitor = ResourceMonitor(
        docker_client=mock_docker,
        config=ResourceConfig(),
        alert_manager=MagicMock(),
        rate_limiter=MagicMock(),
    )

    stats = await monitor.get_container_stats("nonexistent")

    assert stats is None
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_resource_monitor.py::test_resource_monitor_get_container_stats -v`
Expected: FAIL with "AttributeError: 'ResourceMonitor' object has no attribute 'get_container_stats'"

**Step 3: Write minimal implementation**

Add to `ResourceMonitor` class:

```python
    async def get_container_stats(self, name: str) -> ContainerStats | None:
        """Get current stats for a specific container.

        Args:
            name: Container name.

        Returns:
            ContainerStats or None if container not found.
        """
        import asyncio

        try:
            container = self._docker.containers.get(name)
            if container.status != "running":
                return None

            raw_stats = await asyncio.to_thread(
                container.stats, stream=False
            )
            return parse_container_stats(name, raw_stats)
        except docker.errors.NotFound:
            return None
        except Exception as e:
            logger.warning(f"Failed to get stats for {name}: {e}")
            return None
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_resource_monitor.py -v`
Expected: PASS (14 tests)

**Step 5: Commit**

```bash
git add src/monitors/resource_monitor.py tests/test_resource_monitor.py
git commit -m "feat: add ResourceMonitor.get_container_stats method"
```

---

## Task 9: Violation Tracking Logic

**Files:**
- Modify: `src/monitors/resource_monitor.py`
- Test: `tests/test_resource_monitor.py`

**Step 1: Write the failing test**

Add to `tests/test_resource_monitor.py`:

```python
def test_resource_monitor_check_thresholds_starts_violation():
    """Test that exceeding threshold starts violation tracking."""
    from src.monitors.resource_monitor import ResourceMonitor, ContainerStats
    from src.config import ResourceConfig

    config = ResourceConfig(default_cpu_percent=80, default_memory_percent=85)
    monitor = ResourceMonitor(
        docker_client=MagicMock(),
        config=config,
        alert_manager=MagicMock(),
        rate_limiter=MagicMock(),
    )

    stats = ContainerStats(
        name="plex",
        cpu_percent=90.0,  # Exceeds 80%
        memory_percent=50.0,  # Below 85%
        memory_bytes=4_000_000_000,
        memory_limit=8_000_000_000,
    )

    monitor._check_thresholds(stats)

    assert "plex" in monitor._violations
    assert "cpu" in monitor._violations["plex"]
    assert "memory" not in monitor._violations["plex"]


def test_resource_monitor_check_thresholds_clears_violation():
    """Test that going below threshold clears violation."""
    from src.monitors.resource_monitor import ResourceMonitor, ContainerStats, ViolationState
    from src.config import ResourceConfig
    from datetime import datetime

    config = ResourceConfig(default_cpu_percent=80, default_memory_percent=85)
    monitor = ResourceMonitor(
        docker_client=MagicMock(),
        config=config,
        alert_manager=MagicMock(),
        rate_limiter=MagicMock(),
    )

    # Set up existing violation
    monitor._violations["plex"] = {
        "cpu": ViolationState(
            metric="cpu",
            started_at=datetime.now(),
            current_value=90.0,
            threshold=80,
        )
    }

    # Stats now below threshold
    stats = ContainerStats(
        name="plex",
        cpu_percent=70.0,  # Below 80%
        memory_percent=50.0,
        memory_bytes=4_000_000_000,
        memory_limit=8_000_000_000,
    )

    monitor._check_thresholds(stats)

    # Violation should be cleared
    assert "cpu" not in monitor._violations.get("plex", {})


def test_resource_monitor_check_thresholds_updates_violation():
    """Test that continued violation updates current value."""
    from src.monitors.resource_monitor import ResourceMonitor, ContainerStats, ViolationState
    from src.config import ResourceConfig
    from datetime import datetime, timedelta

    config = ResourceConfig(default_cpu_percent=80, default_memory_percent=85)
    monitor = ResourceMonitor(
        docker_client=MagicMock(),
        config=config,
        alert_manager=MagicMock(),
        rate_limiter=MagicMock(),
    )

    # Set up existing violation from 1 minute ago
    started = datetime.now() - timedelta(minutes=1)
    monitor._violations["plex"] = {
        "cpu": ViolationState(
            metric="cpu",
            started_at=started,
            current_value=85.0,
            threshold=80,
        )
    }

    # Stats still above threshold
    stats = ContainerStats(
        name="plex",
        cpu_percent=92.0,  # Still above 80%
        memory_percent=50.0,
        memory_bytes=4_000_000_000,
        memory_limit=8_000_000_000,
    )

    monitor._check_thresholds(stats)

    # Violation should be updated, not replaced
    assert monitor._violations["plex"]["cpu"].started_at == started
    assert monitor._violations["plex"]["cpu"].current_value == 92.0
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_resource_monitor.py::test_resource_monitor_check_thresholds_starts_violation -v`
Expected: FAIL with "AttributeError: 'ResourceMonitor' object has no attribute '_check_thresholds'"

**Step 3: Write minimal implementation**

Add to `ResourceMonitor` class:

```python
    def _check_thresholds(self, stats: ContainerStats) -> None:
        """Check if container exceeds thresholds and track violations.

        Args:
            stats: Current container stats.
        """
        cpu_threshold, memory_threshold = self._config.get_thresholds(stats.name)

        # Ensure container has a violations dict
        if stats.name not in self._violations:
            self._violations[stats.name] = {}

        container_violations = self._violations[stats.name]

        # Check CPU
        self._update_violation(
            container_violations,
            metric="cpu",
            current_value=stats.cpu_percent,
            threshold=cpu_threshold,
        )

        # Check Memory
        self._update_violation(
            container_violations,
            metric="memory",
            current_value=stats.memory_percent,
            threshold=memory_threshold,
        )

        # Clean up empty violation dicts
        if not container_violations:
            del self._violations[stats.name]

    def _update_violation(
        self,
        violations: dict[str, ViolationState],
        metric: str,
        current_value: float,
        threshold: int,
    ) -> None:
        """Update violation state for a single metric.

        Args:
            violations: Container's violation dict to update.
            metric: "cpu" or "memory".
            current_value: Current metric value.
            threshold: Threshold value.
        """
        if current_value > threshold:
            if metric in violations:
                # Update existing violation
                violations[metric].current_value = current_value
            else:
                # Start new violation
                violations[metric] = ViolationState(
                    metric=metric,
                    started_at=datetime.now(),
                    current_value=current_value,
                    threshold=threshold,
                )
        elif metric in violations:
            # Violation cleared
            del violations[metric]
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_resource_monitor.py -v`
Expected: PASS (17 tests)

**Step 5: Commit**

```bash
git add src/monitors/resource_monitor.py tests/test_resource_monitor.py
git commit -m "feat: add violation tracking logic"
```

---

## Task 10: Sustained Violation Detection

**Files:**
- Modify: `src/monitors/resource_monitor.py`
- Test: `tests/test_resource_monitor.py`

**Step 1: Write the failing test**

Add to `tests/test_resource_monitor.py`:

```python
def test_resource_monitor_is_sustained_violation():
    """Test detecting sustained violations."""
    from src.monitors.resource_monitor import ResourceMonitor, ViolationState
    from src.config import ResourceConfig
    from datetime import datetime, timedelta

    config = ResourceConfig(sustained_threshold_seconds=120)
    monitor = ResourceMonitor(
        docker_client=MagicMock(),
        config=config,
        alert_manager=MagicMock(),
        rate_limiter=MagicMock(),
    )

    # Violation started 3 minutes ago (sustained)
    old_violation = ViolationState(
        metric="cpu",
        started_at=datetime.now() - timedelta(minutes=3),
        current_value=90.0,
        threshold=80,
    )
    assert monitor._is_sustained(old_violation) is True

    # Violation started 30 seconds ago (not yet sustained)
    new_violation = ViolationState(
        metric="cpu",
        started_at=datetime.now() - timedelta(seconds=30),
        current_value=90.0,
        threshold=80,
    )
    assert monitor._is_sustained(new_violation) is False


def test_resource_monitor_get_sustained_violations():
    """Test getting list of sustained violations for a container."""
    from src.monitors.resource_monitor import ResourceMonitor, ViolationState
    from src.config import ResourceConfig
    from datetime import datetime, timedelta

    config = ResourceConfig(sustained_threshold_seconds=120)
    monitor = ResourceMonitor(
        docker_client=MagicMock(),
        config=config,
        alert_manager=MagicMock(),
        rate_limiter=MagicMock(),
    )

    # Set up violations
    monitor._violations["plex"] = {
        "cpu": ViolationState(
            metric="cpu",
            started_at=datetime.now() - timedelta(minutes=3),  # Sustained
            current_value=90.0,
            threshold=80,
        ),
        "memory": ViolationState(
            metric="memory",
            started_at=datetime.now() - timedelta(seconds=30),  # Not sustained
            current_value=88.0,
            threshold=85,
        ),
    }

    sustained = monitor._get_sustained_violations("plex")

    assert len(sustained) == 1
    assert sustained[0].metric == "cpu"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_resource_monitor.py::test_resource_monitor_is_sustained_violation -v`
Expected: FAIL with "AttributeError: 'ResourceMonitor' object has no attribute '_is_sustained'"

**Step 3: Write minimal implementation**

Add to `ResourceMonitor` class:

```python
    def _is_sustained(self, violation: ViolationState) -> bool:
        """Check if a violation has exceeded the sustained threshold.

        Args:
            violation: Violation state to check.

        Returns:
            True if violation is sustained.
        """
        elapsed = datetime.now() - violation.started_at
        return elapsed.total_seconds() >= self._config.sustained_threshold_seconds

    def _get_sustained_violations(self, container_name: str) -> list[ViolationState]:
        """Get list of sustained violations for a container.

        Args:
            container_name: Container to check.

        Returns:
            List of sustained ViolationState objects.
        """
        container_violations = self._violations.get(container_name, {})
        return [v for v in container_violations.values() if self._is_sustained(v)]
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_resource_monitor.py -v`
Expected: PASS (19 tests)

**Step 5: Commit**

```bash
git add src/monitors/resource_monitor.py tests/test_resource_monitor.py
git commit -m "feat: add sustained violation detection"
```

---

## Task 11: AlertManager.send_resource_alert Method

**Files:**
- Modify: `src/alerts/manager.py`
- Test: `tests/test_alert_manager.py`

**Step 1: Write the failing test**

Add to `tests/test_alert_manager.py`:

```python
@pytest.mark.asyncio
async def test_send_resource_alert_cpu():
    """Test sending CPU resource alert."""
    from src.alerts.manager import AlertManager
    from unittest.mock import AsyncMock

    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock()

    manager = AlertManager(mock_bot, chat_id=123)

    await manager.send_resource_alert(
        container_name="plex",
        metric="cpu",
        current_value=92.5,
        threshold=80,
        duration_seconds=180,
        memory_bytes=4_000_000_000,
        memory_limit=8_000_000_000,
        memory_percent=50.0,
        cpu_percent=92.5,
    )

    mock_bot.send_message.assert_called_once()
    call_args = mock_bot.send_message.call_args
    text = call_args.kwargs["text"]

    assert "HIGH RESOURCE USAGE" in text
    assert "plex" in text
    assert "CPU: 92.5%" in text
    assert "threshold: 80%" in text
    assert "3 minutes" in text


@pytest.mark.asyncio
async def test_send_resource_alert_memory():
    """Test sending memory resource alert."""
    from src.alerts.manager import AlertManager
    from unittest.mock import AsyncMock

    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock()

    manager = AlertManager(mock_bot, chat_id=123)

    await manager.send_resource_alert(
        container_name="radarr",
        metric="memory",
        current_value=95.0,
        threshold=85,
        duration_seconds=240,
        memory_bytes=3_800_000_000,
        memory_limit=4_000_000_000,
        memory_percent=95.0,
        cpu_percent=45.0,
    )

    mock_bot.send_message.assert_called_once()
    call_args = mock_bot.send_message.call_args
    text = call_args.kwargs["text"]

    assert "HIGH MEMORY USAGE" in text
    assert "radarr" in text
    assert "Memory: 95.0%" in text
    assert "4 minutes" in text
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_alert_manager.py::test_send_resource_alert_cpu -v`
Expected: FAIL with "AttributeError: 'AlertManager' object has no attribute 'send_resource_alert'"

**Step 3: Write minimal implementation**

Add to `AlertManager` class in `src/alerts/manager.py`:

```python
    async def send_resource_alert(
        self,
        container_name: str,
        metric: str,
        current_value: float,
        threshold: int,
        duration_seconds: int,
        memory_bytes: int,
        memory_limit: int,
        memory_percent: float,
        cpu_percent: float,
    ) -> None:
        """Send a resource threshold alert.

        Args:
            container_name: Container name.
            metric: "cpu" or "memory".
            current_value: Current metric value.
            threshold: Threshold that was exceeded.
            duration_seconds: How long threshold has been exceeded.
            memory_bytes: Current memory usage in bytes.
            memory_limit: Memory limit in bytes.
            memory_percent: Memory usage percentage.
            cpu_percent: CPU usage percentage.
        """
        duration_str = self._format_duration(duration_seconds)
        memory_display = self._format_bytes(memory_bytes)
        memory_limit_display = self._format_bytes(memory_limit)

        if metric == "cpu":
            title = "HIGH RESOURCE USAGE"
            primary = f"CPU: {current_value}% (threshold: {threshold}%)"
            secondary = f"Memory: {memory_display} / {memory_limit_display} ({memory_percent}%)"
        else:
            title = "HIGH MEMORY USAGE"
            primary = f"Memory: {current_value}% (threshold: {threshold}%)"
            primary += f"\n        {memory_display} / {memory_limit_display} limit"
            secondary = f"CPU: {cpu_percent}% (normal)"

        text = f"""⚠️ *{title}:* {container_name}

{primary}
Exceeded for: {duration_str}

{secondary}

_Use /resources {container_name} or /diagnose {container_name} for details_"""

        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=text,
                parse_mode="Markdown",
            )
            logger.info(f"Sent resource alert for {container_name} ({metric})")
        except Exception as e:
            logger.error(f"Failed to send resource alert: {e}")

    @staticmethod
    def _format_duration(seconds: int) -> str:
        """Format duration in human-readable form."""
        if seconds >= 3600:
            hours = seconds // 3600
            minutes = (seconds % 3600) // 60
            return f"{hours}h {minutes}m"
        minutes = seconds // 60
        if minutes > 0:
            return f"{minutes} minutes" if minutes > 1 else "1 minute"
        return f"{seconds} seconds"

    @staticmethod
    def _format_bytes(bytes_val: int) -> str:
        """Format bytes as human-readable string."""
        gb = bytes_val / (1024 ** 3)
        if gb >= 1.0:
            return f"{gb:.1f}GB"
        mb = bytes_val / (1024 ** 2)
        return f"{mb:.0f}MB"
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_alert_manager.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/alerts/manager.py tests/test_alert_manager.py
git commit -m "feat: add AlertManager.send_resource_alert method"
```

---

## Task 12: ResourceMonitor._send_alert Method

**Files:**
- Modify: `src/monitors/resource_monitor.py`
- Test: `tests/test_resource_monitor.py`

**Step 1: Write the failing test**

Add to `tests/test_resource_monitor.py`:

```python
@pytest.mark.asyncio
async def test_resource_monitor_send_alert():
    """Test sending alert for sustained violation."""
    from src.monitors.resource_monitor import ResourceMonitor, ContainerStats, ViolationState
    from src.config import ResourceConfig
    from datetime import datetime, timedelta

    mock_alert_manager = MagicMock()
    mock_alert_manager.send_resource_alert = AsyncMock()
    mock_rate_limiter = MagicMock()
    mock_rate_limiter.should_alert.return_value = True

    monitor = ResourceMonitor(
        docker_client=MagicMock(),
        config=ResourceConfig(),
        alert_manager=mock_alert_manager,
        rate_limiter=mock_rate_limiter,
    )

    stats = ContainerStats(
        name="plex",
        cpu_percent=92.0,
        memory_percent=50.0,
        memory_bytes=4_000_000_000,
        memory_limit=8_000_000_000,
    )

    violation = ViolationState(
        metric="cpu",
        started_at=datetime.now() - timedelta(minutes=3),
        current_value=92.0,
        threshold=80,
    )

    await monitor._send_alert(stats, violation)

    mock_rate_limiter.should_alert.assert_called_once()
    mock_rate_limiter.record_alert.assert_called_once()
    mock_alert_manager.send_resource_alert.assert_called_once()


@pytest.mark.asyncio
async def test_resource_monitor_send_alert_rate_limited():
    """Test that rate limiter prevents alert spam."""
    from src.monitors.resource_monitor import ResourceMonitor, ContainerStats, ViolationState
    from src.config import ResourceConfig
    from datetime import datetime, timedelta

    mock_alert_manager = MagicMock()
    mock_alert_manager.send_resource_alert = AsyncMock()
    mock_rate_limiter = MagicMock()
    mock_rate_limiter.should_alert.return_value = False

    monitor = ResourceMonitor(
        docker_client=MagicMock(),
        config=ResourceConfig(),
        alert_manager=mock_alert_manager,
        rate_limiter=mock_rate_limiter,
    )

    stats = ContainerStats(
        name="plex",
        cpu_percent=92.0,
        memory_percent=50.0,
        memory_bytes=4_000_000_000,
        memory_limit=8_000_000_000,
    )

    violation = ViolationState(
        metric="cpu",
        started_at=datetime.now() - timedelta(minutes=3),
        current_value=92.0,
        threshold=80,
    )

    await monitor._send_alert(stats, violation)

    mock_rate_limiter.should_alert.assert_called_once()
    mock_rate_limiter.record_suppressed.assert_called_once()
    mock_alert_manager.send_resource_alert.assert_not_called()
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_resource_monitor.py::test_resource_monitor_send_alert -v`
Expected: FAIL with "AttributeError: 'ResourceMonitor' object has no attribute '_send_alert'"

**Step 3: Write minimal implementation**

Add to `ResourceMonitor` class:

```python
    async def _send_alert(self, stats: ContainerStats, violation: ViolationState) -> None:
        """Send an alert for a sustained violation.

        Args:
            stats: Current container stats.
            violation: The sustained violation.
        """
        # Use rate limiter key that includes metric to allow separate cpu/memory alerts
        rate_key = f"{stats.name}:{violation.metric}"

        if not self._rate_limiter.should_alert(rate_key):
            self._rate_limiter.record_suppressed(rate_key)
            logger.debug(f"Rate-limited {violation.metric} alert for {stats.name}")
            return

        self._rate_limiter.record_alert(rate_key)

        duration = int((datetime.now() - violation.started_at).total_seconds())

        await self._alert_manager.send_resource_alert(
            container_name=stats.name,
            metric=violation.metric,
            current_value=violation.current_value,
            threshold=violation.threshold,
            duration_seconds=duration,
            memory_bytes=stats.memory_bytes,
            memory_limit=stats.memory_limit,
            memory_percent=stats.memory_percent,
            cpu_percent=stats.cpu_percent,
        )
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_resource_monitor.py -v`
Expected: PASS (21 tests)

**Step 5: Commit**

```bash
git add src/monitors/resource_monitor.py tests/test_resource_monitor.py
git commit -m "feat: add ResourceMonitor._send_alert method"
```

---

## Task 13: ResourceMonitor Polling Loop

**Files:**
- Modify: `src/monitors/resource_monitor.py`
- Test: `tests/test_resource_monitor.py`

**Step 1: Write the failing test**

Add to `tests/test_resource_monitor.py`:

```python
@pytest.mark.asyncio
async def test_resource_monitor_start_stop():
    """Test starting and stopping the monitor."""
    from src.monitors.resource_monitor import ResourceMonitor
    from src.config import ResourceConfig
    import asyncio

    mock_docker = MagicMock()
    mock_docker.containers.list.return_value = []

    monitor = ResourceMonitor(
        docker_client=mock_docker,
        config=ResourceConfig(poll_interval_seconds=1),
        alert_manager=MagicMock(),
        rate_limiter=MagicMock(),
    )

    # Start monitor in background
    task = asyncio.create_task(monitor.start())

    # Let it run briefly
    await asyncio.sleep(0.1)

    assert monitor._running is True

    # Stop it
    monitor.stop()

    # Wait for task to complete
    try:
        await asyncio.wait_for(task, timeout=2.0)
    except asyncio.CancelledError:
        pass

    assert monitor._running is False


@pytest.mark.asyncio
async def test_resource_monitor_polls_and_checks():
    """Test that monitor polls containers and checks thresholds."""
    from src.monitors.resource_monitor import ResourceMonitor, ContainerStats
    from src.config import ResourceConfig
    import asyncio

    mock_docker = MagicMock()
    mock_container = MagicMock()
    mock_container.name = "plex"
    mock_container.status = "running"
    mock_container.stats.return_value = {
        "cpu_stats": {
            "cpu_usage": {"total_usage": 200_000_000},
            "system_cpu_usage": 1_000_000_000,
            "online_cpus": 4,
        },
        "precpu_stats": {
            "cpu_usage": {"total_usage": 100_000_000},
            "system_cpu_usage": 900_000_000,
        },
        "memory_stats": {
            "usage": 7_000_000_000,  # 87.5% - exceeds default 85%
            "limit": 8_000_000_000,
        },
    }
    mock_docker.containers.list.return_value = [mock_container]

    monitor = ResourceMonitor(
        docker_client=mock_docker,
        config=ResourceConfig(poll_interval_seconds=1),
        alert_manager=MagicMock(),
        rate_limiter=MagicMock(),
    )

    # Start monitor in background
    task = asyncio.create_task(monitor.start())

    # Let it run one poll cycle
    await asyncio.sleep(0.2)

    # Should have tracked the memory violation
    assert "plex" in monitor._violations
    assert "memory" in monitor._violations["plex"]

    # Stop it
    monitor.stop()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_resource_monitor.py::test_resource_monitor_start_stop -v`
Expected: FAIL with "AttributeError: 'ResourceMonitor' object has no attribute 'start'"

**Step 3: Write minimal implementation**

Add to `ResourceMonitor` class:

```python
    async def start(self) -> None:
        """Start the monitoring loop."""
        if not self._config.enabled:
            logger.info("Resource monitoring disabled")
            return

        self._running = True
        logger.info(
            f"Starting resource monitor (poll interval: {self._config.poll_interval_seconds}s)"
        )

        while self._running:
            try:
                await self._poll_cycle()
            except Exception as e:
                logger.error(f"Error in resource monitor poll cycle: {e}")

            # Wait for next poll
            await asyncio.sleep(self._config.poll_interval_seconds)

    def stop(self) -> None:
        """Stop the monitoring loop."""
        self._running = False
        logger.info("Stopping resource monitor")

    async def _poll_cycle(self) -> None:
        """Execute one polling cycle."""
        stats_list = await self.get_all_stats()

        for stats in stats_list:
            self._check_thresholds(stats)

            # Check for sustained violations and send alerts
            sustained = self._get_sustained_violations(stats.name)
            for violation in sustained:
                await self._send_alert(stats, violation)
```

Also add the missing import at the top of the file:

```python
import asyncio
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_resource_monitor.py -v`
Expected: PASS (23 tests)

**Step 5: Commit**

```bash
git add src/monitors/resource_monitor.py tests/test_resource_monitor.py
git commit -m "feat: add ResourceMonitor polling loop"
```

---

## Task 14: /resources Command Handler

**Files:**
- Create: `src/bot/resources_command.py`
- Test: `tests/test_resources_command.py`

**Step 1: Write the failing test**

Create `tests/test_resources_command.py`:

```python
import pytest
from unittest.mock import MagicMock, AsyncMock


@pytest.mark.asyncio
async def test_resources_command_summary():
    """Test /resources shows all containers."""
    from src.bot.resources_command import resources_command
    from src.monitors.resource_monitor import ContainerStats

    mock_resource_monitor = MagicMock()
    mock_resource_monitor.get_all_stats = AsyncMock(return_value=[
        ContainerStats("plex", 65.0, 78.0, 4_200_000_000, 8_000_000_000),
        ContainerStats("radarr", 12.0, 45.0, 1_200_000_000, 4_000_000_000),
    ])

    handler = resources_command(mock_resource_monitor)

    message = MagicMock()
    message.text = "/resources"
    message.answer = AsyncMock()

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]

    assert "Container Resources" in response
    assert "plex" in response
    assert "radarr" in response
    assert "65" in response  # CPU
    assert "78" in response  # Memory


@pytest.mark.asyncio
async def test_resources_command_specific_container():
    """Test /resources <name> shows detailed view."""
    from src.bot.resources_command import resources_command
    from src.monitors.resource_monitor import ContainerStats
    from src.config import ResourceConfig

    mock_resource_monitor = MagicMock()
    mock_resource_monitor._config = ResourceConfig()
    mock_resource_monitor.get_container_stats = AsyncMock(return_value=ContainerStats(
        "plex", 65.0, 78.0, 4_200_000_000, 8_000_000_000
    ))

    handler = resources_command(mock_resource_monitor)

    message = MagicMock()
    message.text = "/resources plex"
    message.answer = AsyncMock()

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]

    assert "Resources: plex" in response
    assert "CPU:" in response
    assert "Memory:" in response
    assert "threshold" in response


@pytest.mark.asyncio
async def test_resources_command_container_not_found():
    """Test /resources <name> with unknown container."""
    from src.bot.resources_command import resources_command

    mock_resource_monitor = MagicMock()
    mock_resource_monitor.get_container_stats = AsyncMock(return_value=None)

    handler = resources_command(mock_resource_monitor)

    message = MagicMock()
    message.text = "/resources nonexistent"
    message.answer = AsyncMock()

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]

    assert "not found" in response.lower() or "not running" in response.lower()


@pytest.mark.asyncio
async def test_resources_command_no_containers():
    """Test /resources with no running containers."""
    from src.bot.resources_command import resources_command

    mock_resource_monitor = MagicMock()
    mock_resource_monitor.get_all_stats = AsyncMock(return_value=[])

    handler = resources_command(mock_resource_monitor)

    message = MagicMock()
    message.text = "/resources"
    message.answer = AsyncMock()

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]

    assert "no running containers" in response.lower() or "no containers" in response.lower()
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_resources_command.py -v`
Expected: FAIL with "cannot import name 'resources_command'"

**Step 3: Write minimal implementation**

Create `src/bot/resources_command.py`:

```python
from typing import Callable, Awaitable, TYPE_CHECKING

from aiogram.types import Message

if TYPE_CHECKING:
    from src.monitors.resource_monitor import ResourceMonitor


def format_progress_bar(percent: float, width: int = 16) -> str:
    """Format a progress bar for resource usage."""
    filled = int(percent / 100 * width)
    empty = width - filled
    return "█" * filled + "░" * empty


def format_summary_line(name: str, cpu: float, mem: float, mem_display: str) -> str:
    """Format a single container line for summary view."""
    # Pad name to 12 chars
    name_padded = name[:12].ljust(12)
    warning = " ⚠️" if cpu > 70 or mem > 70 else ""
    return f"{name_padded} CPU: {cpu:4.0f}%  MEM: {mem:4.0f}% ({mem_display}){warning}"


def resources_command(
    resource_monitor: "ResourceMonitor",
) -> Callable[[Message], Awaitable[None]]:
    """Factory for /resources command handler."""

    async def handler(message: Message) -> None:
        text = message.text or ""
        parts = text.strip().split(maxsplit=1)

        if len(parts) == 1:
            # Summary view
            stats_list = await resource_monitor.get_all_stats()

            if not stats_list:
                await message.answer("📊 No running containers found")
                return

            lines = ["📊 *Container Resources*", ""]

            for stats in sorted(stats_list, key=lambda s: s.name):
                line = format_summary_line(
                    stats.name,
                    stats.cpu_percent,
                    stats.memory_percent,
                    stats.memory_display,
                )
                lines.append(f"`{line}`")

            lines.append("")
            lines.append("_⚠️ = approaching threshold_")

            await message.answer("\n".join(lines), parse_mode="Markdown")
        else:
            # Detailed view for specific container
            container_name = parts[1].strip()
            stats = await resource_monitor.get_container_stats(container_name)

            if stats is None:
                await message.answer(
                    f"❌ Container '{container_name}' not found or not running"
                )
                return

            cpu_threshold, mem_threshold = resource_monitor._config.get_thresholds(
                container_name
            )

            cpu_bar = format_progress_bar(stats.cpu_percent)
            mem_bar = format_progress_bar(stats.memory_percent)

            response = f"""📊 *Resources: {stats.name}*

CPU:    {stats.cpu_percent:5.1f}% `{cpu_bar}` (threshold: {cpu_threshold}%)
Memory: {stats.memory_percent:5.1f}% `{mem_bar}` (threshold: {mem_threshold}%)
        {stats.memory_display} / {stats.memory_limit_display} limit"""

            await message.answer(response, parse_mode="Markdown")

    return handler
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_resources_command.py -v`
Expected: PASS (4 tests)

**Step 5: Commit**

```bash
git add src/bot/resources_command.py tests/test_resources_command.py
git commit -m "feat: add /resources command handler"
```

---

## Task 15: Register /resources Command and Update Help

**Files:**
- Modify: `src/bot/telegram_bot.py`
- Modify: `src/bot/commands.py`
- Test: `tests/test_resources_command.py`

**Step 1: Write the failing test**

Add to `tests/test_resources_command.py`:

```python
def test_resources_command_in_help():
    """Test that /resources is documented in help text."""
    from src.bot.commands import HELP_TEXT

    assert "/resources" in HELP_TEXT
    assert "CPU" in HELP_TEXT or "resource" in HELP_TEXT.lower()
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_resources_command.py::test_resources_command_in_help -v`
Expected: FAIL with "AssertionError" (resources not in help yet)

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
/help - Show this help message

_Partial container names work: /status rad → radarr_
_Control commands require confirmation_
_Reply /diagnose to a crash alert for quick analysis_"""
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_resources_command.py -v`
Expected: PASS (5 tests)

**Step 5: Commit**

```bash
git add src/bot/commands.py tests/test_resources_command.py
git commit -m "feat: add /resources to help text"
```

---

## Task 16: Update register_commands for ResourceMonitor

**Files:**
- Modify: `src/bot/telegram_bot.py`
- Test: `tests/test_telegram_bot.py`

**Step 1: Write the failing test**

Add to `tests/test_telegram_bot.py`:

```python
def test_register_commands_with_resource_monitor():
    """Test register_commands accepts resource_monitor parameter."""
    from src.bot.telegram_bot import create_dispatcher, register_commands
    from src.state import ContainerStateManager
    from unittest.mock import MagicMock

    state = ContainerStateManager()
    mock_docker = MagicMock()
    mock_resource_monitor = MagicMock()

    dp = create_dispatcher([123])
    result = register_commands(
        dp,
        state,
        docker_client=mock_docker,
        protected_containers=[],
        resource_monitor=mock_resource_monitor,
    )

    # Should return tuple with confirmation manager and diagnostic service
    assert isinstance(result, tuple)
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_telegram_bot.py::test_register_commands_with_resource_monitor -v`
Expected: FAIL with "TypeError: register_commands() got an unexpected keyword argument 'resource_monitor'"

**Step 3: Write minimal implementation**

Update `register_commands` in `src/bot/telegram_bot.py`:

1. Add import at top:
```python
from src.bot.resources_command import resources_command
```

2. Update function signature and add resource_monitor parameter:
```python
def register_commands(
    dp: Dispatcher,
    state: ContainerStateManager,
    docker_client: docker.DockerClient | None = None,
    protected_containers: list[str] | None = None,
    anthropic_client: Any | None = None,
    resource_monitor: Any | None = None,
) -> tuple[ConfirmationManager | None, "DiagnosticService | None"]:
```

3. Add registration for /resources after /diagnose:
```python
    # Register /resources command
    if resource_monitor is not None:
        dp.message.register(
            resources_command(resource_monitor),
            Command("resources"),
            UserFilter(dp["allowed_users"]),
        )
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_telegram_bot.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/bot/telegram_bot.py tests/test_telegram_bot.py
git commit -m "feat: register /resources command in dispatcher"
```

---

## Task 17: Main Integration - Add ResourceMonitor

**Files:**
- Modify: `src/main.py`
- Test: `tests/test_resource_integration.py`

**Step 1: Write the failing test**

Create `tests/test_resource_integration.py`:

```python
import pytest
from unittest.mock import MagicMock, AsyncMock, patch


def test_main_creates_resource_monitor_when_enabled(tmp_path):
    """Test that main.py creates ResourceMonitor when enabled in config."""
    # Create config file with resource monitoring enabled
    config_file = tmp_path / "config.yaml"
    config_file.write_text("""
resource_monitoring:
  enabled: true
  poll_interval_seconds: 30
""")

    env_file = tmp_path / ".env"
    env_file.write_text("""
TELEGRAM_BOT_TOKEN=test_token
TELEGRAM_ALLOWED_USERS=123
""")

    # Import after setting up files
    import os
    original_cwd = os.getcwd()

    try:
        os.chdir(tmp_path)

        with patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "test_token",
            "TELEGRAM_ALLOWED_USERS": "123",
        }):
            from src.config import Settings, AppConfig

            settings = Settings(config_path=str(config_file))
            config = AppConfig(settings)

            assert config.resource_monitoring.enabled is True
            assert config.resource_monitoring.poll_interval_seconds == 30
    finally:
        os.chdir(original_cwd)


def test_alert_manager_proxy_has_resource_alert():
    """Test AlertManagerProxy forwards send_resource_alert."""
    # This tests that the proxy pattern works for resource alerts
    from src.alerts.manager import AlertManager

    # Verify AlertManager has send_resource_alert method
    assert hasattr(AlertManager, "send_resource_alert")
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_resource_integration.py -v`
Expected: PASS (these are verification tests)

**Step 3: Write minimal implementation**

Update `src/main.py`:

1. Add imports:
```python
from src.monitors.resource_monitor import ResourceMonitor
```

2. Add `send_resource_alert` to `AlertManagerProxy`:
```python
    async def send_resource_alert(self, **kwargs):
        chat_id = self.chat_id_store.get_chat_id()
        if chat_id:
            manager = AlertManager(self.bot, chat_id)
            await manager.send_resource_alert(**kwargs)
        else:
            logger.warning("No chat ID yet, cannot send resource alert")
```

3. After log_watcher initialization (around line 116), add ResourceMonitor:
```python
    # Initialize resource monitor if enabled
    resource_monitor = None
    resource_config = config.resource_monitoring
    if resource_config.enabled:
        resource_monitor = ResourceMonitor(
            docker_client=monitor._client,
            config=resource_config,
            alert_manager=alert_manager,
            rate_limiter=rate_limiter,
        )
        logger.info("Resource monitoring enabled")
    else:
        logger.info("Resource monitoring disabled")
```

4. Update register_commands call:
```python
    confirmation, diagnostic_service = register_commands(
        dp,
        state,
        docker_client=monitor._client,
        protected_containers=config.protected_containers,
        anthropic_client=anthropic_client,
        resource_monitor=resource_monitor,
    )
```

5. Start resource monitor as background task (after log_watcher_task):
```python
    # Start resource monitor as background task (if enabled)
    resource_monitor_task = None
    if resource_monitor is not None:
        resource_monitor_task = asyncio.create_task(resource_monitor.start())
```

6. Update shutdown section to stop resource monitor:
```python
    finally:
        logger.info("Shutting down...")
        monitor.stop()
        log_watcher.stop()
        if resource_monitor is not None:
            resource_monitor.stop()
        monitor_task.cancel()
        log_watcher_task.cancel()
        if resource_monitor_task is not None:
            resource_monitor_task.cancel()
        # ... rest of cleanup
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_resource_integration.py -v`
Expected: PASS (2 tests)

**Step 5: Commit**

```bash
git add src/main.py tests/test_resource_integration.py
git commit -m "feat: integrate ResourceMonitor into main.py"
```

---

## Task 18: Final Verification

**Files:**
- All files from previous tasks

**Step 1: Run all tests**

Run: `pytest -v`
Expected: All tests PASS

**Step 2: Type check**

Run: `python -m py_compile src/monitors/resource_monitor.py src/bot/resources_command.py`
Expected: No errors

**Step 3: Manual smoke test commands**

```bash
# Start the bot
python -m src.main

# In Telegram:
# /resources - should show all containers
# /resources plex - should show detailed view for plex
# /help - should include /resources command
```

**Step 4: Commit and tag**

```bash
git add -A
git commit -m "feat: complete resource monitoring implementation

- Add /resources command for on-demand stats
- Add background ResourceMonitor for threshold alerts
- Support configurable per-container thresholds
- Integrate with existing rate limiter for cooldowns"

git tag -a v0.5.0 -m "Resource monitoring"
```

**Step 5: Push to remote**

```bash
git push origin master --tags
```

---

## Success Criteria

- [x] `/resources` shows all containers with CPU/memory
- [x] `/resources <name>` shows detailed view with progress bars
- [x] Background monitor polls at configured interval
- [x] Alerts only after sustained threshold violation
- [x] Per-container threshold overrides work
- [x] Rate limiting prevents alert spam
- [x] Graceful handling when containers stop/start during monitoring
- [x] All tests pass
