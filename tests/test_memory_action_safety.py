"""Memory actions never touch priority/protected containers, and stale buttons confirm."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import yaml

from src.bot.alert_callbacks import mem_kill_callback, mem_restart_callback
from src.bot.control_commands import create_ctrl_confirm_callback
from src.bot.setup_wizard import SetupWizard, create_toggle_callback
from src.config import MemoryConfig
from src.monitors.memory_monitor import MemoryMonitor, MemoryState
from src.services.container_classifier import ContainerClassification
from src.services.container_control import ContainerController
from src.state import ContainerStateManager


def _docker(*names: str) -> MagicMock:
    containers = {}
    for n in names:
        c = MagicMock()
        c.name = n
        containers[n] = c
    client = MagicMock()
    client.containers.list.return_value = list(containers.values())
    client.containers.get.side_effect = lambda n: containers[n]
    return client


def _config(killable, priority=(), **kw) -> MemoryConfig:
    return MemoryConfig(
        enabled=True, warning_threshold=90, critical_threshold=95, safe_threshold=80,
        kill_delay_seconds=0, stabilization_wait=0,
        priority_containers=list(priority), killable_containers=list(killable), **kw,
    )


def _monitor(config, docker_client, protected=None) -> MemoryMonitor:
    monitor = MemoryMonitor(
        docker_client, config, AsyncMock(), AsyncMock(), stats_timeout=0.1,
        protected_containers=protected,
    )
    monitor.get_memory_percent = MagicMock(return_value=97.0)  # type: ignore[method-assign]
    monitor._system_memory = MagicMock(return_value=(97.0, 1024))  # type: ignore[method-assign]
    return monitor


# -- selection and execution ------------------------------------------------


async def test_priority_container_listed_as_killable_is_not_the_target():
    docker_client = _docker("postgres", "junk")
    monitor = _monitor(_config(["postgres", "junk"], priority=["postgres"]), docker_client)

    await monitor._handle_critical(97.0)

    assert monitor.get_pending_kill() == "junk"


async def test_globally_protected_container_is_never_auto_killed():
    docker_client = _docker("vaultwarden", "junk")
    monitor = _monitor(_config(["vaultwarden", "junk"]), docker_client, protected=["vaultwarden"])

    await monitor._handle_critical(97.0)
    assert monitor.get_pending_kill() == "junk"
    assert (await monitor.kill_container("vaultwarden")).success is False
    docker_client.containers.get("vaultwarden").stop.assert_not_called()


async def test_target_made_priority_during_the_countdown_is_spared():
    docker_client = _docker("junk", "other")
    config = _config(["junk", "other"])
    monitor = _monitor(config, docker_client)
    monitor._state = MemoryState.CRITICAL
    await monitor._handle_critical(97.0)
    assert monitor.get_pending_kill() == "junk"

    config.priority_containers.append("junk")  # edited while the countdown runs
    await monitor._execute_kill_countdown()

    docker_client.containers.get("junk").stop.assert_not_called()
    assert monitor.get_pending_kill() == "other"


def test_config_drops_a_container_listed_as_both_priority_and_killable():
    config = MemoryConfig.from_dict({
        "enabled": True,
        "priority_containers": ["postgres"],
        "killable_containers": ["postgres", "junk"],
    })
    assert config.killable_containers == ["junk"]


async def test_wizard_refuses_killable_on_a_priority_container(tmp_path):
    wizard = SetupWizard(str(tmp_path / "config.yaml"), MagicMock(), None, None)
    wizard.start(user_id=1)
    session = wizard.get_session_data(1)
    session.classifications = [ContainerClassification(name="db", image="pg", categories={"priority"})]
    callback = AsyncMock()
    callback.from_user = MagicMock(id=1)
    callback.data = "setup:toggle:killable:db"

    await create_toggle_callback(wizard)(callback)

    assert session.classifications[0].categories == {"priority"}
    assert "Remove it from priority first" in callback.answer.call_args.args[0]


async def test_wizard_save_never_writes_a_contradiction(tmp_path):
    path = tmp_path / "config.yaml"
    wizard = SetupWizard(str(path), MagicMock(), None, None)
    wizard.start(user_id=1)
    session = wizard.get_session_data(1)
    # A classifier is allowed to tag both ways; the save must not keep it.
    session.classifications = [
        ContainerClassification(name="db", image="pg", categories={"priority", "killable"}),
        ContainerClassification(name="junk", image="x", categories={"killable"}),
    ]

    wizard.save_config(1)

    data = yaml.safe_load(path.read_text())
    assert data["memory_management"]["killable_containers"] == ["junk"]


# -- stale buttons ------------------------------------------------------------


def _button(data: str, age: timedelta) -> MagicMock:
    from aiogram.types import Message

    callback = MagicMock()
    callback.data = data
    callback.answer = AsyncMock()
    callback.message = MagicMock(spec=Message)
    callback.message.date = datetime.now(timezone.utc) - age
    callback.message.answer = AsyncMock()
    return callback


def _pressure_monitor(active: bool) -> MagicMock:
    monitor = MagicMock()
    monitor.pressure_active = active
    monitor.kill_container = AsyncMock(return_value=MagicMock(success=True, freed_bytes=None,
                                                              system_percent=None))
    monitor.restart_container = AsyncMock()
    return monitor


async def test_live_alert_kill_button_acts_at_once():
    monitor = _pressure_monitor(active=True)
    await mem_kill_callback(monitor)(_button("mem_kill:junk", timedelta(minutes=2)))
    monitor.kill_container.assert_awaited_once_with("junk")


async def test_old_alert_kill_button_asks_first_then_confirm_stops():
    monitor = _pressure_monitor(active=True)
    callback = _button("mem_kill:junk", timedelta(hours=20))

    await mem_kill_callback(monitor)(callback)

    monitor.kill_container.assert_not_awaited()
    keyboard = callback.message.answer.call_args.kwargs["reply_markup"]
    confirm = keyboard.inline_keyboard[0][0].callback_data
    assert confirm == "ctrl_confirm:stop:junk"

    # The confirm goes through the same handler as /stop.
    container = MagicMock(status="running")
    docker_client = MagicMock()
    docker_client.containers.get.return_value = container
    confirm_cb = MagicMock()
    confirm_cb.data = confirm
    confirm_cb.answer = AsyncMock()
    confirm_cb.message = None
    await create_ctrl_confirm_callback(ContainerStateManager(), ContainerController(docker_client, []))(
        confirm_cb
    )
    container.stop.assert_called_once()


async def test_recent_button_after_pressure_ended_asks_first():
    monitor = _pressure_monitor(active=False)
    callback = _button("mem_kill:junk", timedelta(minutes=1))

    await mem_kill_callback(monitor)(callback)

    monitor.kill_container.assert_not_awaited()
    assert "ctrl_confirm:stop:junk" in str(callback.message.answer.call_args)


async def test_old_alert_restart_button_asks_first():
    monitor = _pressure_monitor(active=True)
    callback = _button("mem_restart:plex", timedelta(hours=3))

    await mem_restart_callback(monitor, _config([], restart_containers=["plex"]))(callback)

    monitor.restart_container.assert_not_awaited()
    assert "ctrl_confirm:restart:plex" in str(callback.message.answer.call_args)
