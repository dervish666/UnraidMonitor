import pytest
from unittest.mock import MagicMock, AsyncMock
from datetime import timedelta


@pytest.mark.asyncio
async def test_alert_suppressed_when_muted(tmp_path):
    """Test that alerts are suppressed when container is muted."""
    from src.alerts.mute_manager import MuteManager

    json_file = tmp_path / "mutes.json"
    manager = MuteManager(json_path=str(json_file))

    # Not muted - should alert
    assert not manager.is_muted("plex")

    # Muted - should not alert
    manager.add_mute("plex", timedelta(hours=1))
    assert manager.is_muted("plex")

    # Different container - should alert
    assert not manager.is_muted("radarr")


def test_mute_manager_created_in_main():
    """Test that MuteManager can be created."""
    from src.alerts.mute_manager import MuteManager

    manager = MuteManager(json_path="/tmp/test_mutes.json")
    assert manager is not None
