"""Integration tests for AI diagnostics feature."""

import pytest
from unittest.mock import MagicMock, AsyncMock


@pytest.mark.asyncio
async def test_full_diagnose_flow():
    """Test full diagnose flow: command -> analysis -> follow-up."""
    from src.state import ContainerStateManager
    from src.models import ContainerInfo
    from src.bot.diagnose_command import diagnose_command
    from src.bot.telegram_bot import create_details_handler
    from src.services.diagnostic import DiagnosticService, DiagnosticContext

    # Setup
    state = ContainerStateManager()
    state.update(ContainerInfo("overseerr", "exited", None, "linuxserver/overseerr:latest", None))

    mock_container = MagicMock()
    mock_container.logs.return_value = b"Error: SQLITE_BUSY"
    mock_container.attrs = {"State": {"ExitCode": 1, "StartedAt": ""}, "RestartCount": 0}
    mock_container.image.tags = ["linuxserver/overseerr:latest"]

    mock_docker = MagicMock()
    mock_docker.containers.get.return_value = mock_container

    mock_anthropic = MagicMock()
    mock_message = MagicMock()
    mock_message.content = [MagicMock(text="Database locked. Restart MariaDB.")]
    mock_anthropic.messages.create = MagicMock(return_value=mock_message)

    service = DiagnosticService(mock_docker, mock_anthropic)

    # Step 1: User sends /diagnose overseerr
    diagnose_handler = diagnose_command(state, service)
    msg1 = MagicMock()
    msg1.text = "/diagnose overseerr"
    msg1.from_user.id = 123
    msg1.reply_to_message = None
    msg1.answer = AsyncMock()

    await diagnose_handler(msg1)

    # Should show brief analysis
    response1 = msg1.answer.call_args_list[-1][0][0]
    assert "Diagnosis" in response1
    assert "Want more details" in response1

    # Step 2: User sends "yes"
    mock_anthropic.messages.create.return_value.content = [
        MagicMock(text="Detailed: The root cause is SQLite database locking...")
    ]

    details_handler = create_details_handler(service)
    msg2 = MagicMock()
    msg2.text = "yes"
    msg2.from_user.id = 123
    msg2.answer = AsyncMock()

    await details_handler(msg2)

    # Should show detailed analysis
    response2 = msg2.answer.call_args[0][0]
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

    mock_anthropic = MagicMock()
    mock_message = MagicMock()
    mock_message.content = [MagicMock(text="Analysis result.")]
    mock_anthropic.messages.create = MagicMock(return_value=mock_message)

    service = DiagnosticService(mock_docker, mock_anthropic)

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
    from src.bot.diagnose_command import diagnose_command
    from src.bot.telegram_bot import create_details_handler
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

    mock_anthropic = MagicMock()
    mock_message = MagicMock()
    mock_message.content = [MagicMock(text="Brief analysis.")]
    mock_anthropic.messages.create = MagicMock(return_value=mock_message)

    service = DiagnosticService(mock_docker, mock_anthropic)

    diagnose_handler = diagnose_command(state, service)
    details_handler = create_details_handler(service)

    # User 1 diagnoses nginx
    msg1 = MagicMock()
    msg1.text = "/diagnose nginx"
    msg1.from_user.id = 111
    msg1.reply_to_message = None
    msg1.answer = AsyncMock()
    await diagnose_handler(msg1)

    # User 2 diagnoses redis
    msg2 = MagicMock()
    msg2.text = "/diagnose redis"
    msg2.from_user.id = 222
    msg2.reply_to_message = None
    msg2.answer = AsyncMock()
    await diagnose_handler(msg2)

    # Both users should have pending contexts
    assert service.has_pending(111)
    assert service.has_pending(222)

    # User 1 asks for details
    mock_anthropic.messages.create.return_value.content = [
        MagicMock(text="Detailed for user 1")
    ]
    yes_msg1 = MagicMock()
    yes_msg1.text = "yes"
    yes_msg1.from_user.id = 111
    yes_msg1.answer = AsyncMock()
    await details_handler(yes_msg1)

    # User 1 should no longer have pending context
    assert not service.has_pending(111)
    # User 2 should still have pending context
    assert service.has_pending(222)


@pytest.mark.asyncio
async def test_diagnose_no_api_key_configured():
    """Test graceful handling when Anthropic API is not configured."""
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

    # No Anthropic client configured
    service = DiagnosticService(mock_docker, anthropic_client=None)

    handler = diagnose_command(state, service)

    message = MagicMock()
    message.text = "/diagnose app"
    message.from_user.id = 123
    message.reply_to_message = None
    message.answer = AsyncMock()

    await handler(message)

    # Should still respond but indicate API not configured
    response = message.answer.call_args_list[-1][0][0]
    assert "Diagnosis" in response
    assert "Anthropic API not configured" in response


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
    mock_service.gather_context.return_value = mock_context
    mock_service.analyze = AsyncMock(return_value="Analysis with more logs.")

    handler = diagnose_command(state, mock_service)

    message = MagicMock()
    message.text = "/diagnose app 200"
    message.from_user.id = 123
    message.reply_to_message = None
    message.answer = AsyncMock()

    await handler(message)

    # Should call gather_context with custom line count
    mock_service.gather_context.assert_called_once_with("app", lines=200)
