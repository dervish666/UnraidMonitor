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
