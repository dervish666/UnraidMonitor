from unittest.mock import AsyncMock, MagicMock

from src.config import AutoHealConfig
from src.services.auto_healer import AutoHealer


def _make(config=None, restart_result="✅ radarr restarted successfully"):
    controller = MagicMock()
    controller.is_protected.return_value = False
    controller.restart = AsyncMock(return_value=restart_result)
    config = config or AutoHealConfig(enabled=True, containers=["radarr"], max_restarts=3, window_minutes=60)
    alert = MagicMock()
    alert.send_autoheal_alert = AsyncMock()
    return AutoHealer(config=config, controller=controller, alert_manager=alert), controller, alert


async def test_heals_opted_in_container():
    healer, controller, alert = _make()
    assert healer.is_enabled("radarr") is True
    outcome = await healer.heal("radarr")
    assert outcome == "restarted"
    controller.restart.assert_awaited_once_with("radarr")
    alert.send_autoheal_alert.assert_awaited_once()
    assert alert.send_autoheal_alert.call_args.kwargs["gave_up"] is False
    assert alert.send_autoheal_alert.call_args.kwargs["failed"] is False


def test_not_enabled_for_unlisted_container():
    healer, _, _ = _make()
    assert healer.is_enabled("plex") is False


def test_not_enabled_for_protected_container():
    healer, controller, _ = _make()
    controller.is_protected.return_value = True
    assert healer.is_enabled("radarr") is False


def test_disabled_globally():
    healer, _, _ = _make(config=AutoHealConfig(enabled=False, containers=["radarr"], max_restarts=3, window_minutes=60))
    assert healer.is_enabled("radarr") is False


async def test_storm_guard_gives_up_after_max():
    healer, controller, alert = _make()
    for _ in range(3):
        await healer.heal("radarr")
    assert controller.restart.await_count == 3
    assert await healer.heal("radarr") == "gave_up"
    assert controller.restart.await_count == 3
    assert alert.send_autoheal_alert.call_args.kwargs["gave_up"] is True


async def test_gives_up_alert_sent_once():
    healer, controller, alert = _make()
    for _ in range(5):
        await healer.heal("radarr")
    gave_up_calls = [c for c in alert.send_autoheal_alert.call_args_list if c.kwargs["gave_up"]]
    assert len(gave_up_calls) == 1


async def test_failed_restart_sends_failure_alert():
    healer, controller, alert = _make(restart_result="❌ Failed to restart radarr. Check logs for details.")
    outcome = await healer.heal("radarr")
    assert outcome == "failed"
    kwargs = alert.send_autoheal_alert.call_args.kwargs
    assert kwargs["failed"] is True
    assert kwargs["gave_up"] is False


async def test_failed_restarts_count_toward_storm_guard():
    healer, controller, alert = _make(restart_result="❌ Failed to restart radarr. Check logs for details.")
    for _ in range(3):
        assert await healer.heal("radarr") == "failed"
    assert await healer.heal("radarr") == "gave_up"
    assert controller.restart.await_count == 3
    assert alert.send_autoheal_alert.call_args.kwargs["gave_up"] is True


async def test_window_expiry_resets_guard(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr("src.services.auto_healer.time.monotonic", lambda: clock[0])
    healer, controller, alert = _make()
    for _ in range(3):
        await healer.heal("radarr")
    assert await healer.heal("radarr") == "gave_up"
    clock[0] += 61 * 60  # advance past the 60-minute window
    assert await healer.heal("radarr") == "restarted"
    assert controller.restart.await_count == 4
