import pytest
from datetime import datetime, timedelta


def test_confirmation_manager_stores_pending():
    """Test that confirmation is stored for user."""
    from src.bot.confirmation import ConfirmationManager

    manager = ConfirmationManager(timeout_seconds=60)
    manager.request(user_id=123, action="restart", container_name="radarr")

    pending = manager.get_pending(123)
    assert pending is not None
    assert pending.action == "restart"
    assert pending.container_name == "radarr"


def test_confirmation_manager_confirm_returns_and_clears():
    """Test that confirm returns pending and clears it."""
    from src.bot.confirmation import ConfirmationManager

    manager = ConfirmationManager(timeout_seconds=60)
    manager.request(user_id=123, action="stop", container_name="sonarr")

    pending = manager.confirm(123)
    assert pending is not None
    assert pending.action == "stop"
    assert pending.container_name == "sonarr"

    # Should be cleared now
    assert manager.get_pending(123) is None
    assert manager.confirm(123) is None


def test_confirmation_manager_expired_not_returned():
    """Test that expired confirmations are not returned."""
    from src.bot.confirmation import ConfirmationManager, PendingConfirmation

    manager = ConfirmationManager(timeout_seconds=60)
    manager.request(user_id=123, action="restart", container_name="radarr")

    # Manually expire it
    manager._pending[123] = PendingConfirmation(
        action="restart",
        container_name="radarr",
        expires_at=datetime.now() - timedelta(seconds=1),
    )

    assert manager.get_pending(123) is None
    assert manager.confirm(123) is None


def test_confirmation_manager_replaces_previous():
    """Test that new request replaces previous pending."""
    from src.bot.confirmation import ConfirmationManager

    manager = ConfirmationManager(timeout_seconds=60)
    manager.request(user_id=123, action="restart", container_name="radarr")
    manager.request(user_id=123, action="stop", container_name="sonarr")

    pending = manager.get_pending(123)
    assert pending.action == "stop"
    assert pending.container_name == "sonarr"


def test_confirmation_manager_users_independent():
    """Test that different users have independent confirmations."""
    from src.bot.confirmation import ConfirmationManager

    manager = ConfirmationManager(timeout_seconds=60)
    manager.request(user_id=123, action="restart", container_name="radarr")
    manager.request(user_id=456, action="stop", container_name="sonarr")

    pending_123 = manager.get_pending(123)
    pending_456 = manager.get_pending(456)

    assert pending_123.container_name == "radarr"
    assert pending_456.container_name == "sonarr"
