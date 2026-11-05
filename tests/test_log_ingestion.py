"""Log streams become whole lines, and a storm cannot flood the event loop."""

import asyncio
from unittest.mock import MagicMock

from src.monitors.log_watcher import LogWatcher


def _watcher(chunks, errors):
    watcher = LogWatcher(containers=["c"], error_patterns=["ERROR"], ignore_patterns=[])
    container = MagicMock()
    container.logs.return_value = iter(chunks)
    client = MagicMock()
    client.containers.get.return_value = container
    watcher._client = client
    watcher._running = True

    async def on_error(name, line):
        errors.append(line)

    watcher.on_error = on_error
    return watcher


async def test_tty_chunks_are_reassembled_into_lines():
    """A TTY container streams raw chunks: one error split across two, and
    two lines in one. Matching per chunk alerted on half a line and merged
    an INFO line into the alert."""
    errors: list[str] = []
    watcher = _watcher([b"2026-11-05 ERROR: dis", b"k full\nINFO all good\nERROR: second\n"], errors)
    await asyncio.wait_for(watcher._stream_logs("c"), timeout=5)
    assert errors == ["2026-11-05 ERROR: disk full", "ERROR: second"]


async def test_last_line_without_newline_is_not_lost():
    errors: list[str] = []
    watcher = _watcher([b"ERROR: crashed on exit"], errors)
    await asyncio.wait_for(watcher._stream_logs("c"), timeout=5)
    assert errors == ["ERROR: crashed on exit"]


async def test_huge_line_is_cut():
    errors: list[str] = []
    watcher = _watcher([b"ERROR: " + b"A" * 1_000_000 + b"\n"], errors)
    await asyncio.wait_for(watcher._stream_logs("c"), timeout=5)
    assert len(errors) == 1 and len(errors[0]) < 4100 and errors[0].endswith("[truncated]")


async def test_storm_wakes_the_loop_per_batch_not_per_line():
    lines = [f"INFO line {i}\n".encode() for i in range(20_000)]
    watcher = _watcher(lines, [])
    loop = asyncio.get_running_loop()
    real = loop.call_soon_threadsafe
    calls = {"n": 0}

    def counting(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    loop.call_soon_threadsafe = counting  # type: ignore[method-assign]
    try:
        await asyncio.wait_for(watcher._stream_logs("c"), timeout=20)
    finally:
        del loop.call_soon_threadsafe  # type: ignore[attr-defined]
    # One per line was 20,000 callbacks queued on the loop at once.
    assert calls["n"] < 2_000, calls["n"]
