"""Tests for memory pressure monitor."""

import asyncio
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
    @pytest.mark.asyncio
    async def test_get_next_killable_returns_first_running(
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
        result = await monitor._get_next_killable()
        assert result == "bitmagnet"

    @pytest.mark.asyncio
    async def test_get_next_killable_skips_already_killed(
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

        result = await monitor._get_next_killable()
        assert result == "obsidian"

    @pytest.mark.asyncio
    async def test_get_next_killable_returns_none_when_exhausted(
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

        result = await monitor._get_next_killable()
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


class TestMemoryReporting:
    """Memory figures surfaced on kill buttons and confirmations."""

    @staticmethod
    def _stats(usage_bytes: int, cache_bytes: int = 0) -> dict:
        return {
            "memory_stats": {
                "usage": usage_bytes,
                "limit": 8 * 1024**3,
                "stats": {"cache": cache_bytes},
            }
        }

    def _running_container(self, name: str, usage_bytes: int) -> MagicMock:
        c = MagicMock()
        c.name = name
        c.status = "running"
        c.stats.return_value = self._stats(usage_bytes)
        return c

    @pytest.mark.asyncio
    async def test_get_killable_memory_includes_usage(
        self, memory_config, mock_docker_client, mock_on_alert, mock_on_ask_restart
    ):
        mems = {"bitmagnet": 500 * 1024**2, "obsidian": 1500 * 1024**2}
        r1 = MagicMock()
        r1.name = "bitmagnet"
        r2 = MagicMock()
        r2.name = "obsidian"
        mock_docker_client.containers.list.return_value = [r1, r2]
        mock_docker_client.containers.get.side_effect = (
            lambda name: self._running_container(name, mems[name])
        )

        monitor = MemoryMonitor(
            docker_client=mock_docker_client,
            config=memory_config,
            on_alert=mock_on_alert,
            on_ask_restart=mock_on_ask_restart,
        )

        result = await monitor.get_killable_memory()
        assert result == [("bitmagnet", 500 * 1024**2), ("obsidian", 1500 * 1024**2)]

    @pytest.mark.asyncio
    async def test_get_killable_memory_timeout_falls_back_to_names(
        self, memory_config, mock_docker_client, mock_on_alert, mock_on_ask_restart
    ):
        running = MagicMock()
        running.name = "bitmagnet"
        mock_docker_client.containers.list.return_value = [running]

        monitor = MemoryMonitor(
            docker_client=mock_docker_client,
            config=memory_config,
            on_alert=mock_on_alert,
            on_ask_restart=mock_on_ask_restart,
            stats_timeout=0.01,
        )

        async def slow(_name: str) -> int:
            await asyncio.sleep(1)
            return 123

        monitor._container_memory_bytes = slow  # type: ignore[assignment]

        result = await monitor.get_killable_memory()
        assert result == [("bitmagnet", None)]  # name kept, memory unknown

    @pytest.mark.asyncio
    @patch("src.monitors.memory_monitor.psutil")
    async def test_kill_container_returns_memory_context(
        self, mock_psutil, memory_config, mock_docker_client, mock_on_alert, mock_on_ask_restart
    ):
        mock_psutil.virtual_memory.return_value = MagicMock(percent=72.0, available=4 * 1024**3)
        mock_docker_client.containers.get.side_effect = (
            lambda name: self._running_container(name, 800 * 1024**2)
        )

        monitor = MemoryMonitor(
            docker_client=mock_docker_client,
            config=memory_config,
            on_alert=mock_on_alert,
            on_ask_restart=mock_on_ask_restart,
        )

        result = await monitor.kill_container("bitmagnet")
        assert result.success is True
        assert result.name == "bitmagnet"
        assert result.freed_bytes == 800 * 1024**2  # captured before the stop
        assert result.system_percent == 72.0
        assert result.system_available_bytes == 4 * 1024**3
        assert "bitmagnet" in monitor._killed_containers

    @pytest.mark.asyncio
    @patch("src.monitors.memory_monitor.psutil")
    async def test_kill_container_failure_returns_unsuccessful(
        self, mock_psutil, memory_config, mock_docker_client, mock_on_alert, mock_on_ask_restart
    ):
        import docker as docker_mod

        mock_psutil.virtual_memory.return_value = MagicMock(percent=72.0, available=1)
        mock_docker_client.containers.get.side_effect = docker_mod.errors.NotFound("nope")

        monitor = MemoryMonitor(
            docker_client=mock_docker_client,
            config=memory_config,
            on_alert=mock_on_alert,
            on_ask_restart=mock_on_ask_restart,
        )

        result = await monitor.kill_container("ghost")
        assert result.success is False
        assert result.name == "ghost"
        assert result.freed_bytes is None


class TestStateMachine:
    @pytest.mark.asyncio
    @patch("src.monitors.memory_monitor.psutil")
    async def test_normal_to_warning(
        self, mock_psutil, memory_config, mock_docker_client, mock_on_alert, mock_on_ask_restart
    ):
        mock_psutil.virtual_memory.return_value = MagicMock(percent=91.0)

        c1 = MagicMock()
        c1.name = "bitmagnet"
        c2 = MagicMock()
        c2.name = "obsidian"
        mock_docker_client.containers.list.return_value = [c1, c2]

        monitor = MemoryMonitor(
            docker_client=mock_docker_client,
            config=memory_config,
            on_alert=mock_on_alert,
            on_ask_restart=mock_on_ask_restart,
        )

        await monitor._check_memory()

        assert monitor._state == MemoryState.WARNING
        mock_on_alert.assert_called_once()
        args = mock_on_alert.call_args[0]
        assert "91" in args[1]  # message contains percentage
        assert args[2] == "warning"  # alert_type
        # killable is (name, memory_bytes|None); these mocks report no stats
        assert args[3] == [("bitmagnet", None), ("obsidian", None)]

    @pytest.mark.asyncio
    @patch("src.monitors.memory_monitor.psutil")
    async def test_warning_lists_only_running_killable(
        self, mock_psutil, memory_config, mock_docker_client, mock_on_alert, mock_on_ask_restart
    ):
        # obsidian is killable but NOT running -- only bitmagnet should be offered
        mock_psutil.virtual_memory.return_value = MagicMock(percent=91.0)
        running = MagicMock()
        running.name = "bitmagnet"
        mock_docker_client.containers.list.return_value = [running]

        monitor = MemoryMonitor(
            docker_client=mock_docker_client,
            config=memory_config,
            on_alert=mock_on_alert,
            on_ask_restart=mock_on_ask_restart,
        )

        await monitor._check_memory()

        args = mock_on_alert.call_args[0]
        assert args[3] == [("bitmagnet", None)]  # stopped 'obsidian' excluded
        assert args[4] == []  # nothing in the restart list

    @pytest.mark.asyncio
    @patch("src.monitors.memory_monitor.psutil")
    async def test_warning_no_running_killable(
        self, mock_psutil, memory_config, mock_docker_client, mock_on_alert, mock_on_ask_restart
    ):
        # No killable containers running at all
        mock_psutil.virtual_memory.return_value = MagicMock(percent=91.0)
        mock_docker_client.containers.list.return_value = []

        monitor = MemoryMonitor(
            docker_client=mock_docker_client,
            config=memory_config,
            on_alert=mock_on_alert,
            on_ask_restart=mock_on_ask_restart,
        )

        await monitor._check_memory()

        args = mock_on_alert.call_args[0]
        assert args[3] == []  # no buttons
        assert "No killable or restartable containers" in args[1]

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
        args = mock_on_alert.call_args[0]
        assert args[2] == "critical"  # alert_type
        # No killable containers running, so empty list
        assert args[3] == []

    @pytest.mark.asyncio
    @patch("src.monitors.memory_monitor.psutil")
    async def test_critical_alert_includes_memory(
        self, mock_psutil, memory_config, mock_docker_client, mock_on_alert, mock_on_ask_restart
    ):
        mock_psutil.virtual_memory.return_value = MagicMock(percent=96.0)
        running = MagicMock()
        running.name = "bitmagnet"
        mock_docker_client.containers.list.return_value = [running]

        def _get(name: str) -> MagicMock:
            c = MagicMock()
            c.name = name
            c.status = "running"
            c.stats.return_value = {
                "memory_stats": {"usage": 700 * 1024**2, "limit": 8 * 1024**3, "stats": {"cache": 0}}
            }
            return c

        mock_docker_client.containers.get.side_effect = _get

        monitor = MemoryMonitor(
            docker_client=mock_docker_client,
            config=memory_config,
            on_alert=mock_on_alert,
            on_ask_restart=mock_on_ask_restart,
        )
        monitor._state = MemoryState.WARNING

        await monitor._check_memory()

        args = mock_on_alert.call_args[0]
        assert args[2] == "critical"
        assert args[3] == [("bitmagnet", 700 * 1024**2)]  # name + memory for the button
        assert "using" in args[1].lower()

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


class TestNormalToCriticalSkip:
    @pytest.mark.asyncio
    @patch("src.monitors.memory_monitor.psutil")
    async def test_normal_to_critical_direct(
        self, mock_psutil, memory_config, mock_docker_client, mock_on_alert, mock_on_ask_restart
    ):
        """Memory jumping from normal directly to critical should go to CRITICAL."""
        mock_psutil.virtual_memory.return_value = MagicMock(percent=96.0)
        mock_docker_client.containers.list.return_value = []

        monitor = MemoryMonitor(
            docker_client=mock_docker_client,
            config=memory_config,
            on_alert=mock_on_alert,
            on_ask_restart=mock_on_ask_restart,
        )
        assert monitor._state == MemoryState.NORMAL

        await monitor._check_memory()

        assert monitor._state == MemoryState.CRITICAL
        mock_on_alert.assert_called()
        args = mock_on_alert.call_args[0]
        assert args[2] == "critical"

    @pytest.mark.asyncio
    @patch("src.monitors.memory_monitor.psutil")
    async def test_normal_stays_normal_below_warning(
        self, mock_psutil, memory_config, mock_docker_client, mock_on_alert, mock_on_ask_restart
    ):
        """Memory below warning should stay NORMAL."""
        mock_psutil.virtual_memory.return_value = MagicMock(percent=70.0)

        monitor = MemoryMonitor(
            docker_client=mock_docker_client,
            config=memory_config,
            on_alert=mock_on_alert,
            on_ask_restart=mock_on_ask_restart,
        )

        await monitor._check_memory()

        assert monitor._state == MemoryState.NORMAL
        mock_on_alert.assert_not_called()


class TestKillCountdown:
    @pytest.mark.asyncio
    @patch("src.monitors.memory_monitor.psutil")
    async def test_kill_after_countdown(
        self, mock_psutil, memory_config, mock_docker_client, mock_on_alert, mock_on_ask_restart
    ):
        # Memory stays critical
        mock_psutil.virtual_memory.return_value = MagicMock(percent=96.0)

        container = MagicMock()
        container.name = "bitmagnet"
        mock_docker_client.containers.get.return_value = container
        mock_docker_client.containers.list.return_value = [container]

        # Use short kill delay for test
        memory_config.kill_delay_seconds = 0.01

        monitor = MemoryMonitor(
            docker_client=mock_docker_client,
            config=memory_config,
            on_alert=mock_on_alert,
            on_ask_restart=mock_on_ask_restart,
        )
        monitor._state = MemoryState.CRITICAL
        monitor._pending_kill = "bitmagnet"

        await monitor._execute_kill_countdown()

        container.stop.assert_called_once()
        assert "bitmagnet" in monitor._killed_containers
        assert monitor._pending_kill is None

    @pytest.mark.asyncio
    @patch("src.monitors.memory_monitor.psutil")
    async def test_cancel_kill_aborts_countdown(
        self, mock_psutil, memory_config, mock_docker_client, mock_on_alert, mock_on_ask_restart
    ):
        mock_psutil.virtual_memory.return_value = MagicMock(percent=96.0)
        container = MagicMock()
        mock_docker_client.containers.get.return_value = container

        # Use longer delay to allow cancellation
        memory_config.kill_delay_seconds = 5.0

        monitor = MemoryMonitor(
            docker_client=mock_docker_client,
            config=memory_config,
            on_alert=mock_on_alert,
            on_ask_restart=mock_on_ask_restart,
        )
        monitor._pending_kill = "bitmagnet"

        # Start the countdown in background
        import asyncio
        countdown_task = asyncio.create_task(monitor._execute_kill_countdown())

        # Wait a bit then cancel
        await asyncio.sleep(0.01)
        result = await monitor.cancel_pending_kill()

        # Wait for countdown to complete
        await countdown_task

        assert result is True
        container.stop.assert_not_called()
        assert monitor._pending_kill is None

    @pytest.mark.asyncio
    async def test_cancel_kill_command(
        self, memory_config, mock_docker_client, mock_on_alert, mock_on_ask_restart
    ):
        memory_config.kill_delay_seconds = 5.0

        monitor = MemoryMonitor(
            docker_client=mock_docker_client,
            config=memory_config,
            on_alert=mock_on_alert,
            on_ask_restart=mock_on_ask_restart,
        )
        monitor._pending_kill = "bitmagnet"

        # Start countdown to create the cancel event
        import asyncio
        countdown_task = asyncio.create_task(monitor._execute_kill_countdown())
        await asyncio.sleep(0.01)  # Let it initialize

        result = await monitor.cancel_pending_kill()
        await countdown_task

        assert result is True
        assert monitor._pending_kill is None

    @pytest.mark.asyncio
    async def test_cancel_kill_no_pending(
        self, memory_config, mock_docker_client, mock_on_alert, mock_on_ask_restart
    ):
        monitor = MemoryMonitor(
            docker_client=mock_docker_client,
            config=memory_config,
            on_alert=mock_on_alert,
            on_ask_restart=mock_on_ask_restart,
        )

        result = await monitor.cancel_pending_kill()

        assert result is False


class TestKilledContainersClearOnRecovery:
    @pytest.mark.asyncio
    async def test_killed_containers_cleared_on_normal_recovery(
        self, memory_config, mock_docker_client, mock_on_alert, mock_on_ask_restart
    ):
        """Killed containers list should be cleared when state returns to NORMAL."""
        monitor = MemoryMonitor(
            docker_client=mock_docker_client,
            config=memory_config,
            on_alert=mock_on_alert,
            on_ask_restart=mock_on_ask_restart,
        )

        monitor._state = MemoryState.WARNING
        monitor._killed_containers = ["bitmagnet"]

        with patch("src.monitors.memory_monitor.psutil") as mock_psutil:
            mock_psutil.virtual_memory.return_value = MagicMock(percent=70.0)
            await monitor._check_memory()

        assert monitor._state == MemoryState.NORMAL
        assert monitor._killed_containers == []


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


class TestPollingLoop:
    @pytest.mark.asyncio
    @patch("src.monitors.memory_monitor.psutil")
    @patch("src.monitors.memory_monitor.asyncio.sleep", new_callable=AsyncMock)
    async def test_start_polls_memory(
        self, mock_sleep, mock_psutil, memory_config, mock_docker_client, mock_on_alert, mock_on_ask_restart
    ):
        mock_psutil.virtual_memory.return_value = MagicMock(percent=50.0)

        # Make sleep raise after first call to stop loop
        call_count = 0

        async def sleep_side_effect(duration):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise asyncio.CancelledError()

        mock_sleep.side_effect = sleep_side_effect

        monitor = MemoryMonitor(
            docker_client=mock_docker_client,
            config=memory_config,
            on_alert=mock_on_alert,
            on_ask_restart=mock_on_ask_restart,
        )

        with pytest.raises(asyncio.CancelledError):
            await monitor.start()

        assert mock_psutil.virtual_memory.called

    def test_stop_sets_running_false(
        self, memory_config, mock_docker_client, mock_on_alert, mock_on_ask_restart
    ):
        monitor = MemoryMonitor(
            docker_client=mock_docker_client,
            config=memory_config,
            on_alert=mock_on_alert,
            on_ask_restart=mock_on_ask_restart,
        )
        monitor._running = True

        monitor.stop()

        assert monitor._running is False


GB = 1024**3


def _pressure_config(**overrides) -> MemoryConfig:
    """MemoryConfig with sane pressure thresholds; lists via overrides."""
    defaults = dict(
        enabled=True,
        warning_threshold=90,
        critical_threshold=95,
        safe_threshold=80,
        kill_delay_seconds=60,
        stabilization_wait=180,
        priority_containers=[],
        killable_containers=[],
        restart_containers=[],
    )
    defaults.update(overrides)
    return MemoryConfig(**defaults)


class TestWarningAlertContent:
    """Top-consumer list and size-sorted button payloads on warning alerts."""

    @staticmethod
    def _stats(usage_bytes: int, cache_bytes: int = 0) -> dict:
        return {
            "memory_stats": {
                "usage": usage_bytes,
                "limit": 8 * 1024**3,
                "stats": {"cache": cache_bytes},
            }
        }

    def _running_container(self, name: str, usage_bytes: int) -> MagicMock:
        c = MagicMock()
        c.name = name
        c.status = "running"
        c.stats.return_value = self._stats(usage_bytes)
        return c

    def _docker_with(self, mock_docker_client, mems: dict) -> None:
        containers = []
        for name in mems:
            c = MagicMock()
            c.name = name
            containers.append(c)
        mock_docker_client.containers.list.return_value = containers
        mock_docker_client.containers.get.side_effect = (
            lambda name: self._running_container(name, mems[name])
        )

    @pytest.mark.asyncio
    @patch("src.monitors.memory_monitor.psutil")
    async def test_warning_sorts_buttons_and_lists_top_users(
        self, mock_psutil, mock_docker_client, mock_on_alert, mock_on_ask_restart
    ):
        mock_psutil.virtual_memory.return_value = MagicMock(percent=91.0)
        config = _pressure_config(
            killable_containers=["sab", "bitmagnet"],  # config (priority) order
            restart_containers=["plex"],
        )
        self._docker_with(mock_docker_client, {
            "plex": 8 * GB, "sab": 1 * GB, "bitmagnet": 2 * GB, "postgres": 3 * GB,
        })

        monitor = MemoryMonitor(mock_docker_client, config, mock_on_alert, mock_on_ask_restart)
        await monitor._check_memory()

        title, message, alert_type, killable, restartable = mock_on_alert.call_args[0]
        assert alert_type == "warning"
        # Buttons are size-sorted, not config order
        assert killable == [("bitmagnet", 2 * GB), ("sab", 1 * GB)]
        assert restartable == [("plex", 8 * GB)]
        # Message lists top users largest first, including non-actionable ones
        assert "Top memory users" in message
        assert (
            message.index("plex")
            < message.index("postgres")
            < message.index("bitmagnet")
            < message.index("sab")
        )

    @pytest.mark.asyncio
    @patch("src.monitors.memory_monitor.psutil")
    async def test_warning_top_users_capped_at_five(
        self, mock_psutil, mock_docker_client, mock_on_alert, mock_on_ask_restart
    ):
        mock_psutil.virtual_memory.return_value = MagicMock(percent=91.0)
        mems = {f"svc{i}": i * GB for i in range(1, 8)}  # svc1 .. svc7
        self._docker_with(mock_docker_client, mems)

        monitor = MemoryMonitor(
            mock_docker_client, _pressure_config(), mock_on_alert, mock_on_ask_restart,
        )
        await monitor._check_memory()

        message = mock_on_alert.call_args[0][1]
        assert "svc7" in message and "svc3" in message  # top five: svc7..svc3
        assert "svc2" not in message and "svc1" not in message

    @pytest.mark.asyncio
    @patch("src.monitors.memory_monitor.psutil")
    async def test_warning_snapshot_timeout_falls_back_to_names(
        self, mock_psutil, mock_docker_client, mock_on_alert, mock_on_ask_restart
    ):
        mock_psutil.virtual_memory.return_value = MagicMock(percent=91.0)
        config = _pressure_config(
            killable_containers=["bitmagnet"], restart_containers=["plex"],
        )
        c1, c2 = MagicMock(), MagicMock()
        c1.name = "bitmagnet"
        c2.name = "plex"
        mock_docker_client.containers.list.return_value = [c1, c2]

        monitor = MemoryMonitor(
            mock_docker_client, config, mock_on_alert, mock_on_ask_restart,
            stats_timeout=0.01,
        )

        async def slow(_name: str) -> int:
            await asyncio.sleep(1)
            return 123

        monitor._container_memory_bytes = slow  # type: ignore[assignment]
        await monitor._check_memory()

        args = mock_on_alert.call_args[0]
        assert args[3] == [("bitmagnet", None)]  # names kept, sizes unknown
        assert args[4] == [("plex", None)]
        assert "Top memory users" not in args[1]

    @pytest.mark.asyncio
    @patch("src.monitors.memory_monitor.psutil")
    async def test_critical_includes_restartable(
        self, mock_psutil, mock_docker_client, mock_on_alert, mock_on_ask_restart
    ):
        mock_psutil.virtual_memory.return_value = MagicMock(percent=96.0)
        config = _pressure_config(
            killable_containers=["sab"], restart_containers=["plex"],
        )
        self._docker_with(mock_docker_client, {"plex": 8 * GB, "sab": 1 * GB})

        monitor = MemoryMonitor(mock_docker_client, config, mock_on_alert, mock_on_ask_restart)
        await monitor._check_memory()

        title, message, alert_type, killable, restartable = mock_on_alert.call_args[0]
        assert title == "Memory Critical"
        assert killable == [("sab", 1 * GB)]  # auto-kill target keeps priority order
        assert restartable == [("plex", 8 * GB)]

    @pytest.mark.asyncio
    async def test_get_restartable_memory_sorted_largest_first(
        self, mock_docker_client, mock_on_alert, mock_on_ask_restart
    ):
        config = _pressure_config(restart_containers=["small", "big"])
        self._docker_with(mock_docker_client, {"small": 1 * GB, "big": 4 * GB})

        monitor = MemoryMonitor(mock_docker_client, config, mock_on_alert, mock_on_ask_restart)
        result = await monitor.get_restartable_memory()
        assert result == [("big", 4 * GB), ("small", 1 * GB)]


class TestRestartContainer:
    """restart_container(): the gentle alternative to a kill."""

    @staticmethod
    def _stats(usage_bytes: int) -> dict:
        return {
            "memory_stats": {
                "usage": usage_bytes,
                "limit": 8 * 1024**3,
                "stats": {"cache": 0},
            }
        }

    @pytest.mark.asyncio
    @patch("src.monitors.memory_monitor.psutil")
    async def test_restart_returns_memory_context(
        self, mock_psutil, mock_docker_client, mock_on_alert, mock_on_ask_restart
    ):
        mock_psutil.virtual_memory.return_value = MagicMock(
            percent=78.0, available=4 * GB,
        )
        container = MagicMock()
        container.name = "plex"
        container.status = "running"
        container.stats.return_value = self._stats(8 * GB)
        mock_docker_client.containers.get.return_value = container

        monitor = MemoryMonitor(
            mock_docker_client, _pressure_config(restart_containers=["plex"]),
            mock_on_alert, mock_on_ask_restart,
        )
        result = await monitor.restart_container("plex")

        container.restart.assert_called_once_with(timeout=10)
        assert result.success is True
        assert result.freed_bytes == 8 * GB
        assert result.system_percent == 78.0
        assert result.system_available_bytes == 4 * GB
        # A restart is not a kill: nothing recorded for the recovery prompt
        assert monitor._killed_containers == []

    @pytest.mark.asyncio
    async def test_restart_failure_returns_unsuccessful(
        self, mock_docker_client, mock_on_alert, mock_on_ask_restart
    ):
        mock_docker_client.containers.get.side_effect = Exception("daemon exploded")

        monitor = MemoryMonitor(
            mock_docker_client, _pressure_config(), mock_on_alert, mock_on_ask_restart,
        )
        result = await monitor.restart_container("plex")
        assert result.success is False
        assert result.freed_bytes is None

    @pytest.mark.asyncio
    @patch("src.monitors.memory_monitor.psutil")
    async def test_restart_cancels_pending_kill_of_same_container(
        self, mock_psutil, mock_docker_client, mock_on_alert, mock_on_ask_restart
    ):
        mock_psutil.virtual_memory.return_value = MagicMock(percent=90.0, available=2 * GB)
        monitor = MemoryMonitor(
            mock_docker_client, _pressure_config(), mock_on_alert, mock_on_ask_restart,
        )
        monitor._pending_kill = "plex"
        event = asyncio.Event()
        monitor._kill_cancel_event = event

        await monitor.restart_container("plex")
        assert event.is_set()

    @pytest.mark.asyncio
    @patch("src.monitors.memory_monitor.psutil")
    async def test_restart_keeps_pending_kill_of_other_container(
        self, mock_psutil, mock_docker_client, mock_on_alert, mock_on_ask_restart
    ):
        mock_psutil.virtual_memory.return_value = MagicMock(percent=90.0, available=2 * GB)
        monitor = MemoryMonitor(
            mock_docker_client, _pressure_config(), mock_on_alert, mock_on_ask_restart,
        )
        monitor._pending_kill = "sab"
        event = asyncio.Event()
        monitor._kill_cancel_event = event

        await monitor.restart_container("plex")
        assert not event.is_set()
