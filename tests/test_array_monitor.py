import pytest
from unittest.mock import MagicMock, AsyncMock


@pytest.mark.asyncio
async def test_array_monitor_alerts_on_high_disk_temp():
    """Test array monitor alerts when disk temp exceeds threshold."""
    from src.unraid.monitors.array_monitor import ArrayMonitor

    mock_client = MagicMock()
    mock_client.get_array_status = AsyncMock(return_value={
        "state": "STARTED",
        "disks": [{"name": "disk1", "temp": 55, "status": "DISK_OK"}],
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


@pytest.mark.asyncio
async def test_array_monitor_alerts_on_disk_problem():
    """Test array monitor alerts on disk status problem."""
    from src.unraid.monitors.array_monitor import ArrayMonitor

    mock_client = MagicMock()
    mock_client.get_array_status = AsyncMock(return_value={
        "state": "STARTED",
        "disks": [{"name": "disk1", "temp": 35, "status": "DISK_DSBL"}],
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
    assert "Problem" in alerts[0]["title"]


@pytest.mark.asyncio
async def test_array_monitor_respects_mute():
    """Test array monitor doesn't alert when muted."""
    from src.unraid.monitors.array_monitor import ArrayMonitor

    mock_client = MagicMock()
    mock_client.get_array_status = AsyncMock(return_value={
        "state": "STARTED",
        "disks": [{"name": "disk1", "temp": 55, "status": "DISK_OK"}],
        "parities": [],
        "caches": [],
        "capacity": {"kilobytes": {"used": "1000", "total": "10000", "free": "9000"}},
    })

    mock_config = MagicMock()
    mock_config.disk_temp_threshold = 50
    mock_config.array_usage_threshold = 85

    mock_mute = MagicMock()
    mock_mute.is_array_muted.return_value = True  # MUTED

    alerts = []
    async def capture_alert(**kwargs):
        alerts.append(kwargs)

    monitor = ArrayMonitor(mock_client, mock_config, capture_alert, mock_mute)
    await monitor.check_once()

    assert len(alerts) == 0  # No alerts when muted


@pytest.mark.asyncio
async def test_array_monitor_tracks_alerted_disks():
    """Test that array monitor doesn't re-alert for the same disk."""
    from src.unraid.monitors.array_monitor import ArrayMonitor

    mock_client = MagicMock()
    mock_client.get_array_status = AsyncMock(return_value={
        "state": "STARTED",
        "disks": [{"name": "disk1", "temp": 55, "status": "DISK_OK"}],
        "parities": [],
        "caches": [],
        "capacity": {"kilobytes": {"used": "1000", "total": "10000", "free": "9000"}},
    })

    mock_config = MagicMock()
    mock_config.disk_temp_threshold = 50
    mock_config.array_usage_threshold = 85

    mock_mute = MagicMock()
    mock_mute.is_array_muted.return_value = False

    alerts = []
    async def capture_alert(**kwargs):
        alerts.append(kwargs)

    monitor = ArrayMonitor(mock_client, mock_config, capture_alert, mock_mute)

    # First check should alert
    await monitor.check_once()
    assert len(alerts) == 1

    # Second check should NOT alert (same disk, same problem)
    await monitor.check_once()
    assert len(alerts) == 1  # Still only 1 alert


@pytest.mark.asyncio
async def test_array_monitor_clear_alert_state():
    """Test that clearing alert state allows re-alerting."""
    from src.unraid.monitors.array_monitor import ArrayMonitor

    mock_client = MagicMock()
    mock_client.get_array_status = AsyncMock(return_value={
        "state": "STARTED",
        "disks": [{"name": "disk1", "temp": 55, "status": "DISK_OK"}],
        "parities": [],
        "caches": [],
        "capacity": {"kilobytes": {"used": "1000", "total": "10000", "free": "9000"}},
    })

    mock_config = MagicMock()
    mock_config.disk_temp_threshold = 50
    mock_config.array_usage_threshold = 85

    mock_mute = MagicMock()
    mock_mute.is_array_muted.return_value = False

    alerts = []
    async def capture_alert(**kwargs):
        alerts.append(kwargs)

    monitor = ArrayMonitor(mock_client, mock_config, capture_alert, mock_mute)

    # First check should alert
    await monitor.check_once()
    assert len(alerts) == 1

    # Clear alert state (simulating unmute)
    monitor.clear_alert_state()

    # Second check should alert again
    await monitor.check_once()
    assert len(alerts) == 2  # Now we have 2 alerts


@pytest.mark.asyncio
async def test_array_monitor_capacity_warning():
    """Test array monitor alerts on high capacity usage."""
    from src.unraid.monitors.array_monitor import ArrayMonitor

    mock_client = MagicMock()
    mock_client.get_array_status = AsyncMock(return_value={
        "state": "STARTED",
        "disks": [],
        "parities": [],
        "caches": [],
        "capacity": {"kilobytes": {"used": "9000", "total": "10000", "free": "1000"}},
    })

    mock_config = MagicMock()
    mock_config.disk_temp_threshold = 50
    mock_config.array_usage_threshold = 85  # 90% > 85%

    mock_mute = MagicMock()
    mock_mute.is_array_muted.return_value = False

    alerts = []
    async def capture_alert(**kwargs):
        alerts.append(kwargs)

    monitor = ArrayMonitor(mock_client, mock_config, capture_alert, mock_mute)
    await monitor.check_once()

    assert len(alerts) == 1
    assert "Capacity Warning" in alerts[0]["title"]
    assert "90.0%" in alerts[0]["message"]


@pytest.mark.asyncio
async def test_array_monitor_checks_all_disk_types():
    """Test array monitor checks data disks, parity, and cache."""
    from src.unraid.monitors.array_monitor import ArrayMonitor

    mock_client = MagicMock()
    mock_client.get_array_status = AsyncMock(return_value={
        "state": "STARTED",
        "disks": [{"name": "disk1", "temp": 55, "status": "DISK_OK"}],
        "parities": [{"name": "parity1", "temp": 60, "status": "DISK_OK"}],
        "caches": [{"name": "cache1", "temp": 45, "status": "DISK_DSBL"}],
        "capacity": {"kilobytes": {"used": "1000", "total": "10000", "free": "9000"}},
    })

    mock_config = MagicMock()
    mock_config.disk_temp_threshold = 50
    mock_config.array_usage_threshold = 85

    mock_mute = MagicMock()
    mock_mute.is_array_muted.return_value = False

    alerts = []
    async def capture_alert(**kwargs):
        alerts.append(kwargs)

    monitor = ArrayMonitor(mock_client, mock_config, capture_alert, mock_mute)
    await monitor.check_once()

    # Should have 3 alerts: disk1 temp, parity1 temp, cache1 status
    assert len(alerts) == 3

    # Verify different disk types in alerts
    titles = [alert["title"] for alert in alerts]
    assert any("Data Disk" in title for title in titles)
    assert any("Parity Disk" in title for title in titles)
    assert any("Cache Disk" in title for title in titles)
