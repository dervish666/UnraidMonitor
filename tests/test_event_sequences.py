"""Monitor state sequences fed through their real entry points.

Docker events go through ``_event_loop`` in their real wire shape, NUT through
``check_once``, memory through the kill countdown. Each test is a sequence,
because each bug here only shows on the second step.
"""

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

from src.alerts.manager import AlertManager
from src.alerts.mute_manager import MuteManager
from src.config import MemoryConfig, NutConfig
from src.monitors.docker_events import DockerEventMonitor, split_event_action
from src.monitors.log_watcher import LogWatcher
from src.monitors.memory_monitor import MemoryMonitor, MemoryState
from src.nut.client import NutUnavailable
from src.nut.monitor import UpsMonitor
from src.state import ContainerStateManager


def _health_event(name: str, status: str) -> dict:
    """What dockerd actually emits (moby daemon/health.go)."""
    return {
        "status": f"health_status: {status}",
        "id": "abc123",
        "from": "linuxserver/sonarr",
        "Type": "container",
        "Action": f"health_status: {status}",
        "Actor": {"ID": "abc123", "Attributes": {"image": "linuxserver/sonarr", "name": name}},
        "scope": "local",
        "time": 1790000000,
    }


def _container(name: str, health: str) -> MagicMock:
    c = MagicMock()
    c.name = name
    c.status = "running"
    c.image.tags = ["linuxserver/sonarr:latest"]
    c.attrs = {"State": {"Health": {"Status": health}, "StartedAt": "0001-01-01"}}
    return c


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


def _docker_monitor(tmp_path, events, health="unhealthy"):
    bot = FakeBot()
    mutes = MuteManager(json_path=str(tmp_path / "mutes.json"))
    monitor = DockerEventMonitor(
        state_manager=ContainerStateManager(),
        alert_manager=AlertManager(bot, chat_id=1),
        mute_manager=mutes,
    )
    client = MagicMock()
    client.events.return_value = iter(events)
    client.containers.get.side_effect = lambda name: _container(name, health)
    monitor._client = client
    return monitor, bot, mutes


async def _pump(monitor: DockerEventMonitor) -> None:
    """Run the blocking event loop in a thread, then drain the alert queue."""
    monitor._running = True
    monitor._loop = asyncio.get_running_loop()
    await asyncio.to_thread(monitor._event_loop)
    await asyncio.sleep(0)  # let call_soon_threadsafe puts land
    while not monitor._pending_alerts.empty():
        event = monitor._pending_alerts.get_nowait()
        if event and event.get("_alert_type") == "health":
            await monitor._handle_health_event(event)


def test_split_event_action():
    assert split_event_action("health_status: unhealthy") == ("health_status", "unhealthy")
    assert split_event_action("die") == ("die", "")
    assert split_event_action("exec_start: sh -c x") == ("exec_start", "sh -c x")


async def test_real_unhealthy_event_alerts(tmp_path):
    """The raw Action is "health_status: unhealthy"; exact matching dropped it."""
    monitor, bot, _ = _docker_monitor(tmp_path, [_health_event("sonarr", "unhealthy")])

    await _pump(monitor)

    assert len(bot.texts) == 1
    assert "UNHEALTHY" in bot.texts[0] and "sonarr" in bot.texts[0]
    # State updated too, so /status shows it.
    assert monitor.state_manager.get("sonarr").health == "unhealthy"


async def test_unhealthy_healthy_unhealthy_alerts_twice(tmp_path):
    events = [
        _health_event("sonarr", "unhealthy"),
        _health_event("sonarr", "healthy"),
        _health_event("sonarr", "unhealthy"),
    ]
    monitor, bot, _ = _docker_monitor(tmp_path, events)
    monitor._running = True
    monitor._loop = asyncio.get_running_loop()

    # Process in order, as the alert task would.
    for event in events:
        monitor._client.events.return_value = iter([event])
        await _pump(monitor)

    assert sum("UNHEALTHY" in t for t in bot.texts) == 2


async def test_real_unhealthy_event_reaches_auto_heal(tmp_path):
    monitor, bot, _ = _docker_monitor(tmp_path, [_health_event("sonarr", "unhealthy")])
    healer = MagicMock()
    healer.is_enabled.return_value = True
    healer.heal = AsyncMock(return_value="restarted")
    monitor.set_auto_healer(healer)

    await _pump(monitor)

    healer.heal.assert_awaited_once_with("sonarr")


async def test_unhealthy_during_mute_alerts_after_mute_if_still_unhealthy(tmp_path):
    monitor, bot, mutes = _docker_monitor(tmp_path, [_health_event("sonarr", "unhealthy")])
    mutes.add_mute("sonarr", timedelta(hours=1))

    await _pump(monitor)
    await monitor._reconcile_unhealthy()
    assert bot.texts == []

    mutes.remove_mute("sonarr")
    await monitor._reconcile_unhealthy()
    assert len(bot.texts) == 1 and "UNHEALTHY" in bot.texts[0]

    # Announced once, not on every reconcile pass.
    await monitor._reconcile_unhealthy()
    assert len(bot.texts) == 1


async def test_unhealthy_during_mute_then_healthy_stays_quiet(tmp_path):
    monitor, bot, mutes = _docker_monitor(tmp_path, [_health_event("sonarr", "unhealthy")])
    mutes.add_mute("sonarr", timedelta(hours=1))
    await _pump(monitor)

    monitor._client.events.return_value = iter([_health_event("sonarr", "healthy")])
    monitor._client.containers.get.side_effect = lambda name: _container(name, "healthy")
    await _pump(monitor)
    mutes.remove_mute("sonarr")
    await monitor._reconcile_unhealthy()

    assert bot.texts == []


async def test_unhealthy_alert_lost_to_outage_is_resent(tmp_path):
    monitor, bot, _ = _docker_monitor(tmp_path, [_health_event("sonarr", "unhealthy")])
    bot.down = True

    await _pump(monitor)
    assert bot.texts == []

    bot.down = False
    await monitor._reconcile_unhealthy()
    assert len(bot.texts) == 1


# -- NUT ----------------------------------------------------------------------


class _Mutes:
    def __init__(self) -> None:
        self.muted = False

    def is_ups_muted(self) -> bool:
        return self.muted


class _Client:
    target = "10.0.0.2:3493"

    def __init__(self) -> None:
        self.reply: object = {"ups.status": "OL"}

    async def fetch(self, ups_name=None):
        if isinstance(self.reply, Exception):
            raise self.reply
        return "ups", self.reply


async def test_nut_outage_that_starts_under_a_mute_is_announced_after_it():
    titles: list[str] = []

    async def on_alert(title, message, alert_type):
        titles.append(title)
        return True

    client, mutes = _Client(), _Mutes()
    monitor = UpsMonitor(client, NutConfig(enabled=True, host="x"), on_alert, mutes)
    await monitor.check_once()

    mutes.muted = True
    client.reply = NutUnavailable("connection refused")
    for _ in range(4):
        await monitor.check_once()
    assert titles == []
    assert monitor.is_available is False  # /health still tells the truth

    mutes.muted = False
    await monitor.check_once()
    assert titles == ["UPS Monitoring Unavailable"]
    await monitor.check_once()
    assert titles == ["UPS Monitoring Unavailable"]

    client.reply = {"ups.status": "OL"}
    await monitor.check_once()
    assert titles == ["UPS Monitoring Unavailable", "UPS Monitoring Restored"]


async def test_nut_on_battery_alert_lost_to_outage_is_retried():
    results = iter([False, True])
    titles: list[str] = []

    async def on_alert(title, message, alert_type):
        titles.append(title)
        return next(results, True)

    client = _Client()
    monitor = UpsMonitor(client, NutConfig(enabled=True, host="x"), on_alert, _Mutes())
    await monitor.check_once()
    client.reply = {"ups.status": "OB DISCHRG", "battery.charge": "90"}
    await monitor.check_once()
    await monitor.check_once()
    await monitor.check_once()

    assert titles.count("UPS On Battery") == 2


# -- Memory -------------------------------------------------------------------


async def test_failed_auto_stop_is_reported_and_moves_to_next_target():
    config = MemoryConfig(
        enabled=True, warning_threshold=90, critical_threshold=95, safe_threshold=80,
        kill_delay_seconds=0, stabilization_wait=0,
        priority_containers=[], killable_containers=["stubborn", "next"],
    )
    stubborn = MagicMock()
    stubborn.name = "stubborn"
    stubborn.stop.side_effect = RuntimeError("cannot stop: device busy")
    nxt = MagicMock()
    nxt.name = "next"
    docker_client = MagicMock()
    docker_client.containers.list.return_value = [stubborn, nxt]
    docker_client.containers.get.side_effect = lambda n: {"stubborn": stubborn, "next": nxt}[n]
    alerts: list[tuple[str, str]] = []

    async def on_alert(title, message, alert_type, killable, restartable):
        alerts.append((title, message))

    monitor = MemoryMonitor(docker_client, config, on_alert, AsyncMock(), stats_timeout=0.1)
    monitor.get_memory_percent = MagicMock(return_value=97.0)  # type: ignore[method-assign]
    monitor._system_memory = MagicMock(return_value=(97.0, 1024))  # type: ignore[method-assign]
    monitor._state = MemoryState.CRITICAL
    await monitor._handle_critical(97.0)
    assert monitor.get_pending_kill() == "stubborn"

    assert await monitor._execute_kill_countdown() is False
    titles = [t for t, _ in alerts]
    assert "Could Not Stop Container" in titles
    assert "Container Stopped" not in titles
    assert monitor.get_pending_kill() == "next"

    assert await monitor._execute_kill_countdown() is True
    assert [t for t, _ in alerts][-1] == "Container Stopped"
    nxt.stop.assert_called_once()


# -- Log watcher ----------------------------------------------------------------


async def test_log_refollow_resumes_from_where_the_stream_ended():
    """tail=0 on re-follow skipped the restart-window errors."""
    container = MagicMock()
    container.logs.side_effect = [iter([b"first\n"]), iter([])]
    client = MagicMock()
    client.containers.get.return_value = container
    watcher = LogWatcher(["app"], ["error"], [])
    watcher._client = client
    watcher._running = True

    await watcher._stream_logs("app")
    await watcher._stream_logs("app")
    watcher._running = False

    first, second = container.logs.call_args_list
    assert first.kwargs.get("tail") == 0
    assert "since" in second.kwargs and second.kwargs["since"] > 0
