"""Tests for alert callback handlers."""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import timedelta

from aiogram.types import Message

from src.bot.alert_callbacks import (
    restart_callback,
    logs_callback,
    diagnose_callback,
    mute_callback,
    raise_limit_callback,
    set_limit_callback,
)
from src.state import ContainerStateManager
from src.models import ContainerInfo


@pytest.fixture
def state():
    """Create a ContainerStateManager with test containers."""
    state = ContainerStateManager()
    state.update(ContainerInfo("plex", "running", "healthy", "linuxserver/plex", None))
    state.update(ContainerInfo("radarr", "running", None, "linuxserver/radarr", None))
    state.update(ContainerInfo("my:container:with:colons", "running", None, "test/image", None))
    return state


@pytest.fixture
def mock_callback():
    """Create a mock CallbackQuery."""
    callback = MagicMock()
    callback.answer = AsyncMock()
    callback.message = MagicMock(spec=Message)
    callback.message.text = None
    callback.message.answer = AsyncMock()
    callback.message.edit_text = AsyncMock()
    callback.message.chat = MagicMock()
    callback.message.chat.id = 12345
    callback.message.bot = MagicMock()
    callback.message.bot.send_chat_action = AsyncMock()
    callback.from_user = MagicMock()
    callback.from_user.id = 12345
    return callback


class TestRestartCallback:
    """Tests for restart_callback handler."""

    @pytest.mark.asyncio
    async def test_restart_success(self, state, mock_callback):
        """Test successful container restart."""
        controller = MagicMock()
        controller.is_protected = MagicMock(return_value=False)
        controller.restart = AsyncMock(return_value="✅ Restarted plex")

        handler = restart_callback(state, controller)
        mock_callback.data = "restart:plex"

        await handler(mock_callback)

        mock_callback.answer.assert_called_once_with("Restarting plex...")
        controller.restart.assert_called_once_with("plex")
        mock_callback.message.answer.assert_called_once_with("✅ Restarted plex")

    @pytest.mark.asyncio
    async def test_restart_container_not_found(self, state, mock_callback):
        """Test restart with non-existent container."""
        controller = MagicMock()

        handler = restart_callback(state, controller)
        mock_callback.data = "restart:nonexistent"

        await handler(mock_callback)

        mock_callback.answer.assert_called_once_with("Container 'nonexistent' not found")
        controller.restart.assert_not_called()

    @pytest.mark.asyncio
    async def test_restart_invalid_callback_data(self, state, mock_callback):
        """Test restart with invalid callback data."""
        controller = MagicMock()

        handler = restart_callback(state, controller)
        mock_callback.data = "restart"  # Missing container name

        await handler(mock_callback)

        mock_callback.answer.assert_called_once_with("Invalid callback data")

    @pytest.mark.asyncio
    async def test_restart_container_with_colons(self, state, mock_callback):
        """Test restart with container name containing colons."""
        controller = MagicMock()
        controller.is_protected = MagicMock(return_value=False)
        controller.restart = AsyncMock(return_value="✅ Restarted my:container:with:colons")

        handler = restart_callback(state, controller)
        mock_callback.data = "restart:my:container:with:colons"

        await handler(mock_callback)

        mock_callback.answer.assert_called_once_with("Restarting my:container:with:colons...")
        controller.restart.assert_called_once_with("my:container:with:colons")

    @pytest.mark.asyncio
    async def test_restart_no_callback_data(self, state, mock_callback):
        """Test restart with empty callback data."""
        controller = MagicMock()

        handler = restart_callback(state, controller)
        mock_callback.data = None

        await handler(mock_callback)

        mock_callback.answer.assert_not_called()

    @pytest.mark.asyncio
    async def test_restart_partial_match(self, state, mock_callback):
        """Test restart with partial container name match."""
        controller = MagicMock()
        controller.is_protected = MagicMock(return_value=False)
        controller.restart = AsyncMock(return_value="✅ Restarted radarr")

        handler = restart_callback(state, controller)
        mock_callback.data = "restart:rad"

        await handler(mock_callback)

        # Should find radarr via partial match
        mock_callback.answer.assert_called_once_with("Restarting radarr...")
        controller.restart.assert_called_once_with("radarr")


class TestLogsCallback:
    """Tests for logs_callback handler."""

    @pytest.mark.asyncio
    async def test_logs_success(self, state, mock_callback):
        """Test successful log retrieval."""
        docker_client = MagicMock()
        container = MagicMock()
        container.logs.return_value = b"2025-01-01 Log line 1\n2025-01-01 Log line 2\n"
        docker_client.containers.get.return_value = container

        handler = logs_callback(state, docker_client)
        mock_callback.data = "logs:plex:50"

        await handler(mock_callback)

        mock_callback.answer.assert_called_once_with("Fetching logs for plex...")
        docker_client.containers.get.assert_called_once_with("plex")
        container.logs.assert_called_once_with(tail=50, timestamps=False)
        mock_callback.message.answer.assert_called_once()

    @pytest.mark.asyncio
    async def test_logs_truncation(self, state, mock_callback):
        """Test log truncation when too long."""
        docker_client = MagicMock()
        container = MagicMock()
        # Create logs longer than max_chars
        long_logs = b"x" * 5000
        container.logs.return_value = long_logs
        docker_client.containers.get.return_value = container

        handler = logs_callback(state, docker_client, max_chars=100)
        mock_callback.data = "logs:plex:50"

        await handler(mock_callback)

        response = mock_callback.message.answer.call_args[0][0]
        assert "truncated" in response

    @pytest.mark.asyncio
    async def test_logs_container_not_found_in_state(self, state, mock_callback):
        """Test logs with container not in state."""
        docker_client = MagicMock()

        handler = logs_callback(state, docker_client)
        mock_callback.data = "logs:nonexistent:50"

        await handler(mock_callback)

        mock_callback.answer.assert_called_once_with("Container 'nonexistent' not found")

    @pytest.mark.asyncio
    async def test_logs_container_not_found_in_docker(self, state, mock_callback):
        """Test logs when container exists in state but not Docker."""
        import docker.errors

        docker_client = MagicMock()
        docker_client.containers.get.side_effect = docker.errors.NotFound("not found")

        handler = logs_callback(state, docker_client)
        mock_callback.data = "logs:plex:50"

        await handler(mock_callback)

        mock_callback.message.answer.assert_called_once()
        response = mock_callback.message.answer.call_args[0][0]
        assert "not found in Docker" in response

    @pytest.mark.asyncio
    async def test_logs_invalid_callback_data(self, state, mock_callback):
        """Test logs with invalid callback data."""
        docker_client = MagicMock()

        handler = logs_callback(state, docker_client)
        mock_callback.data = "logs"  # Missing container and lines

        await handler(mock_callback)

        mock_callback.answer.assert_called_once_with("Invalid callback data")

    @pytest.mark.asyncio
    async def test_logs_container_with_colons(self, state, mock_callback):
        """Test logs with container name containing colons."""
        docker_client = MagicMock()
        container = MagicMock()
        container.logs.return_value = b"Log line\n"
        docker_client.containers.get.return_value = container

        handler = logs_callback(state, docker_client)
        mock_callback.data = "logs:my:container:with:colons:50"

        await handler(mock_callback)

        mock_callback.answer.assert_called_once_with("Fetching logs for my:container:with:colons...")
        docker_client.containers.get.assert_called_once_with("my:container:with:colons")

    @pytest.mark.asyncio
    async def test_logs_caps_line_count(self, state, mock_callback):
        """Test that line count is capped at max_lines."""
        docker_client = MagicMock()
        container = MagicMock()
        container.logs.return_value = b"Log\n"
        docker_client.containers.get.return_value = container

        handler = logs_callback(state, docker_client, max_lines=100)
        mock_callback.data = "logs:plex:500"  # Request more than max

        await handler(mock_callback)

        container.logs.assert_called_once_with(tail=100, timestamps=False)

    @pytest.mark.asyncio
    async def test_logs_invalid_line_count(self, state, mock_callback):
        """Test logs with invalid line count defaults to 50."""
        docker_client = MagicMock()
        container = MagicMock()
        container.logs.return_value = b"Log\n"
        docker_client.containers.get.return_value = container

        handler = logs_callback(state, docker_client)
        mock_callback.data = "logs:plex:invalid"

        await handler(mock_callback)

        container.logs.assert_called_once_with(tail=50, timestamps=False)

    @pytest.mark.asyncio
    async def test_logs_markdown_fallback(self, state, mock_callback):
        """Test fallback to plain text when markdown fails."""
        from aiogram.exceptions import TelegramBadRequest

        docker_client = MagicMock()
        container = MagicMock()
        container.logs.return_value = b"Log with *markdown* chars\n"
        docker_client.containers.get.return_value = container

        # First call raises, second succeeds
        mock_callback.message.answer = AsyncMock(
            side_effect=[TelegramBadRequest(method=MagicMock(), message="Bad Request"), None]
        )

        handler = logs_callback(state, docker_client)
        mock_callback.data = "logs:plex:50"

        await handler(mock_callback)

        # Should be called twice - markdown then plain
        assert mock_callback.message.answer.call_count == 2


class TestDiagnoseCallback:
    """Tests for diagnose_callback handler."""

    @pytest.mark.asyncio
    async def test_diagnose_success(self, state, mock_callback):
        """Test successful diagnosis."""
        diagnostic = MagicMock()
        context = MagicMock()
        context.brief_summary = None
        diagnostic.gather_context = AsyncMock(return_value=context)
        diagnostic.analyze = AsyncMock(return_value="Container crashed due to OOM")
        diagnostic.store_context = MagicMock()

        handler = diagnose_callback(state, diagnostic)
        mock_callback.data = "diagnose:plex"

        await handler(mock_callback)

        mock_callback.answer.assert_called_once_with("Analyzing plex...")
        diagnostic.gather_context.assert_called_once_with("plex", lines=50, alert_context="")
        diagnostic.analyze.assert_called_once_with(context)
        diagnostic.store_context.assert_called_once()

    @pytest.mark.asyncio
    async def test_diagnose_no_service(self, state, mock_callback):
        """Test diagnose when service not configured."""
        handler = diagnose_callback(state, None)
        mock_callback.data = "diagnose:plex"

        await handler(mock_callback)

        mock_callback.answer.assert_called_once_with("AI diagnostics not configured")

    @pytest.mark.asyncio
    async def test_diagnose_container_not_found(self, state, mock_callback):
        """Test diagnose with non-existent container."""
        diagnostic = MagicMock()

        handler = diagnose_callback(state, diagnostic)
        mock_callback.data = "diagnose:nonexistent"

        await handler(mock_callback)

        mock_callback.answer.assert_called_once_with("Container 'nonexistent' not found")

    @pytest.mark.asyncio
    async def test_diagnose_gather_context_fails(self, state, mock_callback):
        """Test diagnose when context gathering fails."""
        diagnostic = MagicMock()
        diagnostic.gather_context = AsyncMock(return_value=None)

        handler = diagnose_callback(state, diagnostic)
        mock_callback.data = "diagnose:plex"

        await handler(mock_callback)

        response = mock_callback.message.answer.call_args_list[-1][0][0]
        assert "Could not get container info" in response

    @pytest.mark.asyncio
    async def test_diagnose_container_with_colons(self, state, mock_callback):
        """Test diagnose with container name containing colons."""
        diagnostic = MagicMock()
        context = MagicMock()
        diagnostic.gather_context = AsyncMock(return_value=context)
        diagnostic.analyze = AsyncMock(return_value="Analysis result")

        handler = diagnose_callback(state, diagnostic)
        mock_callback.data = "diagnose:my:container:with:colons"

        await handler(mock_callback)

        diagnostic.gather_context.assert_called_once_with("my:container:with:colons", lines=50, alert_context="")

    @pytest.mark.asyncio
    async def test_diagnose_invalid_callback_data(self, state, mock_callback):
        """Test diagnose with invalid callback data."""
        diagnostic = MagicMock()

        handler = diagnose_callback(state, diagnostic)
        mock_callback.data = "diagnose"  # Missing container name

        await handler(mock_callback)

        mock_callback.answer.assert_called_once_with("Invalid callback data")


class TestMuteCallback:
    """Tests for mute_callback handler."""

    @pytest.mark.asyncio
    async def test_mute_success(self, state, mock_callback):
        """Test successful mute."""
        mute_manager = MagicMock()
        mute_manager.add_mute = MagicMock()

        handler = mute_callback(state, mute_manager)
        mock_callback.data = "mute:plex:60"

        await handler(mock_callback)

        mute_manager.add_mute.assert_called_once_with("plex", timedelta(minutes=60))
        mock_callback.answer.assert_called_once_with("Muted plex for 1 hour(s)")

    @pytest.mark.asyncio
    async def test_mute_no_manager(self, state, mock_callback):
        """Test mute when manager not configured."""
        handler = mute_callback(state, None)
        mock_callback.data = "mute:plex:60"

        await handler(mock_callback)

        mock_callback.answer.assert_called_once_with("Mute manager not configured")

    @pytest.mark.asyncio
    async def test_mute_container_not_found(self, state, mock_callback):
        """Test mute with non-existent container."""
        mute_manager = MagicMock()

        handler = mute_callback(state, mute_manager)
        mock_callback.data = "mute:nonexistent:60"

        await handler(mock_callback)

        mock_callback.answer.assert_called_once_with("Container 'nonexistent' not found")
        mute_manager.add_mute.assert_not_called()

    @pytest.mark.asyncio
    async def test_mute_container_with_colons(self, state, mock_callback):
        """Test mute with container name containing colons."""
        mute_manager = MagicMock()

        handler = mute_callback(state, mute_manager)
        mock_callback.data = "mute:my:container:with:colons:60"

        await handler(mock_callback)

        mute_manager.add_mute.assert_called_once_with("my:container:with:colons", timedelta(minutes=60))

    @pytest.mark.asyncio
    async def test_mute_duration_minutes(self, state, mock_callback):
        """Test mute duration displayed in minutes."""
        mute_manager = MagicMock()

        handler = mute_callback(state, mute_manager)
        mock_callback.data = "mute:plex:30"

        await handler(mock_callback)

        mock_callback.answer.assert_called_once_with("Muted plex for 30 minute(s)")

    @pytest.mark.asyncio
    async def test_mute_duration_hours(self, state, mock_callback):
        """Test mute duration displayed in hours."""
        mute_manager = MagicMock()

        handler = mute_callback(state, mute_manager)
        mock_callback.data = "mute:plex:120"

        await handler(mock_callback)

        mock_callback.answer.assert_called_once_with("Muted plex for 2 hour(s)")

    @pytest.mark.asyncio
    async def test_mute_duration_days(self, state, mock_callback):
        """Test mute duration displayed in days."""
        mute_manager = MagicMock()

        handler = mute_callback(state, mute_manager)
        mock_callback.data = "mute:plex:2880"  # 2 days

        await handler(mock_callback)

        mock_callback.answer.assert_called_once_with("Muted plex for 2 day(s)")

    @pytest.mark.asyncio
    async def test_mute_invalid_callback_data(self, state, mock_callback):
        """Test mute with invalid callback data."""
        mute_manager = MagicMock()

        handler = mute_callback(state, mute_manager)
        mock_callback.data = "mute"  # Missing container and minutes

        await handler(mock_callback)

        mock_callback.answer.assert_called_once_with("Invalid callback data")

    @pytest.mark.asyncio
    async def test_mute_invalid_minutes_defaults_to_60(self, state, mock_callback):
        """Test mute with invalid minutes defaults to 60."""
        mute_manager = MagicMock()

        handler = mute_callback(state, mute_manager)
        mock_callback.data = "mute:plex:invalid"

        await handler(mock_callback)

        mute_manager.add_mute.assert_called_once_with("plex", timedelta(minutes=60))


class TestMemKillCallback:
    """Tests for mem_kill_callback with protected container support."""

    @pytest.mark.asyncio
    async def test_mem_kill_protected_container_rejected(self):
        from src.bot.alert_callbacks import mem_kill_callback

        memory_monitor = MagicMock()
        memory_monitor.kill_container = AsyncMock()

        handler = mem_kill_callback(memory_monitor, protected_containers=["plex"])

        callback = MagicMock()
        callback.data = "mem_kill:plex"
        callback.answer = AsyncMock()
        callback.message = MagicMock()
        callback.message.answer = AsyncMock()

        await handler(callback)

        callback.answer.assert_called_with("plex is a protected container")
        memory_monitor.kill_container.assert_not_called()

    @pytest.mark.asyncio
    async def test_mem_kill_unprotected_container_proceeds(self):
        from src.bot.alert_callbacks import mem_kill_callback

        memory_monitor = MagicMock()
        memory_monitor.kill_container = AsyncMock(return_value=True)

        handler = mem_kill_callback(memory_monitor, protected_containers=["mariadb"])

        callback = MagicMock()
        callback.data = "mem_kill:plex"
        callback.answer = AsyncMock()
        callback.message = MagicMock()
        callback.message.answer = AsyncMock()

        await handler(callback)

        memory_monitor.kill_container.assert_called_once_with("plex")

    @pytest.mark.asyncio
    async def test_mem_kill_no_protected_list(self):
        from src.bot.alert_callbacks import mem_kill_callback

        memory_monitor = MagicMock()
        memory_monitor.kill_container = AsyncMock(return_value=True)

        # No protected_containers arg — should proceed
        handler = mem_kill_callback(memory_monitor)

        callback = MagicMock()
        callback.data = "mem_kill:plex"
        callback.answer = AsyncMock()
        callback.message = MagicMock()
        callback.message.answer = AsyncMock()

        await handler(callback)

        memory_monitor.kill_container.assert_called_once_with("plex")


class TestRaiseLimitCallback:
    """Tests for raise_limit_callback."""

    @pytest.mark.asyncio
    async def test_shows_threshold_options(self, mock_callback):
        from src.config import ResourceConfig

        config = ResourceConfig(default_cpu_percent=80)
        handler = raise_limit_callback(config)

        mock_callback.data = "res_limit:plex:cpu:80"
        await handler(mock_callback)

        mock_callback.answer.assert_called_once()
        mock_callback.message.answer.assert_called_once()
        call_kwargs = mock_callback.message.answer.call_args
        assert "CPU" in call_kwargs.args[0]
        assert "plex" in call_kwargs.args[0]
        assert call_kwargs.kwargs["reply_markup"] is not None

    @pytest.mark.asyncio
    async def test_invalid_metric(self, mock_callback):
        from src.config import ResourceConfig

        config = ResourceConfig()
        handler = raise_limit_callback(config)

        mock_callback.data = "res_limit:plex:disk:80"
        await handler(mock_callback)

        mock_callback.answer.assert_called_once_with("Invalid metric")

    @pytest.mark.asyncio
    async def test_invalid_container_name(self, mock_callback):
        from src.config import ResourceConfig

        config = ResourceConfig()
        handler = raise_limit_callback(config)

        mock_callback.data = "res_limit:../etc/passwd:cpu:80"
        await handler(mock_callback)

        mock_callback.answer.assert_called_once_with("Invalid container name")


class TestSetLimitCallback:
    """Tests for set_limit_callback."""

    @pytest.mark.asyncio
    async def test_sets_cpu_threshold(self, mock_callback):
        from src.config import ResourceConfig

        config = ResourceConfig()
        mock_callback.message.edit_text = AsyncMock()
        handler = set_limit_callback(config)

        mock_callback.data = "res_set:plex:cpu:95"
        await handler(mock_callback)

        assert config.get_thresholds("plex") == (95, 85)
        mock_callback.message.edit_text.assert_called_once()
        assert "95%" in mock_callback.message.edit_text.call_args.args[0]

    @pytest.mark.asyncio
    async def test_sets_memory_threshold(self, mock_callback):
        from src.config import ResourceConfig

        config = ResourceConfig()
        mock_callback.message.edit_text = AsyncMock()
        handler = set_limit_callback(config)

        mock_callback.data = "res_set:plex:memory:90"
        await handler(mock_callback)

        assert config.get_thresholds("plex") == (80, 90)

    @pytest.mark.asyncio
    async def test_reset_to_default(self, mock_callback):
        from src.config import ResourceConfig

        config = ResourceConfig(container_overrides={"plex": {"cpu_percent": 95}})
        mock_callback.message.edit_text = AsyncMock()
        handler = set_limit_callback(config)

        mock_callback.data = "res_set:plex:cpu:0"
        await handler(mock_callback)

        assert config.get_thresholds("plex") == (80, 85)
        assert "Reset" in mock_callback.answer.call_args.args[0]

    @pytest.mark.asyncio
    async def test_invalid_metric(self, mock_callback):
        from src.config import ResourceConfig

        config = ResourceConfig()
        handler = set_limit_callback(config)

        mock_callback.data = "res_set:plex:disk:95"
        await handler(mock_callback)

        mock_callback.answer.assert_called_once_with("Invalid metric")


async def test_pull_callback_shows_confirmation():
    from src.bot.alert_callbacks import pull_callback
    from src.state import ContainerStateManager
    from src.models import ContainerInfo
    state = ContainerStateManager()
    state.update(ContainerInfo(name="radarr", status="running", health=None, image="img", started_at=None))
    controller = MagicMock(); controller.is_protected.return_value = False
    handler = pull_callback(state, controller)
    cb = MagicMock(); cb.data = "pull:radarr"; cb.answer = AsyncMock()
    cb.message = MagicMock(); cb.message.answer = AsyncMock()
    await handler(cb)
    cb.message.answer.assert_awaited_once()
    kb = cb.message.answer.call_args.kwargs["reply_markup"]
    assert kb.inline_keyboard[0][0].callback_data == "ctrl_confirm:pull:radarr"


async def test_pull_callback_blocks_protected():
    from src.bot.alert_callbacks import pull_callback
    from src.state import ContainerStateManager
    from src.models import ContainerInfo
    state = ContainerStateManager()
    state.update(ContainerInfo(name="mariadb", status="running", health=None, image="img", started_at=None))
    controller = MagicMock(); controller.is_protected.return_value = True
    handler = pull_callback(state, controller)
    cb = MagicMock(); cb.data = "pull:mariadb"; cb.answer = AsyncMock()
    cb.message = MagicMock(); cb.message.answer = AsyncMock()
    await handler(cb)
    cb.message.answer.assert_not_awaited()
    cb.answer.assert_awaited()
