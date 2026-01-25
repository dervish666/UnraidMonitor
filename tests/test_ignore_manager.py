import pytest
import json
from pathlib import Path


def test_ignore_manager_is_ignored_from_config():
    """Test ignoring based on config patterns."""
    from src.alerts.ignore_manager import IgnoreManager

    config_ignores = {
        "plex": ["connection timed out", "slow query"],
        "radarr": ["rate limit"],
    }

    manager = IgnoreManager(config_ignores, json_path="/tmp/test_ignores.json")

    # Substring match, case-insensitive
    assert manager.is_ignored("plex", "Error: Connection timed out after 30s")
    assert manager.is_ignored("plex", "Warning: SLOW QUERY detected")
    assert manager.is_ignored("radarr", "API rate limit exceeded")

    # Not ignored
    assert not manager.is_ignored("plex", "Database error")
    assert not manager.is_ignored("sonarr", "connection timed out")  # different container


def test_ignore_manager_is_ignored_from_json(tmp_path):
    """Test ignoring based on JSON file."""
    from src.alerts.ignore_manager import IgnoreManager

    json_file = tmp_path / "ignored_errors.json"
    json_file.write_text(json.dumps({
        "plex": ["Sqlite3 database is locked"],
    }))

    manager = IgnoreManager({}, json_path=str(json_file))

    assert manager.is_ignored("plex", "Error: Sqlite3 database is locked")
    assert not manager.is_ignored("plex", "Other error")


def test_ignore_manager_add_ignore(tmp_path):
    """Test adding runtime ignores."""
    from src.alerts.ignore_manager import IgnoreManager

    json_file = tmp_path / "ignored_errors.json"

    manager = IgnoreManager({}, json_path=str(json_file))

    # Add ignore
    result = manager.add_ignore("plex", "New error to ignore")
    assert result is True

    # Should now be ignored
    assert manager.is_ignored("plex", "New error to ignore occurred")

    # Adding same ignore again returns False
    result = manager.add_ignore("plex", "New error to ignore")
    assert result is False

    # Check file was saved
    saved = json.loads(json_file.read_text())
    assert "plex" in saved
    assert "New error to ignore" in saved["plex"]


def test_ignore_manager_get_all_ignores(tmp_path):
    """Test getting all ignores with source."""
    from src.alerts.ignore_manager import IgnoreManager

    json_file = tmp_path / "ignored_errors.json"
    json_file.write_text(json.dumps({
        "plex": ["runtime ignore"],
    }))

    config_ignores = {
        "plex": ["config ignore"],
    }

    manager = IgnoreManager(config_ignores, json_path=str(json_file))

    ignores = manager.get_all_ignores("plex")
    assert len(ignores) == 2

    sources = {msg: src for msg, src in ignores}
    assert sources["config ignore"] == "config"
    assert sources["runtime ignore"] == "runtime"


def test_ignore_manager_missing_json_file(tmp_path):
    """Test handling of missing JSON file."""
    from src.alerts.ignore_manager import IgnoreManager

    json_file = tmp_path / "nonexistent.json"

    manager = IgnoreManager({}, json_path=str(json_file))

    # Should work with empty runtime ignores
    assert not manager.is_ignored("plex", "Some error")

    # Adding should create the file
    manager.add_ignore("plex", "New ignore")
    assert json_file.exists()
