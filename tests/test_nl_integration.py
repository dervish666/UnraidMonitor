# tests/test_nl_integration.py
"""Integration tests for natural language chat feature."""
import pytest
from unittest.mock import Mock, AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from src.services.nl_processor import NLProcessor, MemoryStore
from src.services.nl_tools import NLToolExecutor, get_tool_definitions
from src.state import ContainerStateManager
from src.models import ContainerInfo


def make_tool_use_block(tool_name: str, tool_id: str, tool_input: dict) -> Mock:
    """Create a mock tool_use block with proper name attribute.

    Note: Mock(name=...) uses 'name' for the Mock's display name, not as an attribute.
    We must set .name as an attribute after construction.
    """
    block = Mock(type="tool_use", id=tool_id, input=tool_input)
    block.name = tool_name
    return block


@pytest.fixture
def state():
    """Create a ContainerStateManager with test containers."""
    state = ContainerStateManager()
    state.update(ContainerInfo(
        name="plex",
        status="running",
        health="healthy",
        image="plexinc/pms-docker:latest",
        started_at=datetime.now(timezone.utc),
    ))
    state.update(ContainerInfo(
        name="radarr",
        status="running",
        health=None,
        image="linuxserver/radarr:latest",
        started_at=datetime.now(timezone.utc),
    ))
    state.update(ContainerInfo(
        name="mariadb",
        status="running",
        health="healthy",
        image="mariadb:10",
        started_at=datetime.now(timezone.utc),
    ))
    return state


@pytest.fixture
def mock_docker():
    """Create a mock Docker client."""
    docker = Mock()
    container = Mock()
    container.logs.return_value = b"[INFO] Server started\n[ERROR] Connection timeout\n"
    docker.containers.get.return_value = container
    return docker


@pytest.fixture
def executor(state, mock_docker):
    """Create an NLToolExecutor with test state and mock Docker."""
    return NLToolExecutor(
        state=state,
        docker_client=mock_docker,
        protected_containers=["mariadb"],
    )


class TestNLIntegration:
    """Integration tests for the NL chat feature end-to-end flows."""

    @pytest.mark.asyncio
    async def test_end_to_end_status_query(self, executor):
        """Test a complete flow: user asks about container status."""
        # Mock Anthropic client with tool use
        mock_anthropic = Mock()

        # First response: tool use
        tool_use_block = make_tool_use_block("get_container_status", "123", {"name": "plex"})
        response1 = Mock(stop_reason="tool_use", content=[tool_use_block])

        # Second response: final text
        text_block = Mock(type="text", text="Plex is running and healthy.")
        response2 = Mock(stop_reason="end_turn", content=[text_block])

        mock_anthropic.messages.create = Mock(side_effect=[response1, response2])

        processor = NLProcessor(
            anthropic_client=mock_anthropic,
            tool_executor=executor,
        )

        result = await processor.process(user_id=123, message="how's plex doing?")

        assert "plex" in result.response.lower() or "running" in result.response.lower()

    @pytest.mark.asyncio
    async def test_followup_uses_context(self, executor):
        """Test that follow-up questions use conversation context."""
        mock_anthropic = Mock()

        # Simple text responses for simplicity
        text_response = Mock(stop_reason="end_turn", content=[Mock(type="text", text="OK")])
        mock_anthropic.messages.create = Mock(return_value=text_response)

        processor = NLProcessor(
            anthropic_client=mock_anthropic,
            tool_executor=executor,
        )

        # First query
        await processor.process(user_id=123, message="check plex")

        # Second query (follow-up)
        await processor.process(user_id=123, message="what about its logs?")

        # Check that second call included history
        calls = mock_anthropic.messages.create.call_args_list
        second_call_messages = calls[1][1]["messages"]
        # Should have: previous user msg, previous assistant msg, new user msg
        assert len(second_call_messages) >= 3

    @pytest.mark.asyncio
    async def test_protected_container_rejection(self, executor):
        """Test that protected containers cannot be controlled."""
        result = await executor.execute("restart_container", {"name": "mariadb"})

        assert "protected" in result.lower() or "cannot" in result.lower()

    @pytest.mark.asyncio
    async def test_action_returns_confirmation(self, executor):
        """Test that actions return confirmation needed."""
        result = await executor.execute("restart_container", {"name": "plex"})

        assert result.startswith("CONFIRMATION_NEEDED:")

    @pytest.mark.asyncio
    async def test_start_executes_immediately(self, executor):
        """Test that start doesn't require confirmation."""
        mock_controller = Mock()
        mock_controller.start = AsyncMock(return_value="Started plex")
        executor._controller = mock_controller

        result = await executor.execute("start_container", {"name": "plex"})

        assert "CONFIRMATION_NEEDED" not in result
        mock_controller.start.assert_called_once()

    @pytest.mark.asyncio
    async def test_tool_loop_with_multiple_tools(self, executor):
        """Test that processor handles multiple tool calls in sequence."""
        mock_anthropic = Mock()

        # First response: get status
        tool_use_1 = make_tool_use_block("get_container_status", "1", {"name": "plex"})
        response1 = Mock(stop_reason="tool_use", content=[tool_use_1])

        # Second response: get logs
        tool_use_2 = make_tool_use_block("get_container_logs", "2", {"name": "plex"})
        response2 = Mock(stop_reason="tool_use", content=[tool_use_2])

        # Third response: final text
        text_block = Mock(type="text", text="Plex is running. Logs show a connection timeout error.")
        response3 = Mock(stop_reason="end_turn", content=[text_block])

        mock_anthropic.messages.create = Mock(side_effect=[response1, response2, response3])

        processor = NLProcessor(
            anthropic_client=mock_anthropic,
            tool_executor=executor,
        )

        result = await processor.process(user_id=123, message="what's wrong with plex?")

        # Should have called API 3 times
        assert mock_anthropic.messages.create.call_count == 3
        assert "plex" in result.response.lower() or "connection" in result.response.lower()

    @pytest.mark.asyncio
    async def test_confirmation_stored_in_memory(self, executor):
        """Test that pending actions are stored in memory."""
        mock_anthropic = Mock()

        # Response with tool use for restart
        tool_use_block = make_tool_use_block("restart_container", "123", {"name": "plex"})
        response1 = Mock(stop_reason="tool_use", content=[tool_use_block])

        # Final text response
        text_block = Mock(type="text", text="I can restart plex for you. Please confirm.")
        response2 = Mock(stop_reason="end_turn", content=[text_block])

        mock_anthropic.messages.create = Mock(side_effect=[response1, response2])

        processor = NLProcessor(
            anthropic_client=mock_anthropic,
            tool_executor=executor,
        )

        result = await processor.process(user_id=123, message="restart plex")

        # Check pending action is set
        assert result.pending_action is not None
        assert result.pending_action["action"] == "restart"
        assert result.pending_action["container"] == "plex"

        # Check it's also in memory
        memory = processor.memory_store.get(123)
        assert memory is not None
        assert memory.pending_action == result.pending_action

    @pytest.mark.asyncio
    async def test_different_users_have_separate_contexts(self, executor):
        """Test that different users have separate conversation memories."""
        mock_anthropic = Mock()

        text_response = Mock(stop_reason="end_turn", content=[Mock(type="text", text="OK")])
        mock_anthropic.messages.create = Mock(return_value=text_response)

        processor = NLProcessor(
            anthropic_client=mock_anthropic,
            tool_executor=executor,
        )

        # User 1 sends message
        await processor.process(user_id=100, message="check plex")

        # User 2 sends message
        await processor.process(user_id=200, message="check radarr")

        # Verify separate memories
        memory_1 = processor.memory_store.get(100)
        memory_2 = processor.memory_store.get(200)

        assert memory_1 is not None
        assert memory_2 is not None
        assert memory_1 is not memory_2
        assert "plex" in memory_1.messages[0]["content"]
        assert "radarr" in memory_2.messages[0]["content"]

    @pytest.mark.asyncio
    async def test_error_handling_returns_fallback(self, executor):
        """Test that errors during processing return a fallback message."""
        mock_anthropic = Mock()
        mock_anthropic.messages.create = Mock(side_effect=Exception("API error"))

        processor = NLProcessor(
            anthropic_client=mock_anthropic,
            tool_executor=executor,
        )

        result = await processor.process(user_id=123, message="check plex")

        # Should return a user-friendly error message
        assert "sorry" in result.response.lower() or "couldn't" in result.response.lower()

    @pytest.mark.asyncio
    async def test_no_anthropic_client_returns_error(self, executor):
        """Test that missing Anthropic client returns helpful message."""
        processor = NLProcessor(
            anthropic_client=None,
            tool_executor=executor,
        )

        result = await processor.process(user_id=123, message="check plex")

        assert "not configured" in result.response.lower()

    @pytest.mark.asyncio
    async def test_container_not_found_returns_error(self, executor):
        """Test that non-existent container returns error message."""
        result = await executor.execute("get_container_status", {"name": "nonexistent"})

        assert "not found" in result.lower() or "no container" in result.lower()

    @pytest.mark.asyncio
    async def test_stop_protected_container_rejected(self, executor):
        """Test that stopping protected containers is rejected."""
        result = await executor.execute("stop_container", {"name": "mariadb"})

        assert "protected" in result.lower() or "cannot" in result.lower()

    @pytest.mark.asyncio
    async def test_pull_protected_container_rejected(self, executor):
        """Test that pulling protected containers is rejected."""
        result = await executor.execute("pull_container", {"name": "mariadb"})

        assert "protected" in result.lower() or "cannot" in result.lower()

    @pytest.mark.asyncio
    async def test_get_container_list_includes_all(self, state, mock_docker):
        """Test that container list includes all containers."""
        executor = NLToolExecutor(
            state=state,
            docker_client=mock_docker,
            protected_containers=[],
        )

        result = await executor.execute("get_container_list", {})

        assert "plex" in result
        assert "radarr" in result
        assert "mariadb" in result

    @pytest.mark.asyncio
    async def test_logs_retrieval(self, executor):
        """Test that logs are retrieved correctly."""
        result = await executor.execute("get_container_logs", {"name": "plex"})

        # Should contain log content
        assert "Server started" in result or "Connection timeout" in result

    @pytest.mark.asyncio
    async def test_new_message_clears_pending_action(self, executor):
        """Test that a new message clears any pending action."""
        mock_anthropic = Mock()

        # First: action that needs confirmation
        tool_use_block = make_tool_use_block("restart_container", "1", {"name": "plex"})
        response1 = Mock(stop_reason="tool_use", content=[tool_use_block])
        text_response1 = Mock(stop_reason="end_turn", content=[Mock(type="text", text="Confirm restart?")])

        # Second: simple query (no confirmation)
        text_response2 = Mock(stop_reason="end_turn", content=[Mock(type="text", text="Everything is fine.")])

        mock_anthropic.messages.create = Mock(side_effect=[response1, text_response1, text_response2])

        processor = NLProcessor(
            anthropic_client=mock_anthropic,
            tool_executor=executor,
        )

        # First message triggers confirmation
        result1 = await processor.process(user_id=123, message="restart plex")
        assert result1.pending_action is not None

        # Second message should clear pending action
        result2 = await processor.process(user_id=123, message="how are things?")
        # After new message, memory's pending action should be cleared
        memory = processor.memory_store.get(123)
        # Either result2 has no pending_action, or memory was cleared (result2 has None)
        assert result2.pending_action is None
