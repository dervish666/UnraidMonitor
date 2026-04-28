import pytest
from unittest.mock import MagicMock, AsyncMock

from aiogram.types import Message


@pytest.mark.asyncio
async def test_diagnose_command_with_container_name():
    """Test /diagnose with explicit container name."""
    from src.bot.diagnose_command import diagnose_command
    from src.state import ContainerStateManager
    from src.models import ContainerInfo
    from src.services.diagnostic import DiagnosticService, DiagnosticContext

    state = ContainerStateManager()
    state.update(ContainerInfo("overseerr", "exited", None, "linuxserver/overseerr:latest", None))

    mock_context = DiagnosticContext(
        container_name="overseerr",
        logs="Error log",
        exit_code=1,
        image="linuxserver/overseerr:latest",
        uptime_seconds=3600,
        restart_count=0,
    )

    mock_service = MagicMock(spec=DiagnosticService)
    mock_service.gather_context = AsyncMock(return_value=mock_context)
    mock_service.analyze = AsyncMock(return_value="Container crashed due to DB error.")

    handler = diagnose_command(state, mock_service)

    message = MagicMock()
    message.text = "/diagnose overseerr"
    message.from_user.id = 123
    message.reply_to_message = None
    message.answer = AsyncMock()
    message.bot.send_chat_action = AsyncMock()

    await handler(message)

    mock_service.gather_context.assert_called_once_with("overseerr", lines=50)
    mock_service.analyze.assert_called_once()

    # Last call should have reply_markup with action buttons
    last_call = message.answer.call_args_list[-1]
    response = last_call[0][0]
    assert "Diagnosis" in response
    assert "DB error" in response or "crashed" in response

    # Should have inline keyboard with More Details, Restart, Logs
    reply_markup = last_call.kwargs.get("reply_markup") or (last_call[1].get("reply_markup") if len(last_call) > 1 else None)
    assert reply_markup is not None
    all_buttons = [b for row in reply_markup.inline_keyboard for b in row]
    button_texts = [b.text for b in all_buttons]
    assert any("More Details" in t for t in button_texts)
    assert any("Restart" in t for t in button_texts)
    assert any("Logs" in t for t in button_texts)


@pytest.mark.asyncio
async def test_diagnose_command_container_not_found():
    """Test /diagnose with non-existent container."""
    from src.bot.diagnose_command import diagnose_command
    from src.state import ContainerStateManager
    from src.services.diagnostic import DiagnosticService

    state = ContainerStateManager()

    mock_service = MagicMock(spec=DiagnosticService)

    handler = diagnose_command(state, mock_service)

    message = MagicMock()
    message.text = "/diagnose nonexistent"
    message.from_user.id = 123
    message.reply_to_message = None
    message.answer = AsyncMock()

    await handler(message)

    response = message.answer.call_args[0][0]
    assert "No container found" in response


@pytest.mark.asyncio
async def test_diagnose_extracts_from_errors_in_alert():
    """Test /diagnose extracts container from ERRORS IN alert."""
    from src.bot.diagnose_command import _extract_container_from_reply

    reply = MagicMock()
    reply.text = "⚠️ *ERRORS IN: overseerr*\n\nRecent errors detected..."
    assert _extract_container_from_reply(reply) == "overseerr"


@pytest.mark.asyncio
async def test_diagnose_extracts_from_restart_loop_alert():
    """Test /diagnose extracts container from RESTART LOOP alert."""
    from src.bot.diagnose_command import _extract_container_from_reply

    reply = MagicMock()
    reply.text = "🔄 *RESTART LOOP: plex*\n\n3 restarts in 5 minutes"
    assert _extract_container_from_reply(reply) == "plex"


@pytest.mark.asyncio
async def test_diagnose_extracts_from_crash_alert():
    """Test /diagnose extracts container from CRASHED alert."""
    from src.bot.diagnose_command import _extract_container_from_reply

    reply = MagicMock()
    reply.text = "🔴 *CONTAINER CRASHED:* overseerr\n\nExit code: 1"
    assert _extract_container_from_reply(reply) == "overseerr"


@pytest.mark.asyncio
async def test_diag_details_callback():
    """Test diag_details callback returns detailed analysis."""
    from src.bot.diagnose_command import diag_details_callback
    from src.services.diagnostic import DiagnosticService

    mock_service = MagicMock(spec=DiagnosticService)
    mock_service.has_pending.return_value = True
    mock_service.get_details = AsyncMock(return_value="Detailed: root cause is...")

    handler = diag_details_callback(mock_service)

    callback = MagicMock()
    callback.data = "diag_details:overseerr"
    callback.from_user.id = 123
    callback.answer = AsyncMock()
    callback.message = MagicMock(spec=Message)
    callback.message.answer = AsyncMock()
    callback.message.chat = MagicMock()
    callback.message.chat.id = 12345
    callback.message.bot = MagicMock()
    callback.message.bot.send_chat_action = AsyncMock()

    await handler(callback)

    mock_service.get_details.assert_called_once_with(123)
    response = callback.message.answer.call_args[0][0]
    assert "Detailed" in response


@pytest.mark.asyncio
async def test_diag_details_callback_no_pending():
    """Test diag_details callback when no pending context."""
    from src.bot.diagnose_command import diag_details_callback
    from src.services.diagnostic import DiagnosticService

    mock_service = MagicMock(spec=DiagnosticService)
    mock_service.has_pending.return_value = False

    handler = diag_details_callback(mock_service)

    callback = MagicMock()
    callback.data = "diag_details:overseerr"
    callback.from_user.id = 123
    callback.answer = AsyncMock()

    await handler(callback)

    callback.answer.assert_called_once()
    assert "No pending" in callback.answer.call_args[0][0]
