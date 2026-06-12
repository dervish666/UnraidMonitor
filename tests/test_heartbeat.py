"""Tests for the liveness heartbeat loop."""

import asyncio
import os

import pytest

from src.utils.heartbeat import heartbeat_loop


@pytest.mark.asyncio
async def test_heartbeat_writes_file_immediately(tmp_path):
    path = str(tmp_path / "heartbeat")
    task = asyncio.create_task(heartbeat_loop(path=path, interval=60))
    await asyncio.sleep(0)  # let the first iteration run

    assert os.path.exists(path)
    with open(path) as f:
        assert f.read() == str(os.getpid())

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_heartbeat_survives_unwritable_path():
    """A bad path must log and keep looping, not crash the task."""
    task = asyncio.create_task(
        heartbeat_loop(path="/nonexistent-dir/heartbeat", interval=0.01)
    )
    await asyncio.sleep(0.05)  # several iterations, all failing writes

    assert not task.done()  # still alive despite OSError every beat

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_heartbeat_refreshes_mtime(tmp_path):
    path = str(tmp_path / "heartbeat")
    task = asyncio.create_task(heartbeat_loop(path=path, interval=0.01))
    await asyncio.sleep(0)
    first = os.stat(path).st_mtime_ns
    await asyncio.sleep(0.05)
    second = os.stat(path).st_mtime_ns

    assert second > first

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
