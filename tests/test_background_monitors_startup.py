"""Regression test: monitors report is_running before the startup card snapshots them.

The startup card is built immediately after `_start_background_monitors` returns,
with no further yield. Each monitor sets `_running=True` synchronously at the top
of its `start()` coroutine, but `create_task` only schedules that coroutine — it
doesn't run it. So `_start_background_monitors` must yield once before returning,
or freshly-created monitors render red on the card despite running fine.
"""

import asyncio
from unittest.mock import MagicMock

import src.startup as startup_mod
from src.background import _BackgroundTasks


class _FakeMonitor:
    """Mimics a real monitor: flips _running before its first await, then loops."""

    def __init__(self) -> None:
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        self._running = True
        while self._running:
            await asyncio.sleep(3600)


class _FakeClient:
    def __init__(self, connected: bool = True) -> None:
        self._connected = connected

    async def connect(self) -> None:
        return None

    @property
    def is_connected(self) -> bool:
        return self._connected


class _FakeUnraid:
    def __init__(self, client, system_monitor, array_monitor, ups_monitor=None) -> None:
        self.client = client
        self.system_monitor = system_monitor
        self.array_monitor = array_monitor
        self.ups_monitor = ups_monitor
        self.notification_monitor = None


async def _cancel(bg: _BackgroundTasks) -> None:
    for task in bg._tasks:
        task.cancel()
    await asyncio.gather(*bg._tasks, return_exceptions=True)


async def test_unraid_monitors_running_when_card_is_built():
    """The reported bug: Unraid system/array created after connect() showed red."""
    bg = _BackgroundTasks()
    bg.monitor = _FakeMonitor()
    bg.log_watcher = _FakeMonitor()
    uc = _FakeUnraid(_FakeClient(), _FakeMonitor(), _FakeMonitor())

    await startup_mod._start_background_monitors(bg, None, uc, MagicMock(), MagicMock())
    try:
        assert bg.monitor.is_running
        assert bg.log_watcher.is_running
        assert uc.system_monitor.is_running
        assert uc.array_monitor.is_running
    finally:
        await _cancel(bg)


async def test_core_monitors_running_when_unraid_not_configured():
    """The latent case: with no Unraid client there's no connect() await at all."""
    bg = _BackgroundTasks()
    bg.monitor = _FakeMonitor()
    bg.log_watcher = _FakeMonitor()
    uc = _FakeUnraid(None, None, None)

    await startup_mod._start_background_monitors(bg, None, uc, MagicMock(), MagicMock())
    try:
        assert bg.monitor.is_running
        assert bg.log_watcher.is_running
    finally:
        await _cancel(bg)


async def test_failed_unraid_connect_warns_user_and_still_starts_monitors():
    """connect() returns quietly on failure; the user must hear about it, and
    monitors still start so they recover when Unraid comes up (audit 2026-09-22)."""
    from unittest.mock import AsyncMock

    bg = _BackgroundTasks()
    bg.monitor = _FakeMonitor()
    bg.log_watcher = _FakeMonitor()
    uc = _FakeUnraid(_FakeClient(connected=False), _FakeMonitor(), _FakeMonitor())
    chat_ids = MagicMock()
    chat_ids.get_all_chat_ids.return_value = [42]
    bot = MagicMock()
    bot.send_message = AsyncMock()

    await startup_mod._start_background_monitors(bg, None, uc, chat_ids, bot)
    try:
        assert uc.system_monitor.is_running
        bot.send_message.assert_awaited_once()
        assert "UNRAID_API_KEY" in bot.send_message.call_args.kwargs["text"]
    finally:
        await _cancel(bg)
