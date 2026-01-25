import pytest
from unittest.mock import MagicMock, AsyncMock


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
    # (100_000_000 / 100_000_000) * 4 * 100 = 400%
    # This shows CPU usage where 100% = one full core
    # With 4 cores at full usage: 400%
    result = calculate_cpu_percent(stats)
    assert result == 400.0  # 100% per core * 4 cores = 400%


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
    assert result.cpu_percent == 400.0  # 100% per core * 4 cores
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