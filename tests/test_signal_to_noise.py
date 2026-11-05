"""One alert per episode, a recovery when it ends, and no alert for what the user did.

Every test is a sequence through the real entry point (Docker events through
``_event_loop``, Unraid through ``check_once``, NUT through ``check_once``,
resource polls through ``_poll_cycle``, memory through ``_check_memory``).
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

from src.alerts.manager import AlertManager
from src.alerts.mute_manager import MuteManager
from src.alerts.rate_limiter import RateLimiter
from src.config import MemoryConfig, NutConfig, ResourceConfig, UnraidConfig
from src.constants import ALERT_REMINDER_SECONDS
from src.monitors.docker_events import DockerEventMonitor
from src.monitors.memory_monitor import MemoryMonitor, MemoryState
from src.monitors.resource_monitor import ContainerStats, ResourceMonitor
from src.nut.monitor import UpsMonitor
from src.state import ContainerStateManager
from src.unraid.monitors.system_monitor import UnraidSystemMonitor


class FakeBot:
    def __init__(self) -> None:
        self.texts: list[str] = []
        self.send_message = AsyncMock(side_effect=self._send)

    async def _send(self, **kwargs):
        self.texts.append(kwargs["text"])
        return MagicMock()


# -- Docker: stop vs crash ------------------------------------------------------


def _event(name: str, action: str, exit_code: str | None = None) -> dict:
    attrs = {"image": "app:latest", "name": name}
    if exit_code is not None:
        attrs["exitCode"] = exit_code
    return {"Type": "container", "Action": action, "status": action, "id": "abc",
            "Actor": {"ID": "abc", "Attributes": attrs}, "time": 1790000000}


def _docker_monitor(tmp_path, events, oom_killed=False):
    bot = FakeBot()
    monitor = DockerEventMonitor(
        state_manager=ContainerStateManager(),
        alert_manager=AlertManager(bot, chat_id=1),
        mute_manager=MuteManager(json_path=str(tmp_path / "mutes.json")),
    )
    container = MagicMock()
    container.name = "plex"
    container.status = "exited"
    container.image.tags = ["app:latest"]
    container.attrs = {"State": {"OOMKilled": oom_killed, "StartedAt": "0001-01-01"}}
    client = MagicMock()
    client.events.return_value = iter(events)
    client.containers.get.return_value = container
    monitor._client = client
    return monitor, bot


async def _pump(monitor: DockerEventMonitor) -> None:
    monitor._running = True
    monitor._loop = asyncio.get_running_loop()
    await asyncio.to_thread(monitor._event_loop)
    await asyncio.sleep(0)
    while not monitor._pending_alerts.empty():
        event = monitor._pending_alerts.get_nowait()
        if event and event.get("_alert_type") not in ("recovery", "health"):
            await monitor._handle_crash_event(event)


async def test_docker_stop_is_not_a_crash(tmp_path):
    """`docker stop` emits kill (SIGTERM), die 143, stop. The user asked for it."""
    monitor, bot = _docker_monitor(tmp_path, [
        _event("plex", "kill"), _event("plex", "die", "143"), _event("plex", "stop"),
    ])
    await _pump(monitor)
    assert bot.texts == []


async def test_crash_without_a_stop_request_still_alerts(tmp_path):
    monitor, bot = _docker_monitor(tmp_path, [_event("plex", "die", "1")])
    await _pump(monitor)
    assert len(bot.texts) == 1 and "CRASHED" in bot.texts[0].upper()


async def test_kill_long_ago_does_not_hide_a_later_crash(tmp_path):
    monitor, bot = _docker_monitor(tmp_path, [_event("plex", "die", "1")])
    monitor._stopped_at["plex"] = time.monotonic() - 3600
    await _pump(monitor)
    assert len(bot.texts) == 1


async def test_oom_event_labels_the_crash_even_after_a_kill(tmp_path):
    """The kernel's OOM kill is a crash, not a stop, and says so."""
    monitor, bot = _docker_monitor(tmp_path, [
        _event("plex", "oom"), _event("plex", "kill"), _event("plex", "die", "137"),
    ])
    await _pump(monitor)
    assert len(bot.texts) == 1
    assert "OOM killed" in bot.texts[0]


async def test_state_oomkilled_labels_the_crash(tmp_path):
    monitor, bot = _docker_monitor(tmp_path, [_event("plex", "die", "137")], oom_killed=True)
    await _pump(monitor)
    assert "OOM killed" in bot.texts[0]


async def test_exit_137_without_oom_says_sigkill(tmp_path):
    monitor, bot = _docker_monitor(tmp_path, [_event("plex", "die", "137")])
    await _pump(monitor)
    assert "SIGKILL" in bot.texts[0] and "OOM" not in bot.texts[0]


# -- Unraid system: one alert per episode ----------------------------------------


class _ServerMutes:
    def is_server_muted(self) -> bool:
        return False


def _system_monitor(memory_alerts=True):
    titles: list[str] = []

    async def on_alert(title, message, alert_type):
        titles.append(title)
        return True

    client = MagicMock()
    client.get_system_metrics = AsyncMock()
    monitor = UnraidSystemMonitor(
        client, UnraidConfig(), on_alert, _ServerMutes(), memory_alerts=memory_alerts,
    )
    return monitor, client, titles


def _metrics(cpu=10.0, mem=40.0):
    return {"cpu_percent": cpu, "memory_percent": mem, "memory_used": 0, "memory_total": 0,
            "cpu_temperature": None}


async def test_sustained_high_cpu_alerts_once_then_reminds_then_recovers():
    monitor, client, titles = _system_monitor()
    client.get_system_metrics.return_value = _metrics(cpu=99)
    for _ in range(20):  # ten minutes of polls
        await monitor.check_once()
    assert titles == ["High CPU Usage"]

    # Six hours later, one reminder.
    monitor._episodes["cpu_usage"] -= ALERT_REMINDER_SECONDS + 1
    await monitor.check_once()
    await monitor.check_once()
    assert titles == ["High CPU Usage", "High CPU Usage"]

    client.get_system_metrics.return_value = _metrics(cpu=5)
    await monitor.check_once()
    await monitor.check_once()
    assert titles[-1] == "CPU Load Normal" and titles.count("CPU Load Normal") == 1


async def test_cpu_wobbling_just_under_the_threshold_does_not_flap():
    """Recovery needs a real drop (10% under), not one poll under the line."""
    monitor, client, titles = _system_monitor()
    threshold = UnraidConfig().cpu_usage_threshold
    for cpu in [threshold + 5, threshold + 5, threshold - 1, threshold + 5, threshold + 5,
                threshold - 1, threshold + 5, threshold + 5]:
        client.get_system_metrics.return_value = _metrics(cpu=cpu)
        await monitor.check_once()
    assert titles == ["High CPU Usage"]


async def test_no_recovery_message_without_an_alert_first():
    monitor, client, titles = _system_monitor()
    client.get_system_metrics.return_value = _metrics(cpu=5)
    await monitor.check_once()
    assert titles == []


async def test_lost_alert_is_retried_and_does_not_open_the_episode():
    sent = iter([False, True])
    titles: list[str] = []

    async def on_alert(title, message, alert_type):
        titles.append(title)
        return next(sent, True)

    client = MagicMock()
    client.get_system_metrics = AsyncMock(return_value=_metrics(mem=99))
    monitor = UnraidSystemMonitor(client, UnraidConfig(), on_alert, _ServerMutes())
    await monitor.check_once()
    await monitor.check_once()
    await monitor.check_once()
    assert titles == ["Memory Critical", "Memory Critical"]


async def test_system_memory_alert_steps_aside_for_memory_management():
    """MemoryMonitor's 90% Warning and this 90% "Critical" told one story twice."""
    monitor, client, titles = _system_monitor(memory_alerts=False)
    client.get_system_metrics.return_value = _metrics(mem=99)
    await monitor.check_once()
    client.get_system_metrics.return_value = _metrics(mem=10)
    await monitor.check_once()
    assert titles == []


# -- NUT load ---------------------------------------------------------------------


class _UpsMutes:
    def is_ups_muted(self) -> bool:
        return False


class _NutClient:
    target = "10.0.0.2:3493"

    def __init__(self) -> None:
        self.reply: dict = {"ups.status": "OL", "ups.load": "20"}

    async def fetch(self, ups_name=None):
        return "ups", self.reply


async def test_ups_load_alerts_once_then_reminds_then_recovers():
    titles: list[str] = []

    async def on_alert(title, message, alert_type):
        titles.append(title)
        return True

    client = _NutClient()
    monitor = UpsMonitor(client, NutConfig(enabled=True, host="x"), on_alert, _UpsMutes())
    await monitor.check_once()
    client.reply = {"ups.status": "OL", "ups.load": "95"}
    for _ in range(30):
        await monitor.check_once()
    assert titles == ["UPS Load High"]

    monitor._load_alerted_at -= ALERT_REMINDER_SECONDS + 1  # type: ignore[operator]
    await monitor.check_once()
    assert titles == ["UPS Load High", "UPS Load High"]

    client.reply = {"ups.status": "OL", "ups.load": "20"}
    await monitor.check_once()
    await monitor.check_once()
    assert titles[-1] == "UPS Load Normal" and titles.count("UPS Load Normal") == 1


# -- Container resources -------------------------------------------------------------


async def test_container_resource_episode_alerts_once_then_recovers():
    alerts = MagicMock()
    alerts.send_resource_alert = AsyncMock(return_value=True)
    alerts.send_resource_recovery = AsyncMock(return_value=True)
    config = ResourceConfig(enabled=True, sustained_threshold_seconds=0)
    monitor = ResourceMonitor(MagicMock(), config, alerts, RateLimiter())

    def poll(cpu: float) -> tuple[list[ContainerStats], int]:
        return [ContainerStats(name="plex", cpu_percent=cpu, memory_percent=10.0,
                               memory_bytes=1, memory_limit=10)], 0

    with patch.object(monitor, "_collect_stats", new=AsyncMock()) as collect:
        collect.return_value = poll(config.default_cpu_percent + 10)
        for _ in range(10):
            await monitor._poll_cycle()
        assert alerts.send_resource_alert.await_count == 1

        # Dipping just under the line holds the episode open.
        collect.return_value = poll(config.default_cpu_percent - 1)
        await monitor._poll_cycle()
        collect.return_value = poll(config.default_cpu_percent + 10)
        await monitor._poll_cycle()
        assert alerts.send_resource_alert.await_count == 1
        alerts.send_resource_recovery.assert_not_awaited()

        collect.return_value = poll(1.0)
        await monitor._poll_cycle()
        await monitor._poll_cycle()
        alerts.send_resource_recovery.assert_awaited_once()


async def test_resource_recovery_text_reaches_telegram():
    bot = FakeBot()
    assert await AlertManager(bot, chat_id=1).send_resource_recovery(
        container_name="plex", metric="cpu", current_value=12.0, threshold=80,
    )
    assert "plex" in bot.texts[0] and "back to normal" in bot.texts[0]


# -- Memory monitor hysteresis ----------------------------------------------------------


def _memory_monitor(readings):
    alerts: list[str] = []

    async def on_alert(title, message, alert_type, killable=None, restartable=None):
        alerts.append(title)

    config = MemoryConfig(
        enabled=True, warning_threshold=90, critical_threshold=95, safe_threshold=80,
        kill_delay_seconds=0, stabilization_wait=0,
        priority_containers=[], killable_containers=[],
    )
    monitor = MemoryMonitor(MagicMock(), config, on_alert, AsyncMock(), stats_timeout=0.1)
    values = iter(readings)
    monitor.get_memory_percent = MagicMock(side_effect=lambda: next(values))  # type: ignore[method-assign]
    monitor._memory_snapshot = AsyncMock(return_value=[])  # type: ignore[method-assign]
    return monitor, alerts


async def test_memory_wobbling_across_warning_line_warns_once():
    readings = [91, 89, 91, 89, 91, 85, 91]
    monitor, alerts = _memory_monitor(readings)
    for _ in readings:
        await monitor._check_memory()
    assert len(alerts) == 1
    assert monitor._state == MemoryState.WARNING


async def test_memory_below_safe_rearms_the_warning():
    readings = [91, 79, 91]
    monitor, alerts = _memory_monitor(readings)
    for _ in readings:
        await monitor._check_memory()
    assert len(alerts) == 2


async def test_memory_wobbling_across_critical_line_stays_critical():
    readings = [96, 94, 96, 94]
    monitor, alerts = _memory_monitor(readings)
    for _ in readings:
        await monitor._check_memory()
    assert monitor._state == MemoryState.CRITICAL
    critical = [a for a in alerts if "ritical" in a]
    assert len(critical) == 1

    # Two points under the line leaves CRITICAL for WARNING, without a new alert.
    monitor.get_memory_percent = MagicMock(return_value=92)  # type: ignore[method-assign]
    await monitor._check_memory()
    assert monitor._state == MemoryState.WARNING
