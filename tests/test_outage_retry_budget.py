"""Retry passes during a Telegram outage must not starve the Docker event task.

Round 3 review: the crash and log-error retry passes ran inline on the event
task and tried every held alert, each costing the full Telegram retry ladder,
and the pass was stamped before it ran, so a slow pass came due again after a
single event. With six log errors held, an unhealthy container was auto-healed
215s late.
"""

import asyncio
import time
from unittest.mock import MagicMock, patch

import src.monitors.docker_events as docker_events
from src.alerts.manager import AlertManager
from src.alerts.mute_manager import MuteManager
from src.alerts.rate_limiter import RateLimiter
from src.monitor_callbacks import make_log_error_handler
from tests.test_delivery_round3 import FakeBot, _die_event, _docker_monitor, _pump


def _log_alerts(tmp_path, bot):
    return make_log_error_handler(
        MuteManager(json_path=str(tmp_path / "m.json")), RateLimiter(cooldown_seconds=900),
        AlertManager(bot, chat_id=1),  # type: ignore[arg-type]
    )


async def test_log_error_retry_pass_stops_at_first_failed_send(tmp_path):
    bot = FakeBot()
    on_error = _log_alerts(tmp_path, bot)
    bot.down = True
    for i in range(6):
        await on_error(f"app{i}", "ERROR connection refused")
    assert bot.send_message.await_count == 6

    await on_error.retry_pending()
    assert bot.send_message.await_count == 7  # one failed send, not six

    bot.down = False
    for _ in range(6):
        await on_error.retry_pending()
    assert len(bot.texts) == 6  # every held alert still gets out


async def test_new_error_line_for_held_container_does_not_send_again(tmp_path):
    bot = FakeBot()
    on_error = _log_alerts(tmp_path, bot)
    bot.down = True
    await on_error("sonarr", "ERROR first")
    for _ in range(20):
        await on_error("sonarr", "ERROR newer")
    assert bot.send_message.await_count == 1

    bot.down = False
    await on_error.retry_pending()
    assert len(bot.texts) == 1 and "newer" in bot.texts[0]


async def test_crash_retry_pass_stops_at_first_failed_send(tmp_path):
    monitor, bot = _docker_monitor(tmp_path)
    bot.down = True
    await _pump(monitor, [_die_event(f"app{i}") for i in range(4)])
    sent = bot.send_message.await_count

    await monitor._retry_pending_crashes()
    assert bot.send_message.await_count == sent + 1

    bot.down = False
    for _ in range(4):
        await monitor._retry_pending_crashes()
    assert len(bot.texts) == 4


async def test_slow_retry_pass_is_not_due_again_after_one_event(tmp_path):
    monitor, _ = _docker_monitor(tmp_path)
    monitor.ignored_containers = {"x"}  # recovery events for x return at once
    clock = [1000.0]
    passes = 0

    async def slow_hook() -> None:
        nonlocal passes
        passes += 1
        clock[0] += 60  # an outage-length pass

    monitor.reconcile_hooks.append(slow_hook)
    monitor._last_reconcile = 0.0
    monitor._running = True
    for _ in range(3):
        monitor._pending_alerts.put_nowait(
            {"_alert_type": "recovery", "Actor": {"Attributes": {"name": "x"}}},
        )
    monitor._pending_alerts.put_nowait(None)
    fake_time = MagicMock(wraps=time)
    fake_time.monotonic = lambda: clock[0]
    with patch.object(docker_events, "time", fake_time):
        await asyncio.wait_for(monitor._process_alerts(), timeout=5)
    assert passes == 1


async def test_newer_line_arriving_during_a_failed_retry_is_kept(tmp_path):
    """Round 4 review: the failed retry wrote its older in-flight line back over the newer one."""
    bot = FakeBot()
    on_error = _log_alerts(tmp_path, bot)
    bot.down = True
    await on_error("sonarr", "ERROR first")

    async def send_while_new_line_arrives(**kwargs):
        await on_error("sonarr", "ERROR newest")
        raise RuntimeError("telegram unreachable")

    bot.send_message.side_effect = send_while_new_line_arrives
    await on_error.retry_pending()

    bot.send_message.side_effect = bot._send
    bot.down = False
    await on_error.retry_pending()
    assert len(bot.texts) == 1 and "newest" in bot.texts[0]
