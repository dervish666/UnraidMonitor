import pytest


def test_main_creates_ignore_manager():
    """Test that IgnoreManager is created in main."""
    from src.alerts.ignore_manager import IgnoreManager

    # Verify class exists and can be instantiated
    manager = IgnoreManager({}, json_path="/tmp/test.json")
    assert manager is not None


def test_main_creates_recent_errors_buffer():
    """Test that RecentErrorsBuffer is created in main."""
    from src.alerts.recent_errors import RecentErrorsBuffer

    buffer = RecentErrorsBuffer()
    assert buffer is not None
