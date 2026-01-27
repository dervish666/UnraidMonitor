# tests/test_nl_processor.py
import pytest
from datetime import datetime
from src.services.nl_processor import ConversationMemory


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
