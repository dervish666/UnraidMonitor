# src/services/nl_processor.py
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class ConversationMemory:
    """Stores conversation history for a single user."""

    user_id: int
    max_exchanges: int = 5
    messages: list[dict[str, str]] = field(default_factory=list)
    last_activity: datetime = field(default_factory=lambda: datetime.now())
    pending_action: dict[str, Any] | None = None

    def add_exchange(self, user_message: str, assistant_message: str) -> None:
        """Add a user/assistant exchange, trimming old messages if needed."""
        self.messages.append({"role": "user", "content": user_message})
        self.messages.append({"role": "assistant", "content": assistant_message})
        self.last_activity = datetime.now()

        # Trim to max_exchanges (each exchange = 2 messages)
        max_messages = self.max_exchanges * 2
        if len(self.messages) > max_messages:
            self.messages = self.messages[-max_messages:]

    def get_messages(self) -> list[dict[str, str]]:
        """Return a copy of messages for use in API calls."""
        return self.messages.copy()

    def clear(self) -> None:
        """Clear all messages and pending action."""
        self.messages = []
        self.pending_action = None


class MemoryStore:
    """Stores conversation memories for all users."""

    def __init__(self, max_exchanges: int = 5):
        self._memories: dict[int, ConversationMemory] = {}
        self._max_exchanges = max_exchanges

    def get_or_create(self, user_id: int) -> ConversationMemory:
        """Get existing memory or create new one for user."""
        if user_id not in self._memories:
            self._memories[user_id] = ConversationMemory(
                user_id=user_id,
                max_exchanges=self._max_exchanges,
            )
        return self._memories[user_id]

    def get(self, user_id: int) -> ConversationMemory | None:
        """Get memory for user if it exists."""
        return self._memories.get(user_id)

    def clear_user(self, user_id: int) -> None:
        """Remove memory for a user."""
        self._memories.pop(user_id, None)
