"""Regression tests for how MemoryMonitor is wired at startup.

The object is now built even when ``memory_management.enabled`` is false, so the
Stop/Restart buttons on Unraid memory alerts have something to act on. The
pressure loop -- which can kill containers on its own -- must stay strictly gated
on the flag, and ``bg.memory_monitor`` is the single signal for both starting the
loop and reporting the feature as on.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.background import _BackgroundTasks
from src.bot.health_command import build_status_lines
from src.monitors.memory_monitor import MemoryMonitor
import src.startup as startup_mod


def _memory_monitor() -> MemoryMonitor:
    return MemoryMonitor(
        docker_client=MagicMock(),
        config=MagicMock(),
        on_alert=MagicMock(),
        on_ask_restart=MagicMock(),
    )


class _FakeMonitor:
    def __init__(self) -> None:
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        self._running = True
        while self._running:
            await asyncio.sleep(3600)


class _FakeUnraid:
    client = None
    system_monitor = None
    array_monitor = None
    ups_monitor = None


async def _cancel(bg: _BackgroundTasks) -> None:
    for task in bg._tasks:
        task.cancel()
    await asyncio.gather(*bg._tasks, return_exceptions=True)


def test_constructing_the_monitor_does_not_start_it():
    """Construction must be inert -- the kill loop only runs after start()."""
    monitor = _memory_monitor()

    assert monitor.is_running is False


def test_monitor_is_built_even_when_the_feature_is_disabled():
    """The regression that mattered: no object here means no mem_kill handler."""
    memory_config = MagicMock()
    memory_config.enabled = False

    monitor = startup_mod.build_memory_monitor(
        memory_config, MagicMock(), MagicMock(), MagicMock()
    )

    assert isinstance(monitor, MemoryMonitor)
    assert monitor.is_running is False


def test_built_monitor_is_registered_for_kill_callbacks_when_disabled():
    """End-to-end for the original bug: built while disabled -> buttons work."""
    from src.bot.telegram_bot import _register_memory_commands, create_dispatcher

    memory_config = MagicMock()
    memory_config.enabled = False
    monitor = startup_mod.build_memory_monitor(
        memory_config, MagicMock(), MagicMock(), MagicMock()
    )

    dp = create_dispatcher(allowed_users=[1])
    _register_memory_commands(dp, monitor, [], memory_config)

    event = SimpleNamespace(data="mem_kill:plex")
    assert any(
        handler.filters and all(bool(f.callback(event)) for f in handler.filters)
        for handler in dp.callback_query.handlers
    )


async def test_no_task_is_created_when_bg_slot_is_empty():
    """memory_management disabled -> bg.memory_monitor is None -> no kill loop."""
    bg = _BackgroundTasks()
    bg.monitor = _FakeMonitor()
    bg.log_watcher = _FakeMonitor()
    bg.memory_monitor = None

    await startup_mod._start_background_monitors(
        bg, None, _FakeUnraid(), MagicMock(), MagicMock()
    )
    try:
        # Only the docker-event monitor and the log watcher.
        assert len(bg._tasks) == 2
    finally:
        await _cancel(bg)


async def test_task_is_created_when_bg_slot_is_filled():
    bg = _BackgroundTasks()
    bg.monitor = _FakeMonitor()
    bg.log_watcher = _FakeMonitor()
    bg.memory_monitor = _FakeMonitor()

    await startup_mod._start_background_monitors(
        bg, None, _FakeUnraid(), MagicMock(), MagicMock()
    )
    try:
        assert bg.memory_monitor.is_running is True
    finally:
        await _cancel(bg)


def test_status_card_reports_memory_disabled_when_the_loop_is_off():
    """Passing the always-built object here would wrongly advertise the feature."""
    lines = build_status_lines(memory_monitor=None)

    memory_line = next(line for line in lines if "Memory:" in line)
    assert "Disabled" in memory_line


def test_status_card_reports_memory_running_when_the_loop_is_on():
    running = _FakeMonitor()
    running._running = True

    lines = build_status_lines(memory_monitor=running)

    memory_line = next(line for line in lines if "Memory:" in line)
    assert "Running" in memory_line
