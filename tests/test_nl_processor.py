# tests/test_nl_processor.py
import pytest
from datetime import datetime
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from src.services.nl_processor import ConversationMemory, MemoryStore, NLProcessor, ProcessResult


class TestConversationMemory:
    def test_add_exchange_stores_messages(self):
        memory = ConversationMemory(user_id=123)
        memory.add_exchange("what's wrong?", "everything is fine")

        assert len(memory.messages) == 2
        assert memory.messages[0]["role"] == "user"
        assert memory.messages[0]["content"] == "what's wrong?"
        assert memory.messages[1]["role"] == "assistant"
        assert memory.messages[1]["content"] == "everything is fine"

    def test_add_exchange_trims_to_max(self):
        memory = ConversationMemory(user_id=123, max_exchanges=2)

        memory.add_exchange("q1", "a1")
        memory.add_exchange("q2", "a2")
        memory.add_exchange("q3", "a3")  # Should push out q1/a1

        assert len(memory.messages) == 4  # 2 exchanges = 4 messages
        assert memory.messages[0]["content"] == "q2"

    def test_get_messages_returns_copy(self):
        memory = ConversationMemory(user_id=123)
        memory.add_exchange("q", "a")

        messages = memory.get_messages()
        messages.append({"role": "user", "content": "injected"})

        assert len(memory.messages) == 2  # Original unchanged

    def test_clear_removes_all_messages(self):
        memory = ConversationMemory(user_id=123)
        memory.add_exchange("q", "a")
        memory.clear()

        assert len(memory.messages) == 0

    def test_pending_action_initially_none(self):
        memory = ConversationMemory(user_id=123)
        assert memory.pending_action is None

    def test_set_and_get_pending_action(self):
        memory = ConversationMemory(user_id=123)
        memory.pending_action = {"action": "restart", "container": "plex"}

        assert memory.pending_action == {"action": "restart", "container": "plex"}

    def test_clear_also_clears_pending_action(self):
        memory = ConversationMemory(user_id=123)
        memory.pending_action = {"action": "restart", "container": "plex"}
        memory.clear()

        assert memory.pending_action is None


class TestMemoryStore:
    def test_get_or_create_creates_new_memory(self):
        store = MemoryStore()
        memory = store.get_or_create(123)

        assert memory.user_id == 123
        assert len(memory.messages) == 0

    def test_get_or_create_returns_existing_memory(self):
        store = MemoryStore()
        memory1 = store.get_or_create(123)
        memory1.add_exchange("q", "a")

        memory2 = store.get_or_create(123)

        assert memory2 is memory1
        assert len(memory2.messages) == 2

    def test_get_returns_none_for_unknown_user(self):
        store = MemoryStore()
        assert store.get(999) is None

    def test_clear_user_removes_memory(self):
        store = MemoryStore()
        store.get_or_create(123)
        store.clear_user(123)

        assert store.get(123) is None


class TestNLProcessor:
    @pytest.fixture
    def mock_anthropic(self):
        client = Mock()
        # Mock a simple response with no tool use
        response = Mock()
        response.stop_reason = "end_turn"
        response.content = [Mock(type="text", text="Everything looks fine!")]
        client.messages.create = Mock(return_value=response)
        return client

    @pytest.fixture
    def mock_executor(self):
        executor = AsyncMock()
        executor.execute = AsyncMock(return_value="Container: plex\nStatus: running")
        return executor

    @pytest.fixture
    def processor(self, mock_anthropic, mock_executor):
        return NLProcessor(
            anthropic_client=mock_anthropic,
            tool_executor=mock_executor,
        )

    @pytest.mark.asyncio
    async def test_process_simple_query(self, processor):
        result = await processor.process(user_id=123, message="how's everything?")
        assert result.response is not None
        assert len(result.response) > 0

    @pytest.mark.asyncio
    async def test_process_stores_in_memory(self, processor):
        await processor.process(user_id=123, message="check plex")
        memory = processor.memory_store.get(123)
        assert memory is not None
        assert len(memory.messages) == 2  # user + assistant

    @pytest.mark.asyncio
    async def test_process_uses_conversation_history(self, processor, mock_anthropic):
        # First message
        await processor.process(user_id=123, message="check plex")
        # Second message (should include history)
        await processor.process(user_id=123, message="restart it")
        # Check that the second call included history
        calls = mock_anthropic.messages.create.call_args_list
        assert len(calls) == 2
        # Second call should have more messages (history + new)
        second_call_messages = calls[1][1]["messages"]
        assert len(second_call_messages) >= 2

    @pytest.mark.asyncio
    async def test_process_returns_pending_action_for_confirmation(self, processor, mock_anthropic, mock_executor):
        # Mock tool use response
        tool_use_block = Mock(type="tool_use", id="123", name="restart_container", input={"name": "plex"})
        response1 = Mock(stop_reason="tool_use", content=[tool_use_block])
        # Mock executor returning confirmation needed
        mock_executor.execute = AsyncMock(return_value="CONFIRMATION_NEEDED:restart:plex")
        # Mock final response
        response2 = Mock(stop_reason="end_turn", content=[Mock(type="text", text="I can restart plex for you.")])
        mock_anthropic.messages.create = Mock(side_effect=[response1, response2])

        result = await processor.process(user_id=123, message="restart plex")
        assert result.pending_action is not None
        assert result.pending_action["action"] == "restart"
        assert result.pending_action["container"] == "plex"

    @pytest.mark.asyncio
    async def test_process_without_anthropic_returns_error(self):
        processor = NLProcessor(anthropic_client=None, tool_executor=Mock())
        result = await processor.process(user_id=123, message="hello")
        assert "not configured" in result.response.lower() or "not available" in result.response.lower()
