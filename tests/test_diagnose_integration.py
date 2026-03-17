"""Integration tests for AI diagnostics feature."""

import pytest
from unittest.mock import MagicMock, AsyncMock

from src.services.llm.provider import LLMResponse


def make_mock_provider(text=""):
    """Create a mock LLM provider returning the given text."""
    provider = MagicMock()
    provider.supports_tools = False
    provider.model_name = "test-model"
    provider.provider_name = "test"
    provider.chat = AsyncMock(return_value=LLMResponse(
        text=text,
        stop_reason="end",
        tool_calls=None,
    ))
    return provider


@pytest.mark.asyncio
async def test_full_diagnose_flow():
    """Test full diagnose flow: command -> analysis -> details callback."""
    from src.state import ContainerStateManager
    from src.models import ContainerInfo
    from src.bot.diagnose_command import diagnose_command, diag_details_callback
    from src.services.diagnostic import DiagnosticService

    # Setup
    state = ContainerStateManager()
    state.update(ContainerInfo("overseerr", "exited", None, "linuxserver/overseerr:latest", None))

    mock_container = MagicMock()
    mock_container.logs.return_value = b"Error: SQLITE_BUSY"
    mock_container.attrs = {"State": {"ExitCode": 1, "StartedAt": ""}, "RestartCount": 0}
    mock_container.image.tags = ["linuxserver/overseerr:latest"]

    mock_docker = MagicMock()
    mock_docker.containers.get.return_value = mock_container

    mock_provider = make_mock_provider("Database locked. Restart MariaDB.")

    service = DiagnosticService(mock_docker, provider=mock_provider)

    # Step 1: User sends /diagnose overseerr
    diagnose_handler = diagnose_command(state, service)
    msg1 = MagicMock()
    msg1.text = "/diagnose overseerr"
    msg1.from_user.id = 123
    msg1.reply_to_message = None
    msg1.answer = AsyncMock()
    msg1.bot.send_chat_action = AsyncMock()

    await diagnose_handler(msg1)

    # Should show brief analysis with action buttons
    last_call = msg1.answer.call_args_list[-1]
    response1 = last_call[0][0]
    assert "Diagnosis" in response1

    # Should have inline keyboard with More Details button
    reply_markup = last_call.kwargs.get("reply_markup") or last_call[1].get("reply_markup")
    assert reply_markup is not None
    all_buttons = [b for row in reply_markup.inline_keyboard for b in row]
    button_texts = [b.text for b in all_buttons]
    assert any("More Details" in t for t in button_texts)

    # Step 2: User clicks "More Details" button
    mock_provider.chat = AsyncMock(return_value=LLMResponse(
        text="Detailed: The root cause is SQLite database locking...",
        stop_reason="end",
    ))

    details_handler = diag_details_callback(service)
    callback = MagicMock()
    callback.data = "diag_details:overseerr"
    callback.from_user.id = 123
    callback.answer = AsyncMock()
    callback.message = MagicMock()
    callback.message.answer = AsyncMock()
    callback.message.bot.send_chat_action = AsyncMock()

    await details_handler(callback)

    # Should show detailed analysis
    response2 = callback.message.answer.call_args[0][0]
    assert "Detailed" in response2


@pytest.mark.asyncio
async def test_diagnose_reply_to_crash_alert():
    """Test replying /diagnose to a crash alert."""
    from src.state import ContainerStateManager
    from src.models import ContainerInfo
    from src.bot.diagnose_command import diagnose_command
    from src.services.diagnostic import DiagnosticService

    state = ContainerStateManager()
    state.update(ContainerInfo("overseerr", "exited", None, "linuxserver/overseerr:latest", None))

    mock_container = MagicMock()
    mock_container.logs.return_value = b"Error: crash"
    mock_container.attrs = {"State": {"ExitCode": 1, "StartedAt": ""}, "RestartCount": 0}
    mock_container.image.tags = ["linuxserver/overseerr:latest"]

    mock_docker = MagicMock()
    mock_docker.containers.get.return_value = mock_container

    mock_provider = make_mock_provider("Analysis result.")

    service = DiagnosticService(mock_docker, provider=mock_provider)

    handler = diagnose_command(state, service)

    # Simulate reply to crash alert
    reply_msg = MagicMock()
    reply_msg.text = """🔴 *CONTAINER CRASHED:* overseerr

Exit code: 1
Image: linuxserver/overseerr:latest"""

    message = MagicMock()
    message.text = "/diagnose"
    message.from_user.id = 123
    message.reply_to_message = reply_msg
    message.answer = AsyncMock()
    message.bot.send_chat_action = AsyncMock()

    await handler(message)

    # Should extract container from reply and analyze
    response = message.answer.call_args_list[-1][0][0]
    assert "Diagnosis" in response
    assert "overseerr" in response


@pytest.mark.asyncio
async def test_diagnose_different_users_independent_contexts():
    """Test that different users have independent diagnostic contexts."""
    from src.state import ContainerStateManager
    from src.models import ContainerInfo
    from src.bot.diagnose_command import diagnose_command, diag_details_callback
    from src.services.diagnostic import DiagnosticService

    state = ContainerStateManager()
    state.update(ContainerInfo("nginx", "exited", None, "nginx:latest", None))
    state.update(ContainerInfo("redis", "exited", None, "redis:latest", None))

    mock_container = MagicMock()
    mock_container.logs.return_value = b"Error log"
    mock_container.attrs = {"State": {"ExitCode": 1, "StartedAt": ""}, "RestartCount": 0}
    mock_container.image.tags = ["image:latest"]

    mock_docker = MagicMock()
    mock_docker.containers.get.return_value = mock_container

    mock_provider = make_mock_provider("Brief analysis.")

    service = DiagnosticService(mock_docker, provider=mock_provider)

    diagnose_handler = diagnose_command(state, service)
    details_handler = diag_details_callback(service)

    # User 1 diagnoses nginx
    msg1 = MagicMock()
    msg1.text = "/diagnose nginx"
    msg1.from_user.id = 111
    msg1.reply_to_message = None
    msg1.answer = AsyncMock()
    msg1.bot.send_chat_action = AsyncMock()
    await diagnose_handler(msg1)

    # User 2 diagnoses redis
    msg2 = MagicMock()
    msg2.text = "/diagnose redis"
    msg2.from_user.id = 222
    msg2.reply_to_message = None
    msg2.answer = AsyncMock()
    msg2.bot.send_chat_action = AsyncMock()
    await diagnose_handler(msg2)

    # Both users should have pending contexts
    assert service.has_pending(111)
    assert service.has_pending(222)

    # User 1 clicks More Details
    mock_provider.chat = AsyncMock(return_value=LLMResponse(
        text="Detailed for user 1",
        stop_reason="end",
    ))
    callback1 = MagicMock()
    callback1.data = "diag_details:nginx"
    callback1.from_user.id = 111
    callback1.answer = AsyncMock()
    callback1.message = MagicMock()
    callback1.message.answer = AsyncMock()
    callback1.message.bot.send_chat_action = AsyncMock()
    await details_handler(callback1)

    # User 1 should no longer have pending context
    assert not service.has_pending(111)
    # User 2 should still have pending context
    assert service.has_pending(222)


@pytest.mark.asyncio
async def test_diagnose_no_api_key_configured():
    """Test graceful handling when AI provider is not configured."""
    from src.state import ContainerStateManager
    from src.models import ContainerInfo
    from src.bot.diagnose_command import diagnose_command
    from src.services.diagnostic import DiagnosticService

    state = ContainerStateManager()
    state.update(ContainerInfo("app", "exited", None, "app:latest", None))

    mock_container = MagicMock()
    mock_container.logs.return_value = b"Error"
    mock_container.attrs = {"State": {"ExitCode": 1, "StartedAt": ""}, "RestartCount": 0}
    mock_container.image.tags = ["app:latest"]

    mock_docker = MagicMock()
    mock_docker.containers.get.return_value = mock_container

    # No provider configured
    service = DiagnosticService(mock_docker, provider=None)

    handler = diagnose_command(state, service)

    message = MagicMock()
    message.text = "/diagnose app"
    message.from_user.id = 123
    message.reply_to_message = None
    message.answer = AsyncMock()
    message.bot.send_chat_action = AsyncMock()

    await handler(message)

    # Should still respond but indicate provider not configured
    response = message.answer.call_args_list[-1][0][0]
    assert "Diagnosis" in response
    assert "AI provider not configured" in response


@pytest.mark.asyncio
async def test_diagnose_with_custom_line_count():
    """Test /diagnose with custom log line count."""
    from src.state import ContainerStateManager
    from src.models import ContainerInfo
    from src.bot.diagnose_command import diagnose_command
    from src.services.diagnostic import DiagnosticService, DiagnosticContext

    state = ContainerStateManager()
    state.update(ContainerInfo("app", "running", None, "app:latest", None))

    mock_context = DiagnosticContext(
        container_name="app",
        logs="Long error log...",
        exit_code=None,
        image="app:latest",
        uptime_seconds=1000,
        restart_count=0,
    )

    mock_service = MagicMock(spec=DiagnosticService)
    mock_service.gather_context = AsyncMock(return_value=mock_context)
    mock_service.analyze = AsyncMock(return_value="Analysis with more logs.")

    handler = diagnose_command(state, mock_service)

    message = MagicMock()
    message.text = "/diagnose app 200"
    message.from_user.id = 123
    message.reply_to_message = None
    message.answer = AsyncMock()
    message.bot.send_chat_action = AsyncMock()

    await handler(message)

    # Should call gather_context with custom line count
    mock_service.gather_context.assert_called_once_with("app", lines=200)
