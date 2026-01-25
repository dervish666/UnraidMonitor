import pytest
from datetime import datetime, timedelta


def test_recent_errors_buffer_add_and_get():
    """Test adding and retrieving recent errors."""
    from src.alerts.recent_errors import RecentErrorsBuffer

    buffer = RecentErrorsBuffer(max_age_seconds=900, max_per_container=50)

    buffer.add("plex", "Connection failed")
    buffer.add("plex", "Database locked")
    buffer.add("radarr", "API timeout")

    plex_errors = buffer.get_recent("plex")
    assert len(plex_errors) == 2
    assert "Connection failed" in plex_errors
    assert "Database locked" in plex_errors

    radarr_errors = buffer.get_recent("radarr")
    assert len(radarr_errors) == 1
    assert "API timeout" in radarr_errors


def test_recent_errors_buffer_deduplicates():
    """Test that duplicate errors are kept (for counting) but get_recent returns unique."""
    from src.alerts.recent_errors import RecentErrorsBuffer

    buffer = RecentErrorsBuffer()

    buffer.add("plex", "Same error")
    buffer.add("plex", "Same error")
    buffer.add("plex", "Same error")

    errors = buffer.get_recent("plex")
    assert len(errors) == 1
    assert errors[0] == "Same error"


def test_recent_errors_buffer_expires_old():
    """Test that old errors are pruned."""
    from src.alerts.recent_errors import RecentErrorsBuffer

    buffer = RecentErrorsBuffer(max_age_seconds=60)

    # Add an error with old timestamp (manually for testing)
    buffer._errors["plex"] = []
    from src.alerts.recent_errors import RecentError
    old_time = datetime.now() - timedelta(seconds=120)
    buffer._errors["plex"].append(RecentError(message="Old error", timestamp=old_time))
    buffer._errors["plex"].append(RecentError(message="New error", timestamp=datetime.now()))

    errors = buffer.get_recent("plex")
    assert len(errors) == 1
    assert errors[0] == "New error"


def test_recent_errors_buffer_caps_at_max():
    """Test that buffer caps at max_per_container."""
    from src.alerts.recent_errors import RecentErrorsBuffer

    buffer = RecentErrorsBuffer(max_per_container=5)

    for i in range(10):
        buffer.add("plex", f"Error {i}")

    # Should only keep last 5
    errors = buffer.get_recent("plex")
    assert len(errors) == 5


def test_recent_errors_buffer_empty_container():
    """Test getting errors from unknown container."""
    from src.alerts.recent_errors import RecentErrorsBuffer

    buffer = RecentErrorsBuffer()

    errors = buffer.get_recent("unknown")
    assert errors == []
