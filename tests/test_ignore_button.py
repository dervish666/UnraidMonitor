"""Tests for ignore similar button on alerts."""

import pytest
from unittest.mock import AsyncMock, MagicMock


class TestIgnoreSimilarButton:
    @pytest.mark.asyncio
    async def test_log_error_alert_includes_ignore_button(self):
        from src.alerts.manager import AlertManager
        from aiogram.types import InlineKeyboardMarkup

        bot = MagicMock()
        bot.send_message = AsyncMock()

        manager = AlertManager(bot, chat_id=123)

        await manager.send_log_error_alert(
            container_name="sonarr",
            error_line="Connection refused to api.example.com",
            suppressed_count=0,
        )

        # Check that inline keyboard was passed
        call_kwargs = bot.send_message.call_args[1]
        assert "reply_markup" in call_kwargs
        markup = call_kwargs["reply_markup"]
        assert isinstance(markup, InlineKeyboardMarkup)

        # Check button exists
        buttons = markup.inline_keyboard[0]
        assert any("ignore" in b.callback_data.lower() for b in buttons)

    @pytest.mark.asyncio
    async def test_ignore_button_callback_data_format(self):
        """Test callback data contains container and error preview."""
        from src.alerts.manager import AlertManager

        bot = MagicMock()
        bot.send_message = AsyncMock()

        manager = AlertManager(bot, chat_id=123)

        await manager.send_log_error_alert(
            container_name="radarr",
            error_line="Database locked error on update",
            suppressed_count=0,
        )

        call_kwargs = bot.send_message.call_args[1]
        markup = call_kwargs["reply_markup"]
        button = markup.inline_keyboard[0][0]

        # Callback data should be: ignore_similar:container:error_preview
        assert button.callback_data.startswith("ignore_similar:radarr:")
        assert "Database locked" in button.callback_data

    @pytest.mark.asyncio
    async def test_ignore_button_truncates_long_errors(self):
        """Test that long error messages are truncated in callback data."""
        from src.alerts.manager import AlertManager

        bot = MagicMock()
        bot.send_message = AsyncMock()

        manager = AlertManager(bot, chat_id=123)

        # Error longer than 50 chars
        long_error = "A" * 100

        await manager.send_log_error_alert(
            container_name="plex",
            error_line=long_error,
            suppressed_count=0,
        )

        call_kwargs = bot.send_message.call_args[1]
        markup = call_kwargs["reply_markup"]
        button = markup.inline_keyboard[0][0]

        # Callback data should be truncated (max 64 bytes for callback_data)
        assert len(button.callback_data) <= 64


class TestIgnoreSimilarCallback:
    @pytest.mark.asyncio
    async def test_callback_handler_adds_ignore(self):
        """Test callback handler adds ignore pattern."""
        from src.bot.ignore_command import ignore_similar_callback
        from src.alerts.ignore_manager import IgnoreManager
        from src.alerts.recent_errors import RecentErrorsBuffer
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            json_path = f.name

        ignore_manager = IgnoreManager({}, json_path=json_path)
        recent_buffer = RecentErrorsBuffer()
        recent_buffer.add("sonarr", "Connection refused to api.example.com")

        handler = ignore_similar_callback(ignore_manager, None, recent_buffer)

        # Create mock callback query
        callback = MagicMock()
        callback.data = "ignore_similar:sonarr:Connection refused to api.example.com"
        callback.answer = AsyncMock()
        callback.message = MagicMock()
        callback.message.answer = AsyncMock()

        await handler(callback)

        # Verify ignore was added
        assert ignore_manager.is_ignored("sonarr", "Connection refused to api.example.com")
        callback.answer.assert_called()

    @pytest.mark.asyncio
    async def test_callback_handler_with_analyzer(self):
        """Test callback handler uses pattern analyzer when available."""
        from src.bot.ignore_command import ignore_similar_callback
        from src.alerts.ignore_manager import IgnoreManager
        from src.alerts.recent_errors import RecentErrorsBuffer
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            json_path = f.name

        ignore_manager = IgnoreManager({}, json_path=json_path)
        recent_buffer = RecentErrorsBuffer()
        recent_buffer.add("sonarr", "Connection refused to api.example.com on port 443")

        # Create mock analyzer
        mock_analyzer = MagicMock()
        mock_analyzer.analyze_error = AsyncMock(return_value={
            "pattern": "Connection refused to .* on port \\d+",
            "match_type": "regex",
            "explanation": "Connection refused errors",
        })

        handler = ignore_similar_callback(ignore_manager, mock_analyzer, recent_buffer)

        callback = MagicMock()
        callback.data = "ignore_similar:sonarr:Connection refused to api.example.com on port 443"
        callback.answer = AsyncMock()
        callback.message = MagicMock()
        callback.message.answer = AsyncMock()

        await handler(callback)

        # Verify analyzer was called
        mock_analyzer.analyze_error.assert_called_once()

        # Verify regex pattern was added
        ignores = ignore_manager.get_all_ignores("sonarr")
        assert len(ignores) == 1
        assert ignores[0][0] == "Connection refused to .* on port \\d+"
        assert ignores[0][2] == "Connection refused errors"

    @pytest.mark.asyncio
    async def test_callback_handler_invalid_data(self):
        """Test callback handler handles invalid callback data."""
        from src.bot.ignore_command import ignore_similar_callback
        from src.alerts.ignore_manager import IgnoreManager
        from src.alerts.recent_errors import RecentErrorsBuffer
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            json_path = f.name

        ignore_manager = IgnoreManager({}, json_path=json_path)
        recent_buffer = RecentErrorsBuffer()

        handler = ignore_similar_callback(ignore_manager, None, recent_buffer)

        callback = MagicMock()
        callback.data = "ignore_similar:invalid"  # Missing error part
        callback.answer = AsyncMock()

        await handler(callback)

        callback.answer.assert_called_with("Invalid callback data")
