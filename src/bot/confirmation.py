from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass
class PendingConfirmation:
    """A pending confirmation waiting for user response."""
    action: str  # "restart", "stop", "start", "pull"
    container_name: str
    expires_at: datetime


class ConfirmationManager:
    """Manages pending confirmations for control commands."""

    def __init__(self, timeout_seconds: int = 60):
        self.timeout_seconds = timeout_seconds
        self._pending: dict[int, PendingConfirmation] = {}

    def request(self, user_id: int, action: str, container_name: str) -> None:
        """Store a pending confirmation for a user.

        Replaces any existing pending confirmation for this user.
        """
        expires_at = datetime.now() + timedelta(seconds=self.timeout_seconds)
        self._pending[user_id] = PendingConfirmation(
            action=action,
            container_name=container_name,
            expires_at=expires_at,
        )

    def get_pending(self, user_id: int) -> PendingConfirmation | None:
        """Get pending confirmation for user if not expired."""
        pending = self._pending.get(user_id)
        if pending is None:
            return None

        if datetime.now() > pending.expires_at:
            del self._pending[user_id]
            return None

        return pending

    def confirm(self, user_id: int) -> PendingConfirmation | None:
        """Get and clear pending confirmation if valid."""
        pending = self.get_pending(user_id)
        if pending is not None:
            del self._pending[user_id]
        return pending

    def cancel(self, user_id: int) -> bool:
        """Cancel pending confirmation for user."""
        if user_id in self._pending:
            del self._pending[user_id]
            return True
        return False
