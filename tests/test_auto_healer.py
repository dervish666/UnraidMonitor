from unittest.mock import AsyncMock, MagicMock

from src.config import AutoHealConfig
from src.services.auto_healer import AutoHealer


def _make(controller=None, config=None):
    controller = controller or MagicMock()
    controller.is_protected.return_value = False
    controller.restart = AsyncMock(return_value="ok")
    config = config or AutoHealConfig(enabled=True, containers=["radarr"], max_restarts=3, window_minutes=60)
    alert = MagicMock()
    alert.send_autoheal_alert = AsyncMock()
    return AutoHealer(config=config, controller=controller, alert_manager=alert), controller, alert


async def test_heals_opted_in_container():
    healer, controller, alert = _make()
    assert healer.is_enabled("radarr") is True
    await healer.heal("radarr")
    controller.restart.assert_awaited_once_with("radarr")
    alert.send_autoheal_alert.assert_awaited_once()
    assert alert.send_autoheal_alert.call_args.kwargs["gave_up"] is False


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
    await healer.heal("radarr")
    assert controller.restart.await_count == 3
    assert alert.send_autoheal_alert.call_args.kwargs["gave_up"] is True


async def test_gives_up_alert_sent_once():
    healer, controller, alert = _make()
    for _ in range(5):
        await healer.heal("radarr")
    gave_up_calls = [c for c in alert.send_autoheal_alert.call_args_list if c.kwargs["gave_up"]]
    assert len(gave_up_calls) == 1
