import pytest
from unittest.mock import Mock, AsyncMock, MagicMock
from aiogram.types import Message, User, Chat
from src.bot.nl_handler import create_nl_handler, NLFilter


@pytest.fixture
def mock_message():
    message = Mock(spec=Message)
    message.text = "what's wrong with plex?"
    message.from_user = Mock(spec=User)
    message.from_user.id = 123
    message.chat = Mock(spec=Chat)
    message.chat.id = 456
    message.answer = AsyncMock()
    message.reply = AsyncMock()
    return message


@pytest.fixture
def mock_processor():
    processor = Mock()
    processor.process = AsyncMock()
    return processor


class TestNLFilter:
    @pytest.mark.asyncio
    async def test_filter_passes_non_command_text(self, mock_message):
        filter = NLFilter()
        mock_message.text = "what's wrong with plex?"
        result = await filter(mock_message)
        assert result is True

    @pytest.mark.asyncio
    async def test_filter_rejects_commands(self, mock_message):
        filter = NLFilter()
        mock_message.text = "/status"
        result = await filter(mock_message)
        assert result is False

    @pytest.mark.asyncio
    async def test_filter_rejects_empty_text(self, mock_message):
        filter = NLFilter()
        mock_message.text = None
        result = await filter(mock_message)
        assert result is False

    @pytest.mark.asyncio
    async def test_filter_rejects_whitespace_only(self, mock_message):
        filter = NLFilter()
        mock_message.text = "   "
        result = await filter(mock_message)
        assert result is False

    @pytest.mark.asyncio
    async def test_filter_rejects_empty_string(self, mock_message):
        filter = NLFilter()
        mock_message.text = ""
        result = await filter(mock_message)
        assert result is False


class TestNLHandler:
    @pytest.mark.asyncio
    async def test_handler_calls_processor(self, mock_message, mock_processor):
        from src.services.nl_processor import ProcessResult
        mock_processor.process.return_value = ProcessResult(response="All good!")

        handler = create_nl_handler(mock_processor)
        await handler(mock_message)

        mock_processor.process.assert_called_once_with(
            user_id=123,
            message="what's wrong with plex?",
        )

    @pytest.mark.asyncio
    async def test_handler_sends_response(self, mock_message, mock_processor):
        from src.services.nl_processor import ProcessResult
        mock_processor.process.return_value = ProcessResult(response="Everything is fine!")

        handler = create_nl_handler(mock_processor)
        await handler(mock_message)

        mock_message.answer.assert_called()
        call_text = mock_message.answer.call_args[0][0]
        assert "Everything is fine" in call_text

    @pytest.mark.asyncio
    async def test_handler_adds_confirmation_buttons_when_pending(self, mock_message, mock_processor):
        from src.services.nl_processor import ProcessResult
        mock_processor.process.return_value = ProcessResult(
            response="I can restart plex for you.",
            pending_action={"action": "restart", "container": "plex"},
        )

        handler = create_nl_handler(mock_processor)
        await handler(mock_message)

        # Check that reply_markup was passed
        call_kwargs = mock_message.answer.call_args[1]
        assert "reply_markup" in call_kwargs
