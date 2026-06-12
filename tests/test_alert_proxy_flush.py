"""Tests for AlertManagerProxy queue flush retry and dedup behaviour."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.alert_proxy import AlertManagerProxy
from src.alerts.manager import ChatIdStore


def _make_proxy() -> tuple[AlertManagerProxy, ChatIdStore]:
    bot = MagicMock()
    store = ChatIdStore()
    return AlertManagerProxy(bot, store), store


@pytest.mark.asyncio
async def test_flush_failure_keeps_alert_for_one_retry():
    """An alert that fails every chat on flush is retried on the next flush."""
    proxy, store = _make_proxy()

    with patch("src.alert_proxy.AlertManager"):
        await proxy.send_crash_alert(container_name="plex", exit_code=1, image="img")
    assert proxy.queued_count == 1

    store.set_chat_id(111)

    # First flush: every send fails — entry must survive into the retry list
    with patch("src.alert_proxy.AlertManager") as MockAM:
        failing = MagicMock()
        failing.send_crash_alert = AsyncMock(side_effect=RuntimeError("boom"))
        failing.send_recovery_alert = AsyncMock()
        MockAM.return_value = failing

        await proxy.send_recovery_alert(container_name="plex")

    assert proxy.queued_count == 1
    assert proxy._retry_alerts and proxy._retry_alerts[0][0] == "send_crash_alert"

    # Second flush: send succeeds — retry entry delivered and cleared
    proxy._managers.clear()
    with patch("src.alert_proxy.AlertManager") as MockAM:
        working = MagicMock()
        working.send_crash_alert = AsyncMock()
        working.send_recovery_alert = AsyncMock()
        MockAM.return_value = working

        await proxy.send_recovery_alert(container_name="plex")

        working.send_crash_alert.assert_called_once()
    assert proxy.queued_count == 0


@pytest.mark.asyncio
async def test_flush_drops_alert_after_failed_retry():
    """An alert that fails its retry flush too is dropped, not retried forever."""
    proxy, store = _make_proxy()

    with patch("src.alert_proxy.AlertManager"):
        await proxy.send_crash_alert(container_name="plex", exit_code=1, image="img")
    store.set_chat_id(111)

    with patch("src.alert_proxy.AlertManager") as MockAM:
        failing = MagicMock()
        failing.send_crash_alert = AsyncMock(side_effect=RuntimeError("boom"))
        failing.send_recovery_alert = AsyncMock()
        MockAM.return_value = failing

        await proxy.send_recovery_alert(container_name="plex")  # first failure
        assert proxy.queued_count == 1
        await proxy.send_recovery_alert(container_name="plex")  # retry fails too

    assert proxy.queued_count == 0
    assert proxy._retry_alerts == []


@pytest.mark.asyncio
async def test_flush_counts_partial_delivery_as_delivered():
    """Reaching one of two chats is delivery — no retry queued."""
    proxy, store = _make_proxy()

    with patch("src.alert_proxy.AlertManager"):
        await proxy.send_crash_alert(container_name="plex", exit_code=1, image="img")
    store.set_chat_id(111)
    store.set_chat_id(222)

    managers: dict[int, MagicMock] = {}

    def _manager_for(bot, chat_id, **kwargs):
        m = MagicMock()
        m.send_crash_alert = (
            AsyncMock(side_effect=RuntimeError("boom")) if chat_id == 111 else AsyncMock()
        )
        m.send_recovery_alert = AsyncMock()
        managers[chat_id] = m
        return m

    with patch("src.alert_proxy.AlertManager", side_effect=_manager_for):
        await proxy.send_recovery_alert(container_name="plex")

    assert proxy.queued_count == 0
    assert proxy._retry_alerts == []
    assert managers[222].send_crash_alert.call_count == 1


@pytest.mark.asyncio
async def test_identical_consecutive_alerts_deduped_in_queue():
    """The same alert queued back-to-back is stored once."""
    proxy, _store = _make_proxy()

    with patch("src.alert_proxy.AlertManager"):
        await proxy.send_crash_alert(container_name="plex", exit_code=1, image="img")
        await proxy.send_crash_alert(container_name="plex", exit_code=1, image="img")
        # A different alert still queues
        await proxy.send_crash_alert(container_name="sonarr", exit_code=2, image="img")

    assert proxy.queued_count == 2


@pytest.mark.asyncio
async def test_retry_alerts_flush_even_when_main_queue_empty():
    """Retry entries alone must still trigger a flush on the next send."""
    proxy, store = _make_proxy()
    store.set_chat_id(111)
    proxy._retry_alerts = [("send_crash_alert", {"container_name": "plex", "exit_code": 1, "image": "i"})]

    with patch("src.alert_proxy.AlertManager") as MockAM:
        working = MagicMock()
        working.send_crash_alert = AsyncMock()
        working.send_recovery_alert = AsyncMock()
        MockAM.return_value = working

        await proxy.send_recovery_alert(container_name="plex")

        working.send_crash_alert.assert_called_once()
    assert proxy.queued_count == 0
