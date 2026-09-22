import pytest
import time
from unittest.mock import AsyncMock, MagicMock, patch
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


# --- Metric caching tests ---

import asyncio


def _make_monitor(**overrides):
    """Helper to build a monitor with sensible defaults."""
    from src.unraid.monitors.system_monitor import UnraidSystemMonitor
    from src.config import UnraidConfig

    config = UnraidConfig(
        enabled=True,
        host="192.168.1.100",
        cpu_temp_threshold=80,
        cpu_usage_threshold=95,
        memory_usage_threshold=90,
    )

    mock_client = AsyncMock()
    alert_callback = AsyncMock()
    mute_manager = MagicMock()
    mute_manager.is_server_muted.return_value = False

    kw = dict(
        client=mock_client,
        config=config,
        on_alert=alert_callback,
        mute_manager=mute_manager,
    )
    kw.update(overrides)

    monitor = UnraidSystemMonitor(**kw)
    return monitor, mock_client, alert_callback


@pytest.mark.asyncio
async def test_get_current_metrics_returns_cached():
    """get_current_metrics should return cached data from last check_once()."""
    monitor, mock_client, _ = _make_monitor()

    metrics = {"cpu_percent": 50.0, "cpu_temperature": 60.0, "memory_percent": 40.0}
    mock_client.get_system_metrics.return_value = metrics

    await monitor.check_once()

    # Reset mock so we can assert it is NOT called again
    mock_client.get_system_metrics.reset_mock()
    result = await monitor.get_current_metrics()

    assert result == metrics
    mock_client.get_system_metrics.assert_not_called()


@pytest.mark.asyncio
async def test_get_current_metrics_fetches_when_cache_expired():
    """get_current_metrics should fetch fresh data when cache has expired."""
    from src.unraid.monitors import system_monitor as mod

    monitor, mock_client, _ = _make_monitor()

    old_metrics = {"cpu_percent": 50.0, "cpu_temperature": 60.0, "memory_percent": 40.0}
    mock_client.get_system_metrics.return_value = old_metrics

    await monitor.check_once()

    # Simulate cache expiry by rewinding the cache timestamp
    monitor._metrics_cache_time = time.monotonic() - mod._CACHE_TTL - 1

    fresh_metrics = {"cpu_percent": 70.0, "cpu_temperature": 65.0, "memory_percent": 55.0}
    mock_client.get_system_metrics.reset_mock()
    mock_client.get_system_metrics.return_value = fresh_metrics

    result = await monitor.get_current_metrics()

    assert result == fresh_metrics
    mock_client.get_system_metrics.assert_called_once()


@pytest.mark.asyncio
async def test_get_current_metrics_fetches_when_no_cache():
    """get_current_metrics should fetch fresh data when cache is empty."""
    monitor, mock_client, _ = _make_monitor()

    metrics = {"cpu_percent": 50.0, "cpu_temperature": 60.0, "memory_percent": 40.0}
    mock_client.get_system_metrics.return_value = metrics

    # Never called check_once, so no cache
    result = await monitor.get_current_metrics()

    assert result == metrics
    mock_client.get_system_metrics.assert_called_once()


@pytest.mark.asyncio
async def test_get_array_status_returns_cached():
    """get_array_status should return cached data within TTL."""
    monitor, mock_client, _ = _make_monitor()

    array_data = {"state": "Started", "disks": []}
    mock_client.get_array_status.return_value = array_data

    # First call populates the cache
    result1 = await monitor.get_array_status()
    assert result1 == array_data

    mock_client.get_array_status.reset_mock()

    # Second call should use cache
    result2 = await monitor.get_array_status()
    assert result2 == array_data
    mock_client.get_array_status.assert_not_called()


@pytest.mark.asyncio
async def test_get_array_status_fetches_when_cache_expired():
    """get_array_status should fetch fresh data when cache has expired."""
    from src.unraid.monitors import system_monitor as mod

    monitor, mock_client, _ = _make_monitor()

    old_array = {"state": "Started", "disks": []}
    mock_client.get_array_status.return_value = old_array

    result1 = await monitor.get_array_status()
    assert result1 == old_array

    # Expire the cache
    monitor._array_cache_time = time.monotonic() - mod._CACHE_TTL - 1

    new_array = {"state": "Started", "disks": [{"name": "disk1"}]}
    mock_client.get_array_status.return_value = new_array

    result2 = await monitor.get_array_status()
    assert result2 == new_array
    mock_client.get_array_status.assert_called()


@pytest.mark.asyncio
async def test_check_once_updates_cache_timestamp():
    """check_once should update the cache timestamp each time it succeeds."""
    monitor, mock_client, _ = _make_monitor()

    metrics = {"cpu_percent": 10.0, "cpu_temperature": 40.0, "memory_percent": 30.0}
    mock_client.get_system_metrics.return_value = metrics

    await monitor.check_once()
    first_cache_time = monitor._metrics_cache_time

    # Small sleep to ensure monotonic time advances
    await asyncio.sleep(0.01)

    await monitor.check_once()
    second_cache_time = monitor._metrics_cache_time

    assert second_cache_time > first_cache_time
    assert monitor._cached_metrics == metrics


@pytest.mark.asyncio
async def test_cpu_usage_alert_needs_a_sustained_spike():
    """One hot 30s sample is a compile, not an alert; two in a row is."""
    from src.unraid.monitors.system_monitor import UnraidSystemMonitor
    from src.config import UnraidConfig

    config = UnraidConfig(enabled=True, host="192.168.1.100", cpu_usage_threshold=90)
    readings = iter([95.0, 40.0, 95.0, 96.0])
    mock_client = AsyncMock()

    async def metrics():
        return {"cpu_percent": next(readings), "memory_percent": 10.0}

    mock_client.get_system_metrics = metrics
    alert_callback = AsyncMock()
    mute_manager = MagicMock()
    mute_manager.is_server_muted.return_value = False
    monitor = UnraidSystemMonitor(
        client=mock_client, config=config, on_alert=alert_callback, mute_manager=mute_manager,
    )

    await monitor.check_once()
    await monitor.check_once()
    await monitor.check_once()
    alert_callback.assert_not_called()

    await monitor.check_once()
    alert_callback.assert_called_once()
    assert "CPU Usage" in alert_callback.call_args[1]["title"]
