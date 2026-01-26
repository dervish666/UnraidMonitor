import pytest
from datetime import datetime, timedelta


def test_parse_duration_minutes():
    """Test parsing minute durations."""
    from src.alerts.mute_manager import parse_duration

    assert parse_duration("15m") == timedelta(minutes=15)
    assert parse_duration("30m") == timedelta(minutes=30)
    assert parse_duration("1m") == timedelta(minutes=1)


def test_parse_duration_hours():
    """Test parsing hour durations."""
    from src.alerts.mute_manager import parse_duration

    assert parse_duration("2h") == timedelta(hours=2)
    assert parse_duration("24h") == timedelta(hours=24)
    assert parse_duration("1h") == timedelta(hours=1)


def test_parse_duration_invalid():
    """Test invalid duration formats."""
    from src.alerts.mute_manager import parse_duration

    assert parse_duration("abc") is None
    assert parse_duration("15") is None
    assert parse_duration("m15") is None
    assert parse_duration("") is None
    assert parse_duration("0m") is None
    assert parse_duration("-5m") is None


def test_mute_manager_is_muted(tmp_path):
    """Test checking if container is muted."""
    from src.alerts.mute_manager import MuteManager

    json_file = tmp_path / "mutes.json"
    manager = MuteManager(json_path=str(json_file))

    # Not muted initially
    assert not manager.is_muted("plex")

    # Add mute
    manager.add_mute("plex", timedelta(hours=1))
    assert manager.is_muted("plex")


def test_mute_manager_expiry(tmp_path):
    """Test that expired mutes return False."""
    from src.alerts.mute_manager import MuteManager

    json_file = tmp_path / "mutes.json"
    manager = MuteManager(json_path=str(json_file))

    # Add expired mute manually
    manager._mutes["plex"] = datetime.now() - timedelta(minutes=5)

    assert not manager.is_muted("plex")


def test_mute_manager_persistence(tmp_path):
    """Test mutes are saved and loaded."""
    from src.alerts.mute_manager import MuteManager

    json_file = tmp_path / "mutes.json"

    # Create manager and add mute
    manager1 = MuteManager(json_path=str(json_file))
    manager1.add_mute("plex", timedelta(hours=1))

    # Create new manager from same file
    manager2 = MuteManager(json_path=str(json_file))
    assert manager2.is_muted("plex")


def test_mute_manager_remove_mute(tmp_path):
    """Test removing a mute early."""
    from src.alerts.mute_manager import MuteManager

    json_file = tmp_path / "mutes.json"
    manager = MuteManager(json_path=str(json_file))

    manager.add_mute("plex", timedelta(hours=1))
    assert manager.is_muted("plex")

    result = manager.remove_mute("plex")
    assert result is True
    assert not manager.is_muted("plex")

    # Removing non-existent returns False
    result = manager.remove_mute("nonexistent")
    assert result is False


def test_mute_manager_get_active_mutes(tmp_path):
    """Test getting list of active mutes."""
    from src.alerts.mute_manager import MuteManager

    json_file = tmp_path / "mutes.json"
    manager = MuteManager(json_path=str(json_file))

    manager.add_mute("plex", timedelta(hours=1))
    manager.add_mute("radarr", timedelta(minutes=30))

    mutes = manager.get_active_mutes()
    assert len(mutes) == 2

    containers = {m[0] for m in mutes}
    assert "plex" in containers
    assert "radarr" in containers
