"""The bot starts with what it can reach.

Docker down at boot used to raise out of start_monitoring, which took the
Unraid and UPS monitors (which never touch Docker) down with it.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import docker
import pytest

from src.background import _BackgroundTasks
from src.bot.telegram_bot import create_dispatcher
from src.config import AppConfig, Settings
from src.alerts.manager import ChatIdStore
from src.monitors.docker_events import DockerEventMonitor
from src.services.docker_client import SharedDockerClient
from src.state import ContainerStateManager
from src.startup import start_monitoring


def _no_docker(*args, **kwargs):
    raise docker.errors.DockerException("Error while fetching server API version: no socket")


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "config.yaml").write_text(
        "nut:\n  enabled: true\n  host: 127.0.0.1\n  port: 1\n"
        "resource_monitoring:\n  enabled: false\n"
    )
    return tmp_path


async def test_ups_monitoring_starts_when_docker_is_down(workdir):
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None, telegram_bot_token="1:x", telegram_allowed_users="1",
        config_path=str(workdir / "config" / "config.yaml"),
    )
    config = AppConfig(settings)
    bot = MagicMock()
    bot.send_message = AsyncMock()
    bot.set_my_commands = AsyncMock()
    bot.delete_my_commands = AsyncMock()
    bg = _BackgroundTasks()

    with patch("src.monitors.docker_events.docker.DockerClient", side_effect=_no_docker), \
         patch("src.monitors.log_watcher.docker.DockerClient", side_effect=_no_docker), \
         patch("src.startup._build_provider_registry", new=AsyncMock(return_value=MagicMock(
             get_provider=MagicMock(return_value=None)))):
        await start_monitoring(config, settings, bot, create_dispatcher([1]), ChatIdStore(), bg)
        await asyncio.sleep(0)

        assert bg.ups_monitor is not None and bg.ups_monitor.is_running
        assert bg.monitor is not None and not bg.monitor.connected
        await bg.shutdown()


async def test_disconnected_shared_client_raises_a_docker_error():
    client = SharedDockerClient(None)
    assert not client.connected
    with pytest.raises(docker.errors.DockerException):
        client.containers.list()


async def test_event_monitor_connects_once_docker_answers():
    monitor = DockerEventMonitor(state_manager=ContainerStateManager())
    monitor.INITIAL_BACKOFF_SECONDS = 0  # type: ignore[misc]
    monitor._backoff_seconds = 0
    live = MagicMock()
    live.containers.list.return_value = []
    live.events.return_value = iter([])
    attempts = iter([_no_docker, _no_docker, lambda **kw: live])

    with patch("src.monitors.docker_events.docker.DockerClient",
               side_effect=lambda **kw: next(attempts)(**kw)):
        with pytest.raises(docker.errors.DockerException):
            monitor.connect()
        shared = monitor.shared_client
        assert shared is not None and not shared.connected

        task = asyncio.create_task(monitor.start())
        for _ in range(50):
            await asyncio.sleep(0.01)
            if monitor.connected:
                break
        monitor._running = False
        task.cancel()

    assert monitor.connected
    # Consumers holding the wrapper from startup now reach the live daemon.
    assert shared.client is live
