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

    # Check file was saved (new format stores as objects)
    saved = json.loads(json_file.read_text())
    assert "plex" in saved
    # New format stores pattern objects, not plain strings
    patterns = [p["pattern"] for p in saved["plex"]]
    assert "New error to ignore" in patterns


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

    # Now returns (pattern, source, explanation) tuples
    sources = {msg: src for msg, src, _ in ignores}
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


class TestIgnorePatternFormat:
    """Tests for the new regex pattern format with explanations."""

    def test_add_ignore_with_pattern_object(self, tmp_path):
        """Test adding an ignore pattern with regex and explanation."""
        from src.alerts.ignore_manager import IgnoreManager

        json_path = tmp_path / "ignores.json"
        manager = IgnoreManager(config_ignores={}, json_path=str(json_path))

        result = manager.add_ignore_pattern(
            container="sonarr",
            pattern="Connection refused to .* on port \\d+",
            match_type="regex",
            explanation="Connection refused errors to any host",
        )

        assert result is True
        ignores = manager.get_all_ignores("sonarr")
        assert len(ignores) == 1
        pattern, source, explanation = ignores[0]
        assert pattern == "Connection refused to .* on port \\d+"
        assert source == "runtime"
        assert explanation == "Connection refused errors to any host"

    def test_is_ignored_with_regex_pattern(self, tmp_path):
        """Test that regex patterns correctly match variations."""
        from src.alerts.ignore_manager import IgnoreManager

        json_path = tmp_path / "ignores.json"
        manager = IgnoreManager(config_ignores={}, json_path=str(json_path))

        manager.add_ignore_pattern(
            container="sonarr",
            pattern="Connection refused to .* on port \\d+",
            match_type="regex",
            explanation="Connection errors",
        )

        # Should match variations
        assert manager.is_ignored("sonarr", "Connection refused to api.example.com on port 443")
        assert manager.is_ignored("sonarr", "Connection refused to localhost on port 8080")
        assert not manager.is_ignored("sonarr", "Some other error")

    def test_backward_compatible_with_old_format(self, tmp_path):
        """Test that old string format still works as substring match."""
        from src.alerts.ignore_manager import IgnoreManager

        # Create old format file
        json_path = tmp_path / "ignores.json"
        json_path.write_text('{"sonarr": ["simple error text"]}')

        manager = IgnoreManager(config_ignores={}, json_path=str(json_path))

        # Old format should still work as substring match
        assert manager.is_ignored("sonarr", "This has simple error text in it")

    def test_add_ignore_pattern_duplicate_returns_false(self, tmp_path):
        """Test that adding duplicate pattern returns False."""
        from src.alerts.ignore_manager import IgnoreManager

        json_path = tmp_path / "ignores.json"
        manager = IgnoreManager(config_ignores={}, json_path=str(json_path))

        result1 = manager.add_ignore_pattern(
            container="sonarr",
            pattern="test pattern",
            match_type="substring",
            explanation="First add",
        )
        result2 = manager.add_ignore_pattern(
            container="sonarr",
            pattern="test pattern",
            match_type="substring",
            explanation="Duplicate add",
        )

        assert result1 is True
        assert result2 is False

    def test_old_add_ignore_still_works(self, tmp_path):
        """Test that the old add_ignore method still works for backward compatibility."""
        from src.alerts.ignore_manager import IgnoreManager

        json_path = tmp_path / "ignores.json"
        manager = IgnoreManager(config_ignores={}, json_path=str(json_path))

        # Old method should still work
        result = manager.add_ignore("plex", "old style ignore")
        assert result is True
        assert manager.is_ignored("plex", "this contains old style ignore in it")

    def test_mixed_old_and_new_format_patterns(self, tmp_path):
        """Test that old and new format patterns work together."""
        from src.alerts.ignore_manager import IgnoreManager

        # Create file with old format
        json_path = tmp_path / "ignores.json"
        json_path.write_text('{"sonarr": ["old string pattern"]}')

        manager = IgnoreManager(config_ignores={}, json_path=str(json_path))

        # Add new format pattern
        manager.add_ignore_pattern(
            container="sonarr",
            pattern="Error code: \\d{3}",
            match_type="regex",
            explanation="Any error code",
        )

        # Both should work
        assert manager.is_ignored("sonarr", "Found old string pattern here")
        assert manager.is_ignored("sonarr", "Error code: 500 occurred")
        assert not manager.is_ignored("sonarr", "Unrelated message")

    def test_get_all_ignores_returns_three_tuple(self, tmp_path):
        """Test that get_all_ignores returns (pattern, source, explanation) tuples."""
        from src.alerts.ignore_manager import IgnoreManager

        json_path = tmp_path / "ignores.json"
        config_ignores = {"plex": ["config pattern"]}
        manager = IgnoreManager(config_ignores=config_ignores, json_path=str(json_path))

        manager.add_ignore_pattern(
            container="plex",
            pattern="runtime pattern",
            match_type="substring",
            explanation="Test explanation",
        )

        ignores = manager.get_all_ignores("plex")
        assert len(ignores) == 2

        # Config ignores have no explanation
        config_ignore = [i for i in ignores if i[1] == "config"][0]
        assert config_ignore[0] == "config pattern"
        assert config_ignore[2] is None  # No explanation for config

        # Runtime ignores have explanation
        runtime_ignore = [i for i in ignores if i[1] == "runtime"][0]
        assert runtime_ignore[0] == "runtime pattern"
        assert runtime_ignore[2] == "Test explanation"
