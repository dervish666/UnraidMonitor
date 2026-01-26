import pytest
from datetime import timedelta


def test_array_mute_manager_basic(tmp_path):
    """Test array mute manager basic functionality."""
    from src.alerts.array_mute_manager import ArrayMuteManager

    json_file = tmp_path / "array_mutes.json"
    manager = ArrayMuteManager(json_path=str(json_file))

    assert not manager.is_array_muted()

    manager.mute_array(timedelta(hours=2))
    assert manager.is_array_muted()

    manager.unmute_array()
    assert not manager.is_array_muted()


def test_array_mute_manager_persistence(tmp_path):
    """Test mute state persists across instances."""
    from src.alerts.array_mute_manager import ArrayMuteManager

    json_file = tmp_path / "array_mutes.json"

    manager1 = ArrayMuteManager(json_path=str(json_file))
    manager1.mute_array(timedelta(hours=2))

    # Create new instance - should load persisted state
    manager2 = ArrayMuteManager(json_path=str(json_file))
    assert manager2.is_array_muted()
