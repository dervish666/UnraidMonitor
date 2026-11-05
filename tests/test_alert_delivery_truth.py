"""Alert delivery state must follow what Telegram actually accepted.

Every test here drives a sequence (fail -> recover, mute -> expiry) through the
real component, because each bug it guards against was invisible to a single
step: the alert was "sent", the dedup state committed, and the user never
heard a thing.
"""

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.exceptions import TelegramNetworkError

from src.alert_proxy import AlertManagerProxy
from src.alerts.manager import AlertManager, ChatIdStore
from src.alerts.server_mute_manager import ServerMuteManager
from src.config import UnraidConfig
from src.monitors.image_update_monitor import ImageUpdateMonitor
from src.unraid.monitors.array_monitor import ArrayMonitor
from src.unraid.monitors.availability import UnraidAvailability
from src.unraid.monitors.system_monitor import UnraidSystemMonitor
from src.utils.telegram_retry import send_with_retry


class FlakyBot:
    """bot.send_message that fails while ``down`` is True."""

    def __init__(self) -> None:
        self.down = True
        self.sent: list[str] = []
        self.send_message = AsyncMock(side_effect=self._send)

    async def _send(self, **kwargs):
        if self.down:
            raise RuntimeError("telegram unreachable")
        self.sent.append(kwargs["text"])
        return MagicMock()


def _handler_over(results: list[bool]):
    calls: list[str] = []

    async def on_alert(title, message, alert_type):
        calls.append(title)
        return results[min(len(calls), len(results)) - 1]

    return on_alert, calls


# -- AlertManager / proxy ---------------------------------------------------


async def test_alert_manager_reports_a_failed_send():
    bot = FlakyBot()
    manager = AlertManager(bot, chat_id=1)

    assert await manager.send_crash_alert("plex", 1, "img") is False
    bot.down = False
    assert await manager.send_crash_alert("plex", 1, "img") is True


async def test_proxy_retries_a_queued_alert_that_failed_on_flush(tmp_path):
    """The proxy's retry used to be dead code: AlertManager swallowed every error."""
    bot = FlakyBot()
    store = ChatIdStore(str(tmp_path / "chat_ids.json"))
    proxy = AlertManagerProxy(bot, store)
    proxy._SEND_DELAY = 0

    # Before /start: queued, and that counts as accepted.
    assert await proxy.send_crash_alert("plex", 1, "img") is True
    store.set_chat_id(42)

    # Telegram down: the flush and the new alert both fail.
    assert await proxy.send_health_alert("sonarr", "unhealthy") is False
    assert proxy.queued_count == 1

    # Telegram back: the queued crash goes out with the next alert.
    bot.down = False
    assert await proxy.send_recovery_alert("radarr") is True
    assert proxy.queued_count == 0
    assert any("CONTAINER CRASHED" in t and "plex" in t for t in bot.sent)


async def test_send_with_retry_retries_network_errors():
    """TelegramNetworkError subclasses TelegramAPIError and used to be final."""
    func = AsyncMock(side_effect=[
        TelegramNetworkError(method=MagicMock(), message="connection reset"),
        TelegramNetworkError(method=MagicMock(), message="connection reset"),
        "ok",
    ])
    with patch("src.utils.telegram_retry.asyncio.sleep", new=AsyncMock()) as sleep:
        assert await send_with_retry(func, chat_id=1, text="x") == "ok"
    assert func.call_count == 3
    assert sleep.await_count == 2


async def test_send_with_retry_gives_up_on_a_persistent_outage():
    func = AsyncMock(side_effect=TelegramNetworkError(method=MagicMock(), message="down"))
    with patch("src.utils.telegram_retry.asyncio.sleep", new=AsyncMock()):
        with pytest.raises(TelegramNetworkError):
            await send_with_retry(func, max_retries=2, chat_id=1, text="x")
    assert func.call_count == 3


# -- Unraid monitors ----------------------------------------------------------


async def test_system_alert_lost_to_outage_is_retried_next_poll(tmp_path):
    client = MagicMock()
    client.get_system_metrics = AsyncMock(return_value={"cpu_percent": 10, "memory_percent": 99})
    on_alert, calls = _handler_over([False, True])
    mute = ServerMuteManager(str(tmp_path / "m.json"))
    monitor = UnraidSystemMonitor(client, UnraidConfig(), on_alert, mute)

    await monitor.check_once()
    await monitor.check_once()
    await monitor.check_once()  # delivered once: cooldown now holds

    assert calls == ["Memory Critical", "Memory Critical"]


async def test_disk_fault_lost_to_outage_is_retried_next_poll(tmp_path):
    client = MagicMock()
    client.get_array_status = AsyncMock(return_value={
        "disks": [{"name": "disk1", "status": "DISK_DSBL"}],
    })
    on_alert, calls = _handler_over([False, True])
    monitor = ArrayMonitor(client, UnraidConfig(), on_alert, ServerMuteManager(str(tmp_path / "m.json")))

    for _ in range(3):
        await monitor.check_once()

    assert calls == ["💾 Data Disk Problem", "💾 Data Disk Problem"]


async def test_mute_server_silences_the_array_through_startup_wiring(tmp_path, monkeypatch):
    """/mute-server wrote one store while ArrayMonitor read another."""
    from src.startup import _init_unraid

    monkeypatch.chdir(tmp_path)
    config = MagicMock()
    config.unraid = UnraidConfig(enabled=True, host="tower")
    config.protected_containers = []
    settings = MagicMock(unraid_api_key="key")
    uc = _init_unraid(config, settings, ChatIdStore(), MagicMock())

    uc.server_mute_manager.mute_server(timedelta(hours=1))

    on_alert, calls = _handler_over([True])
    uc.array_monitor._on_alert = on_alert
    uc.array_monitor._client = MagicMock()
    uc.array_monitor._client.get_array_status = AsyncMock(return_value={
        "disks": [{"name": "disk1", "status": "DISK_DSBL"}],
    })
    await uc.array_monitor.check_once()
    assert calls == []

    uc.server_mute_manager.unmute_server()
    await uc.array_monitor.check_once()
    assert calls == ["💾 Data Disk Problem"]


async def test_legacy_array_mute_is_imported_once(tmp_path):
    import json
    from datetime import datetime

    legacy = tmp_path / "array_mutes.json"
    expiry = datetime.now() + timedelta(hours=3)
    legacy.write_text(json.dumps({"array": expiry.isoformat()}))
    mgr = ServerMuteManager(str(tmp_path / "server_mutes.json"))

    assert mgr.import_legacy_array_mutes(str(legacy)) is True
    assert mgr.is_array_muted()
    assert not mgr.is_server_muted()
    assert not legacy.exists()
    assert (tmp_path / "array_mutes.json.migrated").exists()
    assert mgr.import_legacy_array_mutes(str(legacy)) is False


async def test_srv_mute_button_mutes_system_alerts_only(tmp_path):
    """Muting a noisy CPU alert also silenced the array and a power cut."""
    from src.bot.alert_callbacks import server_mute_callback

    mgr = ServerMuteManager(str(tmp_path / "m.json"))
    handler = server_mute_callback(mgr)
    callback = MagicMock()
    callback.data = "srv_mute:60"
    callback.answer = AsyncMock()
    callback.message.answer = AsyncMock()

    await handler(callback)

    assert mgr.is_server_muted()
    assert not mgr.is_array_muted()
    assert not mgr.is_ups_muted()


async def test_one_mute_server_expiry_sends_one_notice(tmp_path):
    from src.monitor_callbacks import _expiry_notices

    mgr = ServerMuteManager(str(tmp_path / "m.json"))
    notices = _expiry_notices(mgr, ["server", "array", "ups"])

    assert len(notices) == 1
    assert "system, array, UPS" in notices[0][0]


async def test_unraid_outage_alerts_once_and_recovery_once(tmp_path):
    mute = ServerMuteManager(str(tmp_path / "m.json"))
    on_alert, calls = _handler_over([True])
    ladder = UnraidAvailability(on_alert, mute, threshold=3)
    client = MagicMock()
    client.get_system_metrics = AsyncMock(side_effect=RuntimeError("connection refused"))
    monitor = UnraidSystemMonitor(client, UnraidConfig(), AsyncMock(return_value=True), mute, ladder)

    for _ in range(6):
        await monitor.check_once()
    assert calls == ["Unraid API Unavailable"]

    client.get_system_metrics = AsyncMock(return_value={"cpu_percent": 1, "memory_percent": 1})
    await monitor.check_once()
    await monitor.check_once()
    assert calls == ["Unraid API Unavailable", "Unraid API Restored"]


async def test_failing_array_poll_alerts_despite_healthy_system_polls(tmp_path):
    """An hour of 30s system successes interleaved with 5-minute array failures.

    One shared counter let every system success reset the array's streak, so
    the array outage never alerted and /health read fresh.
    """
    from src.bot.health_command import BROKEN, _unraid_status

    mute = ServerMuteManager(str(tmp_path / "m.json"))
    on_alert, calls = _handler_over([True])
    ladder = UnraidAvailability(on_alert, mute, threshold=3)
    client = MagicMock()
    client.is_connected = True
    client.get_system_metrics = AsyncMock(return_value={"cpu_percent": 1, "memory_percent": 1})
    client.get_array_status = AsyncMock(side_effect=RuntimeError("GraphQL error: array"))
    alerts = AsyncMock(return_value=True)
    system = UnraidSystemMonitor(client, UnraidConfig(), alerts, mute, ladder)
    array = ArrayMonitor(client, UnraidConfig(), alerts, mute, ladder)
    system._running = array._running = True

    for second in range(0, 3600, 30):
        await system.check_once()
        if second % 300 == 0:
            await array.check_once()

    assert calls == ["Unraid API Unavailable"]
    assert ladder.available is False
    item = _unraid_status(client, system, array, None)
    assert item.state == BROKEN
    assert "array polls keep failing" in (item.detail or "")
    assert any("Array: 🔴" in line and "12 failed polls" in line for line in item.health)
    assert any("System: ✅" in line for line in item.health)

    client.get_array_status = AsyncMock(return_value={"state": "STARTED", "disks": []})
    await array.check_once()
    await system.check_once()
    assert calls == ["Unraid API Unavailable", "Unraid API Restored"]
    assert ladder.available is True


async def test_unraid_outage_under_mute_is_announced_after_it(tmp_path):
    mute = ServerMuteManager(str(tmp_path / "m.json"))
    mute.mute_server(timedelta(hours=1))
    on_alert, calls = _handler_over([True])
    ladder = UnraidAvailability(on_alert, mute, threshold=2)

    for _ in range(3):
        await ladder.record_failure("system", "down")
    assert calls == []

    mute.unmute_server()
    await ladder.record_failure("system", "still down")
    assert calls == ["Unraid API Unavailable"]


# -- Image updates -----------------------------------------------------------


async def test_image_update_lost_to_outage_is_announced_next_cycle():
    """Recording the digest before the send lost the update for good."""
    container = MagicMock()
    container.name = "plex"
    container.image.tags = ["plexinc/pms:latest"]
    container.image.attrs = {"RepoDigests": ["plexinc/pms@sha256:old"]}
    docker_client = MagicMock()
    docker_client.containers.list.return_value = [container]
    docker_client.images.get_registry_data.return_value = MagicMock(id="sha256:new")
    alerts = MagicMock()
    alerts.send_update_alert = AsyncMock(side_effect=[False, True, True])
    monitor = ImageUpdateMonitor(docker_client, MagicMock(), alerts)

    await monitor.check_once()
    await monitor.check_once()
    await monitor.check_once()  # delivered: deduped from here

    assert alerts.send_update_alert.await_count == 2


async def test_registry_failures_are_warned_not_debugged(caplog):
    container = MagicMock()
    container.name = "plex"
    container.image.tags = ["plexinc/pms:latest"]
    container.image.attrs = {"RepoDigests": ["plexinc/pms@sha256:old"]}
    docker_client = MagicMock()
    docker_client.containers.list.return_value = [container]
    docker_client.images.get_registry_data.side_effect = RuntimeError("429 Too Many Requests")
    monitor = ImageUpdateMonitor(docker_client, MagicMock(), MagicMock())

    with caplog.at_level("WARNING"):
        await asyncio.to_thread(monitor._collect_updates)

    assert any("could not reach the registry for 1 of 1" in r.message for r in caplog.records)
