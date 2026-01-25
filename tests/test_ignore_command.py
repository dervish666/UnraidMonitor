import pytest
from unittest.mock import MagicMock, AsyncMock


@pytest.mark.asyncio
async def test_ignore_command_shows_recent_errors():
    """Test /ignore shows recent errors when replying to alert."""
    from src.bot.ignore_command import ignore_command
    from src.alerts.recent_errors import RecentErrorsBuffer
    from src.alerts.ignore_manager import IgnoreManager

    buffer = RecentErrorsBuffer()
    buffer.add("plex", "Error 1")
    buffer.add("plex", "Error 2")

    manager = IgnoreManager({}, json_path="/tmp/test.json")

    handler = ignore_command(buffer, manager)

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
    from src.bot.ignore_command import ignore_command
    from src.alerts.recent_errors import RecentErrorsBuffer
    from src.alerts.ignore_manager import IgnoreManager

    buffer = RecentErrorsBuffer()
    manager = IgnoreManager({}, json_path="/tmp/test.json")

    handler = ignore_command(buffer, manager)

    message = MagicMock()
    message.text = "/ignore"
    message.reply_to_message = None
    message.answer = AsyncMock()

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]
    assert "Reply to an error alert" in response


@pytest.mark.asyncio
async def test_ignore_command_not_error_alert():
    """Test /ignore when replying to non-error message."""
    from src.bot.ignore_command import ignore_command
    from src.alerts.recent_errors import RecentErrorsBuffer
    from src.alerts.ignore_manager import IgnoreManager

    buffer = RecentErrorsBuffer()
    manager = IgnoreManager({}, json_path="/tmp/test.json")

    handler = ignore_command(buffer, manager)

    reply_message = MagicMock()
    reply_message.text = "Hello there"

    message = MagicMock()
    message.text = "/ignore"
    message.reply_to_message = reply_message
    message.answer = AsyncMock()

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]
    assert "error alert" in response.lower()
