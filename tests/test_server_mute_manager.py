import pytest
from datetime import timedelta


def test_server_mute_manager_mute_all(tmp_path):
    """Test muting all server alerts."""
    from src.alerts.server_mute_manager import ServerMuteManager

    json_file = tmp_path / "server_mutes.json"
    manager = ServerMuteManager(json_path=str(json_file))

    manager.mute_server(timedelta(hours=2))

    assert manager.is_server_muted()
    assert manager.is_array_muted()
    assert manager.is_ups_muted()


def test_server_mute_manager_mute_array_only(tmp_path):
    """Test muting just array alerts."""
    from src.alerts.server_mute_manager import ServerMuteManager

    json_file = tmp_path / "server_mutes.json"
    manager = ServerMuteManager(json_path=str(json_file))

    manager.mute_array(timedelta(hours=4))

    assert not manager.is_server_muted()
    assert manager.is_array_muted()
    assert not manager.is_ups_muted()


def test_server_mute_manager_mute_ups_only(tmp_path):
    """Test muting just UPS alerts."""
    from src.alerts.server_mute_manager import ServerMuteManager

    json_file = tmp_path / "server_mutes.json"
    manager = ServerMuteManager(json_path=str(json_file))

    manager.mute_ups(timedelta(hours=1))

    assert not manager.is_server_muted()
    assert not manager.is_array_muted()
    assert manager.is_ups_muted()


def test_server_mute_manager_unmute(tmp_path):
    """Test unmuting."""
    from src.alerts.server_mute_manager import ServerMuteManager

    json_file = tmp_path / "server_mutes.json"
    manager = ServerMuteManager(json_path=str(json_file))

    manager.mute_server(timedelta(hours=2))
    assert manager.is_server_muted()

    manager.unmute_server()
    assert not manager.is_server_muted()


def test_server_mute_manager_persistence(tmp_path):
    """Test mutes persist across restarts."""
    from src.alerts.server_mute_manager import ServerMuteManager

    json_file = tmp_path / "server_mutes.json"

    manager1 = ServerMuteManager(json_path=str(json_file))
    manager1.mute_array(timedelta(hours=4))

    manager2 = ServerMuteManager(json_path=str(json_file))
    assert manager2.is_array_muted()


def test_server_mute_manager_get_active_mutes(tmp_path):
    """Test getting active mutes list."""
    from src.alerts.server_mute_manager import ServerMuteManager

    json_file = tmp_path / "server_mutes.json"
    manager = ServerMuteManager(json_path=str(json_file))

    manager.mute_server(timedelta(hours=2))
    manager.mute_array(timedelta(hours=4))

    mutes = manager.get_active_mutes()

    assert len(mutes) == 3  # server, array, ups (mute_server sets all 3)
    categories = {m[0] for m in mutes}
    assert "server" in categories
    assert "array" in categories
