# tests/test_p2_coverage_b.py
"""
P2 coverage tests:
  P2-2: DockerEventMonitor (_reconnect, stop, connect)
  P2-4: control_commands (stop, start, pull)
  P2-5: NLProcessor (rate limiting, message length, max tool iterations)
  P2-6: MemoryStore (TTL expiration, max users eviction)
  P2-7: LogWatcher (_watch_container retry, _watch_container stops when not running)
"""
import asyncio
import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, AsyncMock, patch, Mock


# ---------------------------------------------------------------------------
# P2-2: DockerEventMonitor
# ---------------------------------------------------------------------------


class TestDockerEventMonitorReconnect:
    """Tests for DockerEventMonitor._reconnect."""

    def _make_monitor(self, **kwargs):
        from src.monitors.docker_events import DockerEventMonitor
        from src.state import ContainerStateManager

        state = ContainerStateManager()
        return DockerEventMonitor(state_manager=state, **kwargs)

    @patch("src.monitors.docker_events.docker.DockerClient")
    def test_reconnect_closes_old_client_and_creates_new(self, mock_docker_cls):
        """_reconnect should close the existing client and create a new DockerClient."""
        monitor = self._make_monitor()

        # Set up an existing (old) client
        old_client = MagicMock()
        old_client.containers.list.return_value = []
        monitor._client = old_client

        # The new client returned by DockerClient()
        new_client = MagicMock()
        new_client.containers.list.return_value = []
        mock_docker_cls.return_value = new_client

        monitor._reconnect()

        # Old client must have been closed
        old_client.close.assert_called_once()
        # New client must have been instantiated
        mock_docker_cls.assert_called_once_with(base_url="unix:///var/run/docker.sock")
        # monitor._client should now be the new client
        assert monitor._client is new_client

    @patch("src.monitors.docker_events.docker.DockerClient")
    def test_reconnect_calls_load_initial_state(self, mock_docker_cls):
        """_reconnect should call load_initial_state after creating a new client."""
        from src.models import ContainerInfo

        monitor = self._make_monitor()

        old_client = MagicMock()
        old_client.containers.list.return_value = []
        monitor._client = old_client

        # Prepare a mock container for the new client so load_initial_state populates state
        mock_container = MagicMock()
        mock_container.name = "plex"
        mock_container.status = "running"
        mock_container.image.tags = ["plexinc/plex:latest"]
        mock_container.attrs = {"State": {}}

        new_client = MagicMock()
        new_client.containers.list.return_value = [mock_container]
        mock_docker_cls.return_value = new_client

        monitor._reconnect()

        # load_initial_state should have been called and populated state
        info = monitor.state_manager.get("plex")
        assert info is not None
        assert info.name == "plex"
        assert info.status == "running"

    @patch("src.monitors.docker_events.docker.DockerClient")
    def test_reconnect_handles_close_error_gracefully(self, mock_docker_cls):
        """_reconnect should not raise if closing the old client fails."""
        monitor = self._make_monitor()

        old_client = MagicMock()
        old_client.close.side_effect = Exception("socket already closed")
        old_client.containers.list.return_value = []
        monitor._client = old_client

        new_client = MagicMock()
        new_client.containers.list.return_value = []
        mock_docker_cls.return_value = new_client

        # Should not raise
        monitor._reconnect()

        # New client should still be set
        assert monitor._client is new_client


class TestDockerEventMonitorStop:
    """Tests for DockerEventMonitor.stop."""

    def _make_monitor(self):
        from src.monitors.docker_events import DockerEventMonitor
        from src.state import ContainerStateManager

        state = ContainerStateManager()
        return DockerEventMonitor(state_manager=state)

    def test_stop_cancels_alert_task_and_closes_client(self):
        """stop() should cancel the alert task and close the Docker client."""
        monitor = self._make_monitor()

        mock_client = MagicMock()
        mock_task = MagicMock()

        monitor._client = mock_client
        monitor._alert_task = mock_task
        monitor._running = True

        monitor.stop()

        assert monitor._running is False
        mock_task.cancel.assert_called_once()
        mock_client.close.assert_called_once()

    def test_stop_sets_running_false_with_no_task_or_client(self):
        """stop() should work even when there is no alert_task or client."""
        monitor = self._make_monitor()
        monitor._running = True

        # Should not raise
        monitor.stop()

        assert monitor._running is False

    def test_stop_handles_client_close_error(self):
        """stop() should handle errors when closing the client."""
        monitor = self._make_monitor()

        mock_client = MagicMock()
        mock_client.close.side_effect = Exception("connection refused")
        monitor._client = mock_client
        monitor._running = True

        # Should not raise
        monitor.stop()

        assert monitor._running is False


class TestDockerEventMonitorConnect:
    """Tests for DockerEventMonitor.connect."""

    @patch("src.monitors.docker_events.docker.DockerClient")
    def test_connect_creates_docker_client(self, mock_docker_cls):
        """connect() should create a DockerClient with the configured socket path."""
        from src.monitors.docker_events import DockerEventMonitor
        from src.state import ContainerStateManager

        state = ContainerStateManager()
        monitor = DockerEventMonitor(
            state_manager=state,
            docker_socket_path="unix:///custom/docker.sock",
        )

        mock_client = MagicMock()
        mock_docker_cls.return_value = mock_client

        monitor.connect()

        mock_docker_cls.assert_called_once_with(base_url="unix:///custom/docker.sock")
        assert monitor._client is mock_client


# ---------------------------------------------------------------------------
# P2-4: control_commands (stop, start, pull)
# ---------------------------------------------------------------------------


class TestStopCommand:
    """Tests for the stop_command factory."""

    @pytest.mark.asyncio
    async def test_stop_command_prompts_for_confirmation(self):
        """The /stop handler should show inline confirmation buttons."""
        from src.bot.control_commands import stop_command
        from src.state import ContainerStateManager
        from src.models import ContainerInfo

        state = ContainerStateManager()
        state.update(ContainerInfo("sonarr", "running", None, "linuxserver/sonarr", None))

        controller = MagicMock()
        controller.is_protected.return_value = False

        handler = stop_command(state, controller)

        message = MagicMock()
        message.text = "/stop sonarr"
        message.from_user.id = 42
        message.answer = AsyncMock()

        await handler(message)

        # Should ask for confirmation with inline buttons
        message.answer.assert_called_once()
        response = message.answer.call_args[0][0]
        assert "Stop sonarr?" in response

        reply_markup = message.answer.call_args.kwargs.get("reply_markup")
        assert reply_markup is not None
        buttons = reply_markup.inline_keyboard[0]
        assert any("Confirm" in b.text for b in buttons)
        assert any("Cancel" in b.text for b in buttons)

    @pytest.mark.asyncio
    async def test_stop_command_missing_container_name(self):
        """The /stop handler should show usage when no container is specified."""
        from src.bot.control_commands import stop_command
        from src.state import ContainerStateManager

        state = ContainerStateManager()
        controller = MagicMock()

        handler = stop_command(state, controller)

        message = MagicMock()
        message.text = "/stop"
        message.from_user.id = 42
        message.answer = AsyncMock()

        await handler(message)

        response = message.answer.call_args[0][0]
        assert "Usage" in response
        assert "/stop" in response


class TestStartCommand:
    """Tests for the start_command factory."""

    @pytest.mark.asyncio
    async def test_start_command_shows_confirmation_buttons(self):
        """The /start handler should show inline confirmation buttons."""
        from src.bot.control_commands import start_command
        from src.state import ContainerStateManager
        from src.models import ContainerInfo

        state = ContainerStateManager()
        state.update(ContainerInfo("radarr", "exited", None, "linuxserver/radarr", None))

        controller = MagicMock()
        controller.is_protected.return_value = False

        handler = start_command(state, controller)

        message = MagicMock()
        message.text = "/start radarr"
        message.from_user.id = 99
        message.answer = AsyncMock()

        await handler(message)

        # Response should show start confirmation with buttons
        response = message.answer.call_args[0][0]
        assert "Start radarr?" in response

        reply_markup = message.answer.call_args.kwargs.get("reply_markup")
        assert reply_markup is not None

    @pytest.mark.asyncio
    async def test_start_command_confirm_callback_executes_start(self):
        """Clicking confirm button should call controller.start."""
        from src.bot.control_commands import create_ctrl_confirm_callback
        from src.state import ContainerStateManager
        from src.models import ContainerInfo

        state = ContainerStateManager()
        state.update(ContainerInfo("plex", "exited", None, "plexinc/plex", None))

        controller = MagicMock()
        controller.is_protected.return_value = False
        controller.start = AsyncMock(return_value="✅ plex started")

        handler = create_ctrl_confirm_callback(state, controller)

        callback = MagicMock()
        callback.data = "ctrl_confirm:start:plex"
        callback.from_user.id = 55
        callback.answer = AsyncMock()
        callback.message = MagicMock()
        callback.message.edit_text = AsyncMock()
        callback.message.bot.send_chat_action = AsyncMock()

        await handler(callback)

        controller.start.assert_called_once_with("plex")


class TestPullCommand:
    """Tests for the pull_command factory."""

    @pytest.mark.asyncio
    async def test_pull_command_prompts_for_confirmation(self):
        """The /pull handler should show inline confirmation buttons."""
        from src.bot.control_commands import pull_command
        from src.state import ContainerStateManager
        from src.models import ContainerInfo

        state = ContainerStateManager()
        state.update(ContainerInfo("plex", "running", "healthy", "plexinc/plex", None))

        controller = MagicMock()
        controller.is_protected.return_value = False

        handler = pull_command(state, controller)

        message = MagicMock()
        message.text = "/pull plex"
        message.from_user.id = 77
        message.answer = AsyncMock()

        await handler(message)

        # Should prompt for confirmation with buttons
        message.answer.assert_called_once()
        response = message.answer.call_args[0][0]
        assert "Pull plex?" in response

        reply_markup = message.answer.call_args.kwargs.get("reply_markup")
        assert reply_markup is not None
        buttons = reply_markup.inline_keyboard[0]
        assert any("Confirm" in b.text for b in buttons)

    @pytest.mark.asyncio
    async def test_pull_command_confirm_callback_executes_pull(self):
        """Clicking confirm button should call controller.pull_and_recreate."""
        from src.bot.control_commands import create_ctrl_confirm_callback
        from src.state import ContainerStateManager
        from src.models import ContainerInfo

        state = ContainerStateManager()
        state.update(ContainerInfo("plex", "running", None, "plexinc/plex", None))

        controller = MagicMock()
        controller.is_protected.return_value = False
        controller.pull_and_recreate = AsyncMock(return_value="✅ plex updated")

        handler = create_ctrl_confirm_callback(state, controller)

        callback = MagicMock()
        callback.data = "ctrl_confirm:pull:plex"
        callback.from_user.id = 77
        callback.answer = AsyncMock()
        callback.message = MagicMock()
        callback.message.edit_text = AsyncMock()
        callback.message.bot.send_chat_action = AsyncMock()

        await handler(callback)

        controller.pull_and_recreate.assert_called_once_with("plex")


# ---------------------------------------------------------------------------
# P2-5: NLProcessor (rate limiting, message length, max tool iterations)
# ---------------------------------------------------------------------------


class TestNLProcessorRateLimiting:
    """Tests for NLProcessor rate limiting."""

    @pytest.fixture
    def mock_provider(self):
        from unittest.mock import MagicMock
        from src.services.llm.provider import LLMResponse
        provider = MagicMock()
        provider.supports_tools = True
        provider.model_name = "test-model"
        provider.provider_name = "test"
        provider.chat = AsyncMock(return_value=LLMResponse(text="OK", stop_reason="end"))
        return provider

    @pytest.fixture
    def mock_executor(self):
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_rate_limiting_rejects_messages_over_limit(self, mock_provider, mock_executor):
        """Messages beyond the per-minute rate limit should be rejected."""
        from src.services.nl_processor import NLProcessor

        processor = NLProcessor(
            provider=mock_provider,
            tool_executor=mock_executor,
            rate_limit_per_minute=2,
            rate_limit_per_hour=100,
        )

        user_id = 123

        # First two messages should succeed
        result1 = await processor.process(user_id, "hello")
        assert "Rate limit" not in result1.response

        result2 = await processor.process(user_id, "world")
        assert "Rate limit" not in result2.response

        # Third message should be rate-limited
        result3 = await processor.process(user_id, "too many")
        assert "Rate limit" in result3.response
        assert "wait" in result3.response.lower()

    @pytest.mark.asyncio
    async def test_rate_limiting_is_per_user(self, mock_provider, mock_executor):
        """Rate limiting should be tracked per user, not globally."""
        from src.services.nl_processor import NLProcessor

        processor = NLProcessor(
            provider=mock_provider,
            tool_executor=mock_executor,
            rate_limit_per_minute=1,
            rate_limit_per_hour=100,
        )

        # User A sends one message (allowed)
        result_a = await processor.process(100, "from user A")
        assert "Rate limit" not in result_a.response

        # User B should still be allowed
        result_b = await processor.process(200, "from user B")
        assert "Rate limit" not in result_b.response


class TestNLProcessorMessageLength:
    """Tests for NLProcessor message length validation."""

    @pytest.mark.asyncio
    async def test_message_length_over_2000_rejected(self):
        """Messages longer than 2000 characters should be rejected."""
        from unittest.mock import MagicMock
        from src.services.llm.provider import LLMResponse
        from src.services.nl_processor import NLProcessor

        mock_provider = MagicMock()
        mock_provider.supports_tools = True
        mock_provider.model_name = "test-model"
        mock_provider.provider_name = "test"
        processor = NLProcessor(
            provider=mock_provider,
            tool_executor=AsyncMock(),
        )

        long_message = "x" * 2001
        result = await processor.process(user_id=1, message=long_message)

        assert "too long" in result.response.lower()
        assert "2001" in result.response
        assert "2000" in result.response

    @pytest.mark.asyncio
    async def test_message_length_exactly_2000_accepted(self):
        """A message that is exactly 2000 characters should be accepted."""
        from unittest.mock import MagicMock
        from src.services.llm.provider import LLMResponse
        from src.services.nl_processor import NLProcessor

        mock_provider = MagicMock()
        mock_provider.supports_tools = True
        mock_provider.model_name = "test-model"
        mock_provider.provider_name = "test"
        mock_provider.chat = AsyncMock(return_value=LLMResponse(text="Got it", stop_reason="end"))

        processor = NLProcessor(
            provider=mock_provider,
            tool_executor=AsyncMock(),
        )

        message_2000 = "a" * 2000
        result = await processor.process(user_id=1, message=message_2000)

        # Should NOT be rejected for length
        assert "too long" not in result.response.lower()


class TestNLProcessorMaxToolIterations:
    """Tests for NLProcessor max tool iterations."""

    @pytest.mark.asyncio
    async def test_max_tool_iterations_stops_the_loop(self):
        """The tool-use loop should stop after max_tool_iterations even if provider keeps requesting tools."""
        from unittest.mock import MagicMock
        from src.services.llm.provider import LLMResponse, ToolCall
        from src.services.nl_processor import NLProcessor

        mock_provider = MagicMock()
        mock_provider.supports_tools = True
        mock_provider.model_name = "test-model"
        mock_provider.provider_name = "test"

        mock_executor = AsyncMock()
        mock_executor.execute = AsyncMock(return_value="tool result data")

        # Every response requests another tool use (infinite loop scenario)
        tc = ToolCall(id="tool_1", name="get_status", input={})
        tool_response = LLMResponse(text="", stop_reason="tool_use", tool_calls=[tc])
        final_response = LLMResponse(text="", stop_reason="tool_use", tool_calls=[tc])

        # We set max_tool_iterations=3, so we need initial call + 3 loop calls
        mock_provider.chat = AsyncMock(
            side_effect=[tool_response, tool_response, tool_response, final_response]
        )

        processor = NLProcessor(
            provider=mock_provider,
            tool_executor=mock_executor,
            max_tool_iterations=3,
        )

        result = await processor.process(user_id=1, message="do something")

        # The API should have been called exactly 4 times:
        # 1 initial call + 3 iterations (hitting the max)
        assert mock_provider.chat.call_count == 4
        # The executor should have been called 3 times (once per iteration)
        assert mock_executor.execute.call_count == 3

    @pytest.mark.asyncio
    async def test_tool_loop_exits_early_on_end_turn(self):
        """The tool loop should exit when provider responds with end instead of tool_use."""
        from unittest.mock import MagicMock
        from src.services.llm.provider import LLMResponse, ToolCall
        from src.services.nl_processor import NLProcessor

        mock_provider = MagicMock()
        mock_provider.supports_tools = True
        mock_provider.model_name = "test-model"
        mock_provider.provider_name = "test"

        mock_executor = AsyncMock()
        mock_executor.execute = AsyncMock(return_value="status info")

        # First call returns tool_use, second returns end
        tc = ToolCall(id="t1", name="get_status", input={})
        tool_response = LLMResponse(text="", stop_reason="tool_use", tool_calls=[tc])
        final_response = LLMResponse(text="Here is the status.", stop_reason="end")

        mock_provider.chat = AsyncMock(
            side_effect=[tool_response, final_response]
        )

        processor = NLProcessor(
            provider=mock_provider,
            tool_executor=mock_executor,
            max_tool_iterations=10,
        )

        result = await processor.process(user_id=1, message="check status")

        # Should have exited early after 1 tool iteration
        assert mock_provider.chat.call_count == 2
        assert result.response == "Here is the status."


# ---------------------------------------------------------------------------
# P2-6: MemoryStore (TTL expiration, max users eviction)
# ---------------------------------------------------------------------------


class TestMemoryStoreTTL:
    """Tests for MemoryStore TTL expiration."""

    def test_ttl_expiration_removes_old_memories(self):
        """Memories older than the TTL should be cleaned up on get_or_create."""
        from src.services.nl_processor import MemoryStore

        store = MemoryStore(memory_ttl_minutes=30, max_users=100)

        # Create a memory for user 1
        memory = store.get_or_create(1)
        memory.add_exchange("hello", "hi")

        # Artificially age the memory beyond the TTL
        memory.last_activity = datetime.now() - timedelta(minutes=31)

        # Accessing for a different user triggers cleanup
        store.get_or_create(2)

        # User 1's memory should have been expired
        assert store.get(1) is None

    def test_ttl_does_not_remove_recent_memories(self):
        """Memories within the TTL window should be kept."""
        from src.services.nl_processor import MemoryStore

        store = MemoryStore(memory_ttl_minutes=30, max_users=100)

        memory = store.get_or_create(1)
        memory.add_exchange("hello", "hi")

        # Set last_activity to 10 minutes ago (within 30 min TTL)
        memory.last_activity = datetime.now() - timedelta(minutes=10)

        # Trigger cleanup via get_or_create for another user
        store.get_or_create(2)

        # User 1's memory should still exist
        assert store.get(1) is not None

    def test_ttl_expiration_cleans_multiple_users(self):
        """Multiple expired memories should all be cleaned up."""
        from src.services.nl_processor import MemoryStore

        store = MemoryStore(memory_ttl_minutes=5, max_users=100)

        # Create memories for users 1, 2, 3
        for uid in [1, 2, 3]:
            mem = store.get_or_create(uid)
            mem.add_exchange("q", "a")
            mem.last_activity = datetime.now() - timedelta(minutes=10)

        # Trigger cleanup
        store.get_or_create(99)

        # All old users should be expired
        assert store.get(1) is None
        assert store.get(2) is None
        assert store.get(3) is None
        # New user should exist
        assert store.get(99) is not None


class TestMemoryStoreMaxUsersEviction:
    """Tests for MemoryStore max users eviction."""

    def test_max_users_eviction_when_limit_reached(self):
        """When max_users is reached, the oldest memory should be evicted."""
        from src.services.nl_processor import MemoryStore

        store = MemoryStore(max_users=3, memory_ttl_minutes=9999)

        # Create three users
        for uid in [1, 2, 3]:
            mem = store.get_or_create(uid)
            mem.add_exchange(f"q{uid}", f"a{uid}")

        # Make user 1 the oldest
        store.get(1).last_activity = datetime.now() - timedelta(minutes=100)
        store.get(2).last_activity = datetime.now() - timedelta(minutes=50)
        store.get(3).last_activity = datetime.now() - timedelta(minutes=10)

        # Adding a 4th user should evict user 1 (the oldest)
        store.get_or_create(4)

        assert store.get(1) is None  # evicted
        assert store.get(2) is not None
        assert store.get(3) is not None
        assert store.get(4) is not None

    def test_max_users_eviction_preserves_newest(self):
        """Eviction should always remove the least-recently-active user."""
        from src.services.nl_processor import MemoryStore

        store = MemoryStore(max_users=2, memory_ttl_minutes=9999)

        mem1 = store.get_or_create(10)
        mem1.last_activity = datetime.now() - timedelta(minutes=5)

        mem2 = store.get_or_create(20)
        mem2.last_activity = datetime.now() - timedelta(minutes=1)

        # Adding user 30 should evict user 10 (oldest activity)
        store.get_or_create(30)

        assert store.get(10) is None
        assert store.get(20) is not None
        assert store.get(30) is not None

    def test_existing_user_does_not_trigger_eviction(self):
        """Accessing an existing user should not cause eviction."""
        from src.services.nl_processor import MemoryStore

        store = MemoryStore(max_users=2, memory_ttl_minutes=9999)

        store.get_or_create(1)
        store.get_or_create(2)

        # Re-access user 1 -- should NOT evict anyone
        store.get_or_create(1)

        assert store.get(1) is not None
        assert store.get(2) is not None


# ---------------------------------------------------------------------------
# P2-7: LogWatcher._watch_container retry behaviour
# ---------------------------------------------------------------------------


class TestLogWatcherWatchContainerRetry:
    """Tests for LogWatcher._watch_container retry loop."""

    def _make_watcher(self):
        from src.monitors.log_watcher import LogWatcher

        return LogWatcher(
            containers=["test-container"],
            error_patterns=["error"],
            ignore_patterns=[],
        )

    @pytest.mark.asyncio
    async def test_watch_container_retries_on_generic_error(self):
        """_watch_container should retry after a generic exception with a 5s sleep."""
        watcher = self._make_watcher()
        watcher._running = True

        call_count = 0

        async def fake_stream_logs(container_name):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise RuntimeError("connection lost")
            # On the third call, stop the watcher to exit the loop
            watcher._running = False

        with patch.object(watcher, "_stream_logs", side_effect=fake_stream_logs):
            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                await watcher._watch_container("test-container")

        # Should have been called 3 times (2 failures + 1 success that sets _running=False)
        assert call_count == 3
        # Should have slept with 5 second delay for each generic error
        assert mock_sleep.call_count == 2
        for call in mock_sleep.call_args_list:
            assert call[0][0] == 5

    @pytest.mark.asyncio
    async def test_watch_container_retries_on_not_found_with_30s_delay(self):
        """_watch_container should retry with 30s delay when container is not found."""
        import docker.errors
        watcher = self._make_watcher()
        watcher._running = True

        call_count = 0

        async def fake_stream_logs(container_name):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise docker.errors.NotFound("container not found")
            # On the second call, exit cleanly
            watcher._running = False

        with patch.object(watcher, "_stream_logs", side_effect=fake_stream_logs):
            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                await watcher._watch_container("test-container")

        assert call_count == 2
        # The first sleep should be 30 seconds for NotFound
        mock_sleep.assert_any_call(30)

    @pytest.mark.asyncio
    async def test_watch_container_stops_when_running_is_false(self):
        """_watch_container should exit immediately when _running is False."""
        watcher = self._make_watcher()
        watcher._running = False

        with patch.object(watcher, "_stream_logs", new_callable=AsyncMock) as mock_stream:
            await watcher._watch_container("test-container")

        # _stream_logs should never be called since _running is False from the start
        mock_stream.assert_not_called()

    @pytest.mark.asyncio
    async def test_watch_container_exits_loop_when_running_set_false_during_error(self):
        """If _running becomes False during an error recovery, the loop should exit."""
        watcher = self._make_watcher()
        watcher._running = True

        async def fake_stream_logs(container_name):
            # Set _running to False so the loop stops after the sleep
            watcher._running = False
            raise RuntimeError("oops")

        with patch.object(watcher, "_stream_logs", side_effect=fake_stream_logs):
            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                await watcher._watch_container("test-container")

        # _stream_logs called once, then error, then sleep, then loop checks _running and exits
        assert mock_sleep.call_count == 1
