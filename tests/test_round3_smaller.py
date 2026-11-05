"""Smaller round-2 blockers: cut button names, log re-follow, stats health,
numeric parsing, and the restart the heartbeat used to only claim."""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.types import Message

from src.alerts.manager import ChatIdStore
from src.bot.alert_callbacks import (
    mem_kill_callback,
    mem_restart_callback,
    mem_restart_yes_callback,
)
from src.config import MemoryConfig
from src.models import ContainerInfo
from src.monitors.memory_monitor import KillResult
from src.state import ContainerStateManager
from src.utils.formatting import fit_callback

LONG = "binhex-sabnzbdvpn-with-a-very-long-descriptive-name-for-x"  # 57 chars
assert len(LONG) >= 55


def _callback(data: str) -> MagicMock:
    assert len(data.encode()) <= 64 and data.endswith("…")
    cb = MagicMock()
    cb.data = data
    cb.answer = AsyncMock()
    cb.message = MagicMock(spec=Message)
    cb.message.date = datetime.now(timezone.utc)
    cb.message.answer = AsyncMock()
    cb.message.edit_text = AsyncMock()
    return cb


def _memory_monitor() -> MagicMock:
    mm = MagicMock()
    mm.pressure_active = True
    mm.kill_container = AsyncMock(return_value=KillResult(True, LONG))
    mm.restart_container = AsyncMock(return_value=KillResult(True, LONG))
    mm.confirm_restart = AsyncMock(return_value=True)
    mm.killed_containers = [LONG]
    return mm


async def test_memory_buttons_resolve_a_cut_long_name():
    """fit_callback cut the name, and the handlers refused every cut name."""
    state = ContainerStateManager()
    state.update(ContainerInfo(LONG, "running", None, "img", None))
    state.update(ContainerInfo("plex", "running", None, "img", None))
    mm = _memory_monitor()

    await mem_kill_callback(mm, [], state)(_callback(fit_callback("mem_kill:", LONG)))
    mm.kill_container.assert_awaited_once_with(LONG)

    config = MemoryConfig.from_dict({"restart_containers": [LONG]})
    await mem_restart_callback(mm, config, [])(_callback(fit_callback("mem_restart:", LONG)))
    mm.restart_container.assert_awaited_once_with(LONG)

    await mem_restart_yes_callback(mm)(_callback(fit_callback("mem_restart_yes:", LONG)))
    mm.confirm_restart.assert_awaited_once_with(LONG)


async def test_log_refollow_bounds_the_read_with_tail():
    from src.monitors.log_watcher import LOG_QUEUE_MAX_LINES, LogWatcher

    watcher = LogWatcher(containers=["plex"], error_patterns=["error"], ignore_patterns=[],
                         on_error=AsyncMock())
    container = MagicMock()
    container.logs.return_value = iter([])
    watcher._client = MagicMock()
    watcher._client.containers.get.return_value = container
    watcher._running = True
    watcher._stream_ended_at["plex"] = 1_790_000_000.0

    await watcher._stream_logs("plex")

    kwargs = container.logs.call_args.kwargs
    assert kwargs["since"] == 1_790_000_000
    assert kwargs["tail"] == LOG_QUEUE_MAX_LINES  # docker-py's default is "all"


async def test_failed_stats_for_every_container_is_a_failed_poll():
    from src.config import ResourceConfig
    from src.monitors.resource_monitor import ResourceMonitor

    broken = MagicMock()
    broken.name = "plex"
    broken.stats.side_effect = RuntimeError("stats timed out")
    docker = MagicMock()
    docker.containers.list.return_value = [broken]
    monitor = ResourceMonitor(docker, ResourceConfig(poll_interval_seconds=10), AsyncMock(), MagicMock())

    with patch("src.monitors.resource_monitor.asyncio.sleep", side_effect=asyncio.CancelledError):
        try:
            await monitor.start()
        except asyncio.CancelledError:
            pass
    assert monitor.poll_health.consecutive_failures == 1
    assert "stats failed" in (monitor.poll_health.last_error or "")

    # Nothing running is not a failure.
    docker.containers.list.return_value = []
    await monitor._poll_cycle()


def test_malformed_numeric_chat_ids_are_skipped_not_fatal(tmp_path):
    path = tmp_path / "chat_ids.json"
    path.write_text('[123, "--1", "456", "²", "-789"]')
    store = ChatIdStore(json_path=str(path))
    assert set(store.get_all_chat_ids()) == {123, 456, -789}


async def test_malformed_numeric_tool_argument_is_a_tool_error():
    from src.services.nl_tools import NLToolExecutor

    state = ContainerStateManager()
    state.update(ContainerInfo("plex", "running", None, "img", None))
    executor = NLToolExecutor(state, MagicMock(), [])
    result = await executor.execute("get_container_logs", {"name": "plex", "lines": "--50"})
    assert "lines" in result and "integer" in result


async def test_restart_after_monitors_died_is_rate_limited(tmp_path):
    from src.restart import restart_after_monitors_died

    state = str(tmp_path / "dead.json")
    with patch("src.restart.restart_bot", new=AsyncMock()) as restart:
        assert await restart_after_monitors_died(MagicMock(), MagicMock(), state, now=1000.0)
        # The exec happened; the new process dies again ten minutes later.
        assert not await restart_after_monitors_died(MagicMock(), MagicMock(), state, now=1600.0)
        assert await restart_after_monitors_died(MagicMock(), MagicMock(), state, now=1000.0 + 3600)
    assert restart.await_count == 2


async def test_heartbeat_asks_for_a_restart_once_after_the_grace(tmp_path):
    from src.utils.heartbeat import heartbeat_loop

    on_dead = AsyncMock()
    task = asyncio.create_task(heartbeat_loop(
        str(tmp_path / "hb"), interval=0.01, alive=lambda: False, on_dead=on_dead, grace=0.03,
    ))
    await asyncio.sleep(0.15)
    task.cancel()
    on_dead.assert_awaited_once()
    assert not (tmp_path / "hb").exists()
