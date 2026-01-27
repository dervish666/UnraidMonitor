import pytest
from unittest.mock import MagicMock, AsyncMock


@pytest.mark.asyncio
async def test_ignore_command_shows_recent_errors():
    """Test /ignore shows recent errors when replying to alert."""
    from src.bot.ignore_command import ignore_command, IgnoreSelectionState
    from src.alerts.recent_errors import RecentErrorsBuffer
    from src.alerts.ignore_manager import IgnoreManager

    buffer = RecentErrorsBuffer()
    buffer.add("plex", "Error 1")
    buffer.add("plex", "Error 2")

    manager = IgnoreManager({}, json_path="/tmp/test.json")
    selection_state = IgnoreSelectionState()

    handler = ignore_command(buffer, manager, selection_state)

    # Create mock message replying to an alert
    reply_message = MagicMock()
    reply_message.text = "⚠️ ERRORS IN: plex\n\nFound 2 errors"

    message = MagicMock()
    message.text = "/ignore"
    message.reply_to_message = reply_message
    message.answer = AsyncMock()
    message.from_user = MagicMock()
    message.from_user.id = 123

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]

    assert "Recent errors in plex" in response
    assert "Error 1" in response
    assert "Error 2" in response


@pytest.mark.asyncio
async def test_ignore_command_no_reply():
    """Test /ignore without replying to message."""
    from src.bot.ignore_command import ignore_command, IgnoreSelectionState
    from src.alerts.recent_errors import RecentErrorsBuffer
    from src.alerts.ignore_manager import IgnoreManager

    buffer = RecentErrorsBuffer()
    manager = IgnoreManager({}, json_path="/tmp/test.json")
    selection_state = IgnoreSelectionState()

    handler = ignore_command(buffer, manager, selection_state)

    message = MagicMock()
    message.text = "/ignore"
    message.reply_to_message = None
    message.answer = AsyncMock()
    message.from_user = MagicMock()
    message.from_user.id = 123

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]
    assert "Reply to an error alert" in response


@pytest.mark.asyncio
async def test_ignore_command_not_error_alert():
    """Test /ignore when replying to non-error message."""
    from src.bot.ignore_command import ignore_command, IgnoreSelectionState
    from src.alerts.recent_errors import RecentErrorsBuffer
    from src.alerts.ignore_manager import IgnoreManager

    buffer = RecentErrorsBuffer()
    manager = IgnoreManager({}, json_path="/tmp/test.json")
    selection_state = IgnoreSelectionState()

    handler = ignore_command(buffer, manager, selection_state)

    reply_message = MagicMock()
    reply_message.text = "Hello there"

    message = MagicMock()
    message.text = "/ignore"
    message.reply_to_message = reply_message
    message.answer = AsyncMock()
    message.from_user = MagicMock()
    message.from_user.id = 123

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]
    assert "error alert" in response.lower()


@pytest.mark.asyncio
async def test_ignore_selection_saves_ignore():
    """Test that selecting a number saves the ignore."""
    from src.bot.ignore_command import ignore_selection_handler, IgnoreSelectionState
    from src.alerts.ignore_manager import IgnoreManager
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        json_path = f.name

    manager = IgnoreManager({}, json_path=json_path)
    selection_state = IgnoreSelectionState()

    # Set up pending selection
    selection_state.set_pending(123, "plex", ["Error message 1", "Error message 2"])

    handler = ignore_selection_handler(manager, selection_state)

    message = MagicMock()
    message.text = "1"
    message.answer = AsyncMock()
    message.from_user = MagicMock()
    message.from_user.id = 123

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]
    assert "plex" in response
    assert "ignored" in response.lower()
    assert "Error message 1" in response

    # Verify it was saved
    assert manager.is_ignored("plex", "Error message 1 happened")


@pytest.mark.asyncio
async def test_ignores_command_lists_all():
    """Test /ignores lists all ignores."""
    from src.bot.ignore_command import ignores_command
    from src.alerts.ignore_manager import IgnoreManager
    import json

    # Create manager with config and runtime ignores
    config_ignores = {"plex": ["config pattern"]}

    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({"plex": ["runtime pattern"], "radarr": ["another"]}, f)
        json_path = f.name

    manager = IgnoreManager(config_ignores, json_path=json_path)

    handler = ignores_command(manager)

    message = MagicMock()
    message.text = "/ignores"
    message.answer = AsyncMock()

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]

    assert "plex" in response
    assert "config pattern" in response
    assert "(config)" in response
    assert "runtime pattern" in response
    assert "radarr" in response


@pytest.mark.asyncio
async def test_ignores_command_empty():
    """Test /ignores with no ignores."""
    from src.bot.ignore_command import ignores_command
    from src.alerts.ignore_manager import IgnoreManager

    manager = IgnoreManager({}, json_path="/tmp/nonexistent.json")

    handler = ignores_command(manager)

    message = MagicMock()
    message.text = "/ignores"
    message.answer = AsyncMock()

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]
    assert "no ignored" in response.lower() or "No ignored" in response


def test_ignore_commands_in_help():
    """Test that /ignore and /ignores are in help text."""
    from src.bot.commands import HELP_TEXT

    assert "/ignore" in HELP_TEXT
    assert "/ignores" in HELP_TEXT


class TestExtractPatternFromLog:
    """Test timestamp stripping from log lines."""

    def test_iso_timestamp(self):
        """Test ISO format timestamps are stripped."""
        from src.bot.ignore_command import extract_pattern_from_log

        line = "2024-01-27T10:30:45.123456Z ERROR: Connection failed"
        result = extract_pattern_from_log(line)
        assert result == "ERROR: Connection failed"

    def test_iso_timestamp_with_timezone(self):
        """Test ISO format with timezone offset."""
        from src.bot.ignore_command import extract_pattern_from_log

        line = "2024-01-27T10:30:45+00:00 ERROR: Connection failed"
        result = extract_pattern_from_log(line)
        assert result == "ERROR: Connection failed"

    def test_bracketed_datetime(self):
        """Test bracketed datetime format."""
        from src.bot.ignore_command import extract_pattern_from_log

        line = "[2024-01-27 10:30:45] ERROR: Connection failed"
        result = extract_pattern_from_log(line)
        assert result == "ERROR: Connection failed"

    def test_simple_datetime(self):
        """Test simple datetime format."""
        from src.bot.ignore_command import extract_pattern_from_log

        line = "2024-01-27 10:30:45 ERROR: Connection failed"
        result = extract_pattern_from_log(line)
        assert result == "ERROR: Connection failed"

    def test_time_only(self):
        """Test time-only prefix."""
        from src.bot.ignore_command import extract_pattern_from_log

        line = "10:30:45 ERROR: Connection failed"
        result = extract_pattern_from_log(line)
        assert result == "ERROR: Connection failed"

    def test_no_timestamp(self):
        """Test line without timestamp is unchanged."""
        from src.bot.ignore_command import extract_pattern_from_log

        line = "ERROR: Connection failed to database"
        result = extract_pattern_from_log(line)
        assert result == "ERROR: Connection failed to database"

    def test_preserves_meaningful_content_if_stripped_too_short(self):
        """Test that we don't strip too much."""
        from src.bot.ignore_command import extract_pattern_from_log

        # If stripping leaves less than 10 chars, use original
        line = "2024-01-27T10:30:45Z Error"
        result = extract_pattern_from_log(line)
        # "Error" is only 5 chars, so original should be used
        assert result == "2024-01-27T10:30:45Z Error"

    def test_whitespace_handling(self):
        """Test whitespace is trimmed."""
        from src.bot.ignore_command import extract_pattern_from_log

        line = "  2024-01-27T10:30:45Z   ERROR: Connection failed  "
        result = extract_pattern_from_log(line)
        assert result == "ERROR: Connection failed"
