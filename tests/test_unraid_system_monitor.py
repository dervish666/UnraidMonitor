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
