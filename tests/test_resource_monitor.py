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
