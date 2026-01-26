import pytest


def test_unraid_components_can_be_created():
    """Test that Unraid components can be instantiated."""
    from src.config import UnraidConfig
    from src.alerts.server_mute_manager import ServerMuteManager

    config = UnraidConfig(enabled=True, host="192.168.1.100")
    assert config.enabled

    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".json") as f:
        manager = ServerMuteManager(json_path=f.name)
        assert manager is not None
