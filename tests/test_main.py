"""Tests for main.py AlertManagerProxy and _BackgroundTasks."""

import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch


@pytest.mark.asyncio
async def test_proxy_queues_alerts_when_no_chat_id():
    """Alerts are queued when no chat ID is registered."""
    from src.alert_proxy import AlertManagerProxy
    from src.alerts.manager import ChatIdStore

    store = ChatIdStore()
    proxy = AlertManagerProxy(bot=MagicMock(), chat_id_store=store)

    await proxy.send_crash_alert(container_name="plex", exit_code=1, image="img")

    assert len(proxy._queued_alerts) == 1
    assert proxy._queued_alerts[0][0] == "send_crash_alert"


@pytest.mark.asyncio
async def test_proxy_drops_alerts_when_queue_full():
    """Alerts are dropped when the queue exceeds MAX_QUEUED."""
    from src.alert_proxy import AlertManagerProxy
    from src.alerts.manager import ChatIdStore

    store = ChatIdStore()
    proxy = AlertManagerProxy(bot=MagicMock(), chat_id_store=store)
    proxy._queued_alerts = [("dummy", {})] * proxy.MAX_QUEUED

    await proxy.send_crash_alert(container_name="plex", exit_code=1, image="img")

    # Should not exceed MAX_QUEUED
    assert len(proxy._queued_alerts) == proxy.MAX_QUEUED


@pytest.mark.asyncio
async def test_proxy_sends_to_all_chat_ids():
    """Alerts are delivered to all registered chat IDs."""
    from src.alert_proxy import AlertManagerProxy
    from src.alerts.manager import ChatIdStore

    store = ChatIdStore()
    store.set_chat_id(111)
    store.set_chat_id(222)

    proxy = AlertManagerProxy(bot=MagicMock(), chat_id_store=store)
    proxy._SEND_DELAY = 0  # Speed up test

    # Mock the managers
    mock_mgr1 = MagicMock()
    mock_mgr1.send_recovery_alert = AsyncMock()
    mock_mgr2 = MagicMock()
    mock_mgr2.send_recovery_alert = AsyncMock()
    proxy._managers = {111: mock_mgr1, 222: mock_mgr2}

    await proxy.send_recovery_alert(container_name="plex")

    mock_mgr1.send_recovery_alert.assert_called_once_with(container_name="plex")
    mock_mgr2.send_recovery_alert.assert_called_once_with(container_name="plex")


@pytest.mark.asyncio
async def test_proxy_flushes_queue_on_first_send():
    """Queued alerts are flushed when the first real send happens."""
    from src.alert_proxy import AlertManagerProxy
    from src.alerts.manager import ChatIdStore

    store = ChatIdStore()
    proxy = AlertManagerProxy(bot=MagicMock(), chat_id_store=store)
    proxy._SEND_DELAY = 0

    # Queue an alert while no chat ID
    await proxy.send_crash_alert(container_name="plex", exit_code=1, image="img")
    assert len(proxy._queued_alerts) == 1

    # Now register a chat ID and send another
    store.set_chat_id(123)

    mock_mgr = MagicMock()
    mock_mgr.send_crash_alert = AsyncMock()
    mock_mgr.send_recovery_alert = AsyncMock()
    proxy._managers[123] = mock_mgr

    await proxy.send_recovery_alert(container_name="sonarr")

    # Queue should be drained
    assert len(proxy._queued_alerts) == 0
    # Both queued crash alert and new recovery alert should have been sent
    mock_mgr.send_crash_alert.assert_called_once()
    mock_mgr.send_recovery_alert.assert_called_once()


@pytest.mark.asyncio
async def test_proxy_health_alert():
    """send_health_alert delegates properly."""
    from src.alert_proxy import AlertManagerProxy
    from src.alerts.manager import ChatIdStore

    store = ChatIdStore()
    store.set_chat_id(100)

    proxy = AlertManagerProxy(bot=MagicMock(), chat_id_store=store)
    proxy._SEND_DELAY = 0

    mock_mgr = MagicMock()
    mock_mgr.send_health_alert = AsyncMock()
    proxy._managers[100] = mock_mgr

    await proxy.send_health_alert(container_name="nginx", health_status="unhealthy")

    mock_mgr.send_health_alert.assert_called_once_with(
        container_name="nginx", health_status="unhealthy"
    )


@pytest.mark.asyncio
async def test_background_tasks_shutdown_flushes_mutes():
    """Shutdown flushes all mute managers."""
    from src.background import _BackgroundTasks

    bg = _BackgroundTasks()

    mgr1 = MagicMock()
    mgr2 = MagicMock()
    bg.mute_managers = [mgr1, mgr2]

    await bg.shutdown()

    mgr1.flush.assert_called_once()
    mgr2.flush.assert_called_once()


@pytest.mark.asyncio
async def test_background_tasks_shutdown_handles_flush_errors():
    """Shutdown continues even if a mute manager flush fails."""
    from src.background import _BackgroundTasks

    bg = _BackgroundTasks()

    mgr1 = MagicMock()
    mgr1.flush.side_effect = IOError("disk full")
    mgr2 = MagicMock()
    bg.mute_managers = [mgr1, mgr2]

    await bg.shutdown()

    # Both should have been called, even though mgr1 failed
    mgr1.flush.assert_called_once()
    mgr2.flush.assert_called_once()
