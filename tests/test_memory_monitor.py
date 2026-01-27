"""Tests for memory pressure monitor."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.monitors.memory_monitor import MemoryMonitor, MemoryState
from src.config import MemoryConfig


@pytest.fixture
def memory_config():
    return MemoryConfig(
        enabled=True,
        warning_threshold=90,
        critical_threshold=95,
        safe_threshold=80,
        kill_delay_seconds=60,
        stabilization_wait=180,
        priority_containers=["plex"],
        killable_containers=["bitmagnet", "obsidian"],
    )


@pytest.fixture
def mock_docker_client():
    return MagicMock()


@pytest.fixture
def mock_on_alert():
    return AsyncMock()


@pytest.fixture
def mock_on_ask_restart():
    return AsyncMock()


class TestMemoryMonitor:
    def test_init(self, memory_config, mock_docker_client, mock_on_alert, mock_on_ask_restart):
        monitor = MemoryMonitor(
            docker_client=mock_docker_client,
            config=memory_config,
            on_alert=mock_on_alert,
            on_ask_restart=mock_on_ask_restart,
        )

        assert monitor._config == memory_config
        assert monitor._state == MemoryState.NORMAL
        assert monitor._killed_containers == []
        assert not monitor._running

    def test_is_enabled(self, memory_config, mock_docker_client, mock_on_alert, mock_on_ask_restart):
        monitor = MemoryMonitor(
            docker_client=mock_docker_client,
            config=memory_config,
            on_alert=mock_on_alert,
            on_ask_restart=mock_on_ask_restart,
        )
        assert monitor.is_enabled() is True

    def test_is_disabled(self, mock_docker_client, mock_on_alert, mock_on_ask_restart):
        config = MemoryConfig.from_dict({"enabled": False})
        monitor = MemoryMonitor(
            docker_client=mock_docker_client,
            config=config,
            on_alert=mock_on_alert,
            on_ask_restart=mock_on_ask_restart,
        )
        assert monitor.is_enabled() is False


class TestMemoryReading:
    @patch("src.monitors.memory_monitor.psutil")
    def test_get_memory_percent(
        self, mock_psutil, memory_config, mock_docker_client, mock_on_alert, mock_on_ask_restart
    ):
        mock_psutil.virtual_memory.return_value = MagicMock(percent=85.5)

        monitor = MemoryMonitor(
            docker_client=mock_docker_client,
            config=memory_config,
            on_alert=mock_on_alert,
            on_ask_restart=mock_on_ask_restart,
        )

        percent = monitor.get_memory_percent()
        assert percent == 85.5
        mock_psutil.virtual_memory.assert_called_once()


class TestContainerControl:
    def test_get_next_killable_returns_first_running(
        self, memory_config, mock_docker_client, mock_on_alert, mock_on_ask_restart
    ):
        # Mock running containers
        container1 = MagicMock()
        container1.name = "bitmagnet"
        container1.status = "running"

        container2 = MagicMock()
        container2.name = "obsidian"
        container2.status = "running"

        mock_docker_client.containers.list.return_value = [container1, container2]

        monitor = MemoryMonitor(
            docker_client=mock_docker_client,
            config=memory_config,
            on_alert=mock_on_alert,
            on_ask_restart=mock_on_ask_restart,
        )

        # bitmagnet is first in killable list
        result = monitor._get_next_killable()
        assert result == "bitmagnet"

    def test_get_next_killable_skips_already_killed(
        self, memory_config, mock_docker_client, mock_on_alert, mock_on_ask_restart
    ):
        container1 = MagicMock()
        container1.name = "bitmagnet"
        container1.status = "exited"  # Already killed

        container2 = MagicMock()
        container2.name = "obsidian"
        container2.status = "running"

        mock_docker_client.containers.list.return_value = [container2]
        mock_docker_client.containers.get.return_value = container1

        monitor = MemoryMonitor(
            docker_client=mock_docker_client,
            config=memory_config,
            on_alert=mock_on_alert,
            on_ask_restart=mock_on_ask_restart,
        )
        monitor._killed_containers = ["bitmagnet"]

        result = monitor._get_next_killable()
        assert result == "obsidian"

    def test_get_next_killable_returns_none_when_exhausted(
        self, memory_config, mock_docker_client, mock_on_alert, mock_on_ask_restart
    ):
        mock_docker_client.containers.list.return_value = []

        monitor = MemoryMonitor(
            docker_client=mock_docker_client,
            config=memory_config,
            on_alert=mock_on_alert,
            on_ask_restart=mock_on_ask_restart,
        )
        monitor._killed_containers = ["bitmagnet", "obsidian"]

        result = monitor._get_next_killable()
        assert result is None

    @pytest.mark.asyncio
    async def test_stop_container(
        self, memory_config, mock_docker_client, mock_on_alert, mock_on_ask_restart
    ):
        container = MagicMock()
        container.name = "bitmagnet"
        mock_docker_client.containers.get.return_value = container

        monitor = MemoryMonitor(
            docker_client=mock_docker_client,
            config=memory_config,
            on_alert=mock_on_alert,
            on_ask_restart=mock_on_ask_restart,
        )

        await monitor._stop_container("bitmagnet")

        container.stop.assert_called_once()
        assert "bitmagnet" in monitor._killed_containers


class TestStateMachine:
    @pytest.mark.asyncio
    @patch("src.monitors.memory_monitor.psutil")
    async def test_normal_to_warning(
        self, mock_psutil, memory_config, mock_docker_client, mock_on_alert, mock_on_ask_restart
    ):
        mock_psutil.virtual_memory.return_value = MagicMock(percent=91.0)

        monitor = MemoryMonitor(
            docker_client=mock_docker_client,
            config=memory_config,
            on_alert=mock_on_alert,
            on_ask_restart=mock_on_ask_restart,
        )

        await monitor._check_memory()

        assert monitor._state == MemoryState.WARNING
        mock_on_alert.assert_called_once()
        assert "91" in mock_on_alert.call_args[0][1]

    @pytest.mark.asyncio
    @patch("src.monitors.memory_monitor.psutil")
    async def test_warning_to_critical(
        self, mock_psutil, memory_config, mock_docker_client, mock_on_alert, mock_on_ask_restart
    ):
        mock_psutil.virtual_memory.return_value = MagicMock(percent=96.0)
        mock_docker_client.containers.list.return_value = []

        monitor = MemoryMonitor(
            docker_client=mock_docker_client,
            config=memory_config,
            on_alert=mock_on_alert,
            on_ask_restart=mock_on_ask_restart,
        )
        monitor._state = MemoryState.WARNING

        await monitor._check_memory()

        assert monitor._state == MemoryState.CRITICAL
        mock_on_alert.assert_called()

    @pytest.mark.asyncio
    @patch("src.monitors.memory_monitor.psutil")
    async def test_returns_to_normal_below_warning(
        self, mock_psutil, memory_config, mock_docker_client, mock_on_alert, mock_on_ask_restart
    ):
        mock_psutil.virtual_memory.return_value = MagicMock(percent=85.0)

        monitor = MemoryMonitor(
            docker_client=mock_docker_client,
            config=memory_config,
            on_alert=mock_on_alert,
            on_ask_restart=mock_on_ask_restart,
        )
        monitor._state = MemoryState.WARNING

        await monitor._check_memory()

        assert monitor._state == MemoryState.NORMAL

    @pytest.mark.asyncio
    @patch("src.monitors.memory_monitor.psutil")
    async def test_recovering_asks_restart_when_safe(
        self, mock_psutil, memory_config, mock_docker_client, mock_on_alert, mock_on_ask_restart
    ):
        mock_psutil.virtual_memory.return_value = MagicMock(percent=75.0)

        monitor = MemoryMonitor(
            docker_client=mock_docker_client,
            config=memory_config,
            on_alert=mock_on_alert,
            on_ask_restart=mock_on_ask_restart,
        )
        monitor._state = MemoryState.RECOVERING
        monitor._killed_containers = ["bitmagnet"]

        await monitor._check_memory()

        mock_on_ask_restart.assert_called_once_with("bitmagnet")


class TestKillCountdown:
    @pytest.mark.asyncio
    @patch("src.monitors.memory_monitor.psutil")
    @patch("src.monitors.memory_monitor.asyncio.sleep", new_callable=AsyncMock)
    async def test_kill_after_countdown(
        self, mock_sleep, mock_psutil, memory_config, mock_docker_client, mock_on_alert, mock_on_ask_restart
    ):
        # Memory stays critical
        mock_psutil.virtual_memory.return_value = MagicMock(percent=96.0)

        container = MagicMock()
        container.name = "bitmagnet"
        mock_docker_client.containers.get.return_value = container
        mock_docker_client.containers.list.return_value = [container]

        monitor = MemoryMonitor(
            docker_client=mock_docker_client,
            config=memory_config,
            on_alert=mock_on_alert,
            on_ask_restart=mock_on_ask_restart,
        )
        monitor._state = MemoryState.CRITICAL
        monitor._pending_kill = "bitmagnet"

        await monitor._execute_kill_countdown()

        mock_sleep.assert_called_with(60)  # kill_delay_seconds
        container.stop.assert_called_once()
        assert "bitmagnet" in monitor._killed_containers

    @pytest.mark.asyncio
    @patch("src.monitors.memory_monitor.psutil")
    @patch("src.monitors.memory_monitor.asyncio.sleep", new_callable=AsyncMock)
    async def test_cancel_kill_aborts_countdown(
        self, mock_sleep, mock_psutil, memory_config, mock_docker_client, mock_on_alert, mock_on_ask_restart
    ):
        container = MagicMock()
        mock_docker_client.containers.get.return_value = container

        monitor = MemoryMonitor(
            docker_client=mock_docker_client,
            config=memory_config,
            on_alert=mock_on_alert,
            on_ask_restart=mock_on_ask_restart,
        )
        monitor._pending_kill = "bitmagnet"
        monitor._kill_cancelled = True

        await monitor._execute_kill_countdown()

        container.stop.assert_not_called()
        assert monitor._kill_cancelled is False  # Reset after handling

    def test_cancel_kill_command(
        self, memory_config, mock_docker_client, mock_on_alert, mock_on_ask_restart
    ):
        monitor = MemoryMonitor(
            docker_client=mock_docker_client,
            config=memory_config,
            on_alert=mock_on_alert,
            on_ask_restart=mock_on_ask_restart,
        )
        monitor._pending_kill = "bitmagnet"

        result = monitor.cancel_pending_kill()

        assert result is True
        assert monitor._kill_cancelled is True

    def test_cancel_kill_no_pending(
        self, memory_config, mock_docker_client, mock_on_alert, mock_on_ask_restart
    ):
        monitor = MemoryMonitor(
            docker_client=mock_docker_client,
            config=memory_config,
            on_alert=mock_on_alert,
            on_ask_restart=mock_on_ask_restart,
        )

        result = monitor.cancel_pending_kill()

        assert result is False


class TestRestartHandling:
    @pytest.mark.asyncio
    async def test_confirm_restart_starts_container(
        self, memory_config, mock_docker_client, mock_on_alert, mock_on_ask_restart
    ):
        container = MagicMock()
        mock_docker_client.containers.get.return_value = container

        monitor = MemoryMonitor(
            docker_client=mock_docker_client,
            config=memory_config,
            on_alert=mock_on_alert,
            on_ask_restart=mock_on_ask_restart,
        )
        monitor._killed_containers = ["bitmagnet", "obsidian"]
        monitor._state = MemoryState.RECOVERING

        await monitor.confirm_restart("bitmagnet")

        container.start.assert_called_once()
        assert "bitmagnet" not in monitor._killed_containers
        assert "obsidian" in monitor._killed_containers

    @pytest.mark.asyncio
    async def test_decline_restart_removes_from_list(
        self, memory_config, mock_docker_client, mock_on_alert, mock_on_ask_restart
    ):
        monitor = MemoryMonitor(
            docker_client=mock_docker_client,
            config=memory_config,
            on_alert=mock_on_alert,
            on_ask_restart=mock_on_ask_restart,
        )
        monitor._killed_containers = ["bitmagnet"]
        monitor._state = MemoryState.RECOVERING

        await monitor.decline_restart("bitmagnet")

        assert "bitmagnet" not in monitor._killed_containers
        assert monitor._state == MemoryState.NORMAL

    def test_get_killed_containers(
        self, memory_config, mock_docker_client, mock_on_alert, mock_on_ask_restart
    ):
        monitor = MemoryMonitor(
            docker_client=mock_docker_client,
            config=memory_config,
            on_alert=mock_on_alert,
            on_ask_restart=mock_on_ask_restart,
        )
        monitor._killed_containers = ["bitmagnet", "obsidian"]

        result = monitor.get_killed_containers()

        assert result == ["bitmagnet", "obsidian"]
