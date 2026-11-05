"""A crashed monitor is loud, stops the heartbeat, and never skips the mute flush."""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

from src.background import _BackgroundTasks
from src.utils.heartbeat import heartbeat_loop


async def _crash() -> None:
    await asyncio.sleep(0)
    raise RuntimeError("poll loop exploded")


async def _forever() -> None:
    await asyncio.Event().wait()


async def _settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


async def test_crashed_monitor_is_logged_by_name(caplog):
    bg = _BackgroundTasks()
    with caplog.at_level(logging.ERROR):
        bg.add_task(asyncio.create_task(_crash(), name="unraid-array"))
        await _settle()
    assert "unraid-array died: RuntimeError: poll loop exploded" in caplog.text
    assert bg.dead_tasks == ["unraid-array"]


async def test_disabled_monitor_returning_is_not_death(caplog):
    async def disabled() -> None:
        return None

    bg = _BackgroundTasks()
    with caplog.at_level(logging.ERROR):
        bg.add_task(asyncio.create_task(disabled(), name="memory-monitor"))
        await _settle()
    assert bg.dead_tasks == [] and "memory-monitor" not in caplog.text
    assert bg.any_monitor_alive()


async def test_liveness_follows_the_last_monitor():
    """One monitor crashing is logged; the beat only stops when nothing is left."""
    bg = _BackgroundTasks()
    assert bg.any_monitor_alive()  # wizard / startup: nothing registered yet

    survivor = asyncio.create_task(_forever(), name="ups-monitor")
    bg.add_task(asyncio.create_task(_crash(), name="docker-events"))
    bg.add_task(survivor)
    bg.add_task(asyncio.create_task(_forever(), name="mute-maintenance"), monitor=False)
    await _settle()
    assert bg.any_monitor_alive()

    survivor.cancel()
    await _settle()
    assert not bg.any_monitor_alive()
    await bg.shutdown()


async def test_heartbeat_stops_while_nothing_is_alive_and_resumes(tmp_path):
    path = tmp_path / "heartbeat"
    alive = {"v": True}
    task = asyncio.create_task(heartbeat_loop(str(path), interval=0.01, alive=lambda: alive["v"]))
    await asyncio.sleep(0.03)
    assert path.exists()

    path.unlink()
    alive["v"] = False
    await asyncio.sleep(0.05)
    assert not path.exists()

    alive["v"] = True
    await asyncio.sleep(0.05)
    assert path.exists()
    task.cancel()


async def test_shutdown_flushes_mutes_after_a_dead_task_and_a_failing_stop(caplog):
    bg = _BackgroundTasks()
    bg.add_task(asyncio.create_task(_crash(), name="docker-events"))
    await _settle()

    bg.unraid_system_monitor = MagicMock(stop=AsyncMock(side_effect=RuntimeError("already gone")))
    bg.ups_monitor = MagicMock(stop=AsyncMock())
    bg.unraid_client = MagicMock(disconnect=AsyncMock(side_effect=OSError("socket closed")))
    mutes = MagicMock()
    bg.mute_managers = [mutes]

    with caplog.at_level(logging.ERROR):
        await bg.shutdown()

    bg.ups_monitor.stop.assert_awaited_once()
    mutes.flush.assert_called_once()
    assert "Failed to stop unraid system" in caplog.text
    # Death during shutdown is expected and not reported as a crash.
    assert bg.dead_tasks == ["docker-events"]
