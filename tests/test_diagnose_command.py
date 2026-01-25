import pytest
from unittest.mock import MagicMock, AsyncMock


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
    mock_service.gather_context.return_value = mock_context
    mock_service.analyze = AsyncMock(return_value="Container crashed due to DB error.")

    handler = diagnose_command(state, mock_service)

    message = MagicMock()
    message.text = "/diagnose overseerr"
    message.from_user.id = 123
    message.reply_to_message = None
    message.answer = AsyncMock()

    await handler(message)

    mock_service.gather_context.assert_called_once_with("overseerr", lines=50)
    mock_service.analyze.assert_called_once()
    response = message.answer.call_args_list[-1][0][0]
    assert "Diagnosis" in response
    assert "DB error" in response or "crashed" in response


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
