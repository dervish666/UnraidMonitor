"""Delivery truth gaps from round 2: state commits only after Telegram takes it.

Each producer here used to record its alert as sent (cooldown, give-up flag,
episode deleted, legacy file retired) before or regardless of the send result,
so an alert lost to a Telegram outage was gone for good.
"""

import asyncio
import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from src.alerts.manager import AlertManager
from src.alerts.mute_manager import MuteManager
from src.alerts.rate_limiter import RateLimiter
from src.alerts.server_mute_manager import ServerMuteManager
from src.config import AutoHealConfig, ResourceConfig, UnraidConfig
from src.monitor_callbacks import make_log_error_handler
from src.monitors.docker_events import DockerEventMonitor
from src.monitors.resource_monitor import ContainerStats, ResourceMonitor
from src.services.auto_healer import AutoHealer
from src.state import ContainerStateManager
from src.unraid.monitors.notification_monitor import UnraidNotificationMonitor


class FakeBot:
    def __init__(self) -> None:
        self.down = False
        self.texts: list[str] = []
        self.send_message = AsyncMock(side_effect=self._send)

    async def _send(self, **kwargs):
        if self.down:
            raise RuntimeError("telegram unreachable")
        self.texts.append(kwargs["text"])
        return MagicMock()


def _die_event(name: str, exit_code: str = "1") -> dict:
    return {
        "status": "die", "id": "abc123", "Type": "container", "Action": "die",
        "Actor": {"ID": "abc123", "Attributes": {"name": name, "exitCode": exit_code}},
        "time": 1790000000,
    }


def _container(name: str) -> MagicMock:
    c = MagicMock()
    c.name = name
    c.status = "exited"
    c.image.tags = ["app:latest"]
    c.attrs = {"State": {"StartedAt": "0001-01-01", "OOMKilled": False}}
    return c


async def _pump(monitor: DockerEventMonitor, events: list[dict]) -> None:
    """Feed raw events through _event_loop, then dispatch the queue as _process_alerts does."""
    monitor._client.events.return_value = iter(events)
    monitor._running = True
    monitor._loop = asyncio.get_running_loop()
    await asyncio.to_thread(monitor._event_loop)
    await asyncio.sleep(0)
    while not monitor._pending_alerts.empty():
        event = monitor._pending_alerts.get_nowait()
        if event is None:
            continue
        kind = event.get("_alert_type")
        if kind == "health":
            await monitor._handle_health_event(event)
        elif kind == "recovery":
            await monitor._handle_recovery_event(event)
        else:
            await monitor._handle_crash_event(event)


def _docker_monitor(tmp_path):
    bot = FakeBot()
    monitor = DockerEventMonitor(
        state_manager=ContainerStateManager(),
        alert_manager=AlertManager(bot, chat_id=1),
        rate_limiter=RateLimiter(cooldown_seconds=900),
        mute_manager=MuteManager(json_path=str(tmp_path / "mutes.json")),
    )
    monitor._client = MagicMock()
    monitor._client.containers.get.side_effect = _container
    return monitor, bot


async def test_crash_alert_lost_to_outage_is_retried_then_rate_limited(tmp_path):
    monitor, bot = _docker_monitor(tmp_path)
    bot.down = True
    await _pump(monitor, [_die_event("plex")])
    assert bot.texts == []

    bot.down = False
    await monitor._retry_pending_crashes()
    assert len(bot.texts) == 1 and "plex" in bot.texts[0]

    # Delivered once: the cooldown now holds and the retry queue is empty.
    await monitor._retry_pending_crashes()
    await _pump(monitor, [_die_event("plex")])
    assert len(bot.texts) == 1


async def test_crash_retry_runs_on_the_reconcile_cadence(tmp_path):
    monitor, bot = _docker_monitor(tmp_path)
    bot.down = True
    await _pump(monitor, [_die_event("plex")])
    bot.down = False

    hook = AsyncMock()
    monitor.reconcile_hooks.append(hook)
    monitor._running = True
    monitor._last_reconcile = 0.0
    task = asyncio.create_task(monitor._process_alerts())
    for _ in range(50):
        await asyncio.sleep(0.01)
        if bot.texts:
            break
    monitor._running = False
    monitor._pending_alerts.put_nowait(None)
    await task

    assert len(bot.texts) == 1
    hook.assert_awaited()


async def test_log_error_lost_to_outage_is_retried_not_cooled_down(tmp_path):
    bot = FakeBot()
    limiter = RateLimiter(cooldown_seconds=900)
    on_error = make_log_error_handler(
        MuteManager(json_path=str(tmp_path / "m.json")), limiter,
        AlertManager(bot, chat_id=1),  # type: ignore[arg-type]
    )
    bot.down = True
    await on_error("plex", "ERROR database is locked")
    assert bot.texts == []
    assert limiter.should_alert("plex")  # the failed send did not start the cooldown

    bot.down = False
    await on_error.retry_pending()
    assert len(bot.texts) == 1 and "database is locked" in bot.texts[0]

    await on_error.retry_pending()
    await on_error("plex", "ERROR again")  # inside the cooldown now
    assert len(bot.texts) == 1


async def test_auto_heal_give_up_lost_to_outage_is_resent(tmp_path):
    monitor, bot = _docker_monitor(tmp_path)
    controller = MagicMock()
    controller.is_protected.return_value = False
    controller.restart = AsyncMock(return_value="✅ plex restarted successfully")
    healer = AutoHealer(
        AutoHealConfig(enabled=True, containers=["plex"], max_restarts=1, window_minutes=60),
        controller, AlertManager(bot, chat_id=1),
    )
    monitor.set_auto_healer(healer)
    unhealthy = {"Actor": {"Attributes": {"name": "plex"}}}
    # The reconcile pass only re-checks containers state says are unhealthy.
    monitor.state_manager.get = MagicMock(  # type: ignore[method-assign]
        return_value=MagicMock(health="unhealthy"),
    )

    await monitor._handle_health_event(unhealthy)  # restart 1 of 1
    bot.down = True
    await monitor._handle_health_event(unhealthy)  # give up; the alert is lost
    assert not healer.give_up_announced("plex")

    bot.down = False
    await monitor._reconcile_unhealthy()
    assert sum("gave up" in t for t in bot.texts) == 1
    assert healer.give_up_announced("plex")

    await monitor._reconcile_unhealthy()
    assert sum("gave up" in t for t in bot.texts) == 1  # announced once


async def test_resource_recovery_is_kept_until_delivered():
    alerts = MagicMock()
    alerts.send_resource_alert = AsyncMock(return_value=True)
    alerts.send_resource_recovery = AsyncMock(side_effect=[False, True])
    config = ResourceConfig(sustained_threshold_seconds=0, default_cpu_percent=80)
    monitor = ResourceMonitor(MagicMock(), config, alerts, RateLimiter())

    def poll_at(cpu: float):
        monitor._collect_stats = AsyncMock(return_value=([  # type: ignore[method-assign]
            ContainerStats(name="plex", cpu_percent=cpu, memory_percent=10,
                           memory_bytes=1, memory_limit=10),
        ], 0))
        return monitor._poll_cycle()

    await poll_at(95)
    alerts.send_resource_alert.assert_awaited_once()
    await poll_at(10)  # recovered; the recovery send fails
    await poll_at(10)  # retried and delivered
    await poll_at(10)
    assert alerts.send_resource_recovery.await_count == 2


def _notification(nid: str, importance: str) -> dict:
    return {"id": nid, "importance": importance, "title": "SMART", "subject": "disk1",
            "description": "reallocated sectors", "timestamp": "2026-11-05T10:00:00Z"}


async def test_cpu_mute_does_not_hold_disk_notifications(tmp_path):
    mutes = ServerMuteManager(str(tmp_path / "s.json"))
    client = MagicMock()
    client.get_notifications = AsyncMock(return_value=[])
    on_alert = AsyncMock(return_value=True)
    relay = UnraidNotificationMonitor(client, UnraidConfig(), on_alert, mutes)
    await relay.check_once()  # primes on the (empty) backlog

    mutes.mute_system(timedelta(hours=24))  # the srv_mute button: system only
    client.get_notifications.return_value = [_notification("n1", "WARNING")]
    assert await relay.check_once() == 1

    mutes.mute_array(timedelta(hours=24))  # both muted now
    client.get_notifications.return_value = [
        _notification("n2", "WARNING"), _notification("n3", "ALERT"),
    ]
    assert await relay.check_once() == 1  # ALERT goes through, WARNING is held
    assert on_alert.await_count == 2

    mutes.unmute_system()
    assert await relay.check_once() == 1  # the held WARNING follows the mute


async def test_legacy_mute_file_survives_a_failed_import_save(tmp_path):
    legacy = tmp_path / "array_mutes.json"
    legacy.write_text(json.dumps({"array": (datetime.now() + timedelta(hours=3)).isoformat()}))
    mgr = ServerMuteManager(str(tmp_path / "server_mutes.json"))

    with patch("src.alerts.base_mute_manager.atomic_write_json", side_effect=OSError("disk full")):
        assert mgr.import_legacy_array_mutes(str(legacy)) is True
    assert legacy.exists()  # not retired: the mute is only in memory

    # Next start, with a working disk: imported again, saved, then retired.
    mgr = ServerMuteManager(str(tmp_path / "server_mutes.json"))
    assert mgr.import_legacy_array_mutes(str(legacy)) is True
    assert not legacy.exists()
    assert ServerMuteManager(str(tmp_path / "server_mutes.json")).is_array_muted()
