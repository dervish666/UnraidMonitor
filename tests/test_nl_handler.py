import pytest
from unittest.mock import Mock, AsyncMock, MagicMock
from aiogram.types import Message, User, Chat, CallbackQuery
from src.bot.nl_handler import create_nl_handler, NLFilter, create_nl_confirm_callback, create_nl_cancel_callback


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


@pytest.fixture
def mock_callback():
    callback = Mock(spec=CallbackQuery)
    callback.from_user = Mock(spec=User)
    callback.from_user.id = 123
    callback.message = Mock(spec=Message)
    callback.message.edit_text = AsyncMock()
    callback.answer = AsyncMock()
    callback.data = "nl_confirm:restart:plex"
    return callback


class TestNLConfirmCallback:
    @pytest.mark.asyncio
    async def test_confirm_executes_action(self, mock_callback, mock_processor):
        mock_controller = Mock()
        mock_controller.restart = AsyncMock(return_value="✅ plex restarted")

        handler = create_nl_confirm_callback(mock_processor, mock_controller)
        await handler(mock_callback)

        mock_controller.restart.assert_called_once_with("plex")

    @pytest.mark.asyncio
    async def test_confirm_updates_message(self, mock_callback, mock_processor):
        mock_controller = Mock()
        mock_controller.restart = AsyncMock(return_value="✅ plex restarted")

        handler = create_nl_confirm_callback(mock_processor, mock_controller)
        await handler(mock_callback)

        mock_callback.message.edit_text.assert_called()
        call_text = mock_callback.message.edit_text.call_args[0][0]
        assert "restarted" in call_text.lower()

    @pytest.mark.asyncio
    async def test_confirm_clears_pending_action(self, mock_callback, mock_processor):
        mock_controller = Mock()
        mock_controller.restart = AsyncMock(return_value="✅ plex restarted")

        # Set up pending action in memory
        from src.services.nl_processor import MemoryStore
        mock_processor.memory_store = MemoryStore()
        memory = mock_processor.memory_store.get_or_create(123)
        memory.pending_action = {"action": "restart", "container": "plex"}

        handler = create_nl_confirm_callback(mock_processor, mock_controller)
        await handler(mock_callback)

        assert memory.pending_action is None

    @pytest.mark.asyncio
    async def test_confirm_stop_action(self, mock_callback, mock_processor):
        mock_callback.data = "nl_confirm:stop:radarr"
        mock_controller = Mock()
        mock_controller.stop = AsyncMock(return_value="✅ radarr stopped")

        handler = create_nl_confirm_callback(mock_processor, mock_controller)
        await handler(mock_callback)

        mock_controller.stop.assert_called_once_with("radarr")

    @pytest.mark.asyncio
    async def test_confirm_start_action(self, mock_callback, mock_processor):
        mock_callback.data = "nl_confirm:start:sonarr"
        mock_controller = Mock()
        mock_controller.start = AsyncMock(return_value="✅ sonarr started")

        handler = create_nl_confirm_callback(mock_processor, mock_controller)
        await handler(mock_callback)

        mock_controller.start.assert_called_once_with("sonarr")

    @pytest.mark.asyncio
    async def test_confirm_pull_action(self, mock_callback, mock_processor):
        mock_callback.data = "nl_confirm:pull:jellyfin"
        mock_controller = Mock()
        mock_controller.pull_and_recreate = AsyncMock(return_value="✅ jellyfin updated")

        handler = create_nl_confirm_callback(mock_processor, mock_controller)
        await handler(mock_callback)

        mock_controller.pull_and_recreate.assert_called_once_with("jellyfin")

    @pytest.mark.asyncio
    async def test_confirm_unknown_action(self, mock_callback, mock_processor):
        mock_callback.data = "nl_confirm:unknown:container"
        mock_controller = Mock()

        handler = create_nl_confirm_callback(mock_processor, mock_controller)
        await handler(mock_callback)

        mock_callback.message.edit_text.assert_called()
        call_text = mock_callback.message.edit_text.call_args[0][0]
        assert "unknown" in call_text.lower()

    @pytest.mark.asyncio
    async def test_confirm_invalid_callback_data(self, mock_callback, mock_processor):
        mock_callback.data = "invalid_format"
        mock_controller = Mock()

        handler = create_nl_confirm_callback(mock_processor, mock_controller)
        await handler(mock_callback)

        mock_callback.answer.assert_called_with("Invalid action")

    @pytest.mark.asyncio
    async def test_confirm_null_callback_data(self, mock_callback, mock_processor):
        mock_callback.data = None
        mock_controller = Mock()

        handler = create_nl_confirm_callback(mock_processor, mock_controller)
        await handler(mock_callback)

        # Should return early without calling anything
        mock_callback.answer.assert_not_called()

    @pytest.mark.asyncio
    async def test_confirm_null_from_user(self, mock_callback, mock_processor):
        mock_callback.from_user = None
        mock_controller = Mock()

        handler = create_nl_confirm_callback(mock_processor, mock_controller)
        await handler(mock_callback)

        # Should return early without calling anything
        mock_callback.answer.assert_not_called()


class TestNLCancelCallback:
    @pytest.mark.asyncio
    async def test_cancel_updates_message(self, mock_callback, mock_processor):
        mock_callback.data = "nl_cancel"

        handler = create_nl_cancel_callback(mock_processor)
        await handler(mock_callback)

        mock_callback.message.edit_text.assert_called()
        call_text = mock_callback.message.edit_text.call_args[0][0]
        assert "cancel" in call_text.lower()

    @pytest.mark.asyncio
    async def test_cancel_clears_pending_action(self, mock_callback, mock_processor):
        mock_callback.data = "nl_cancel"

        # Set up pending action in memory
        from src.services.nl_processor import MemoryStore
        mock_processor.memory_store = MemoryStore()
        memory = mock_processor.memory_store.get_or_create(123)
        memory.pending_action = {"action": "restart", "container": "plex"}

        handler = create_nl_cancel_callback(mock_processor)
        await handler(mock_callback)

        assert memory.pending_action is None

    @pytest.mark.asyncio
    async def test_cancel_answers_callback(self, mock_callback, mock_processor):
        mock_callback.data = "nl_cancel"

        handler = create_nl_cancel_callback(mock_processor)
        await handler(mock_callback)

        mock_callback.answer.assert_called()

    @pytest.mark.asyncio
    async def test_cancel_null_from_user(self, mock_callback, mock_processor):
        mock_callback.from_user = None

        handler = create_nl_cancel_callback(mock_processor)
        await handler(mock_callback)

        # Should return early without calling anything
        mock_callback.answer.assert_not_called()
