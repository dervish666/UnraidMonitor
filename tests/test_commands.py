import pytest
from unittest.mock import MagicMock, AsyncMock


@pytest.mark.asyncio
async def test_help_command_returns_help_text():
    from src.bot.commands import help_command
    from src.state import ContainerStateManager

    state = ContainerStateManager()
    handler = help_command(state)

    message = MagicMock()
    message.answer = AsyncMock()

    await handler(message)

    message.answer.assert_called_once()
    call_args = message.answer.call_args[0][0]
    assert "/status" in call_args
    assert "/help" in call_args


@pytest.mark.asyncio
async def test_status_command_shows_summary():
    from src.bot.commands import status_command
    from src.state import ContainerStateManager
    from src.models import ContainerInfo

    state = ContainerStateManager()
    state.update(ContainerInfo("plex", "running", "healthy", "img", None))
    state.update(ContainerInfo("radarr", "running", "unhealthy", "img", None))
    state.update(ContainerInfo("backup", "exited", None, "img", None))

    handler = status_command(state)

    message = MagicMock()
    message.text = "/status"
    message.answer = AsyncMock()

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]
    assert "Running: 2" in response
    assert "Stopped: 1" in response
    assert "Unhealthy: 1" in response
    assert "backup" in response  # stopped container listed
    assert "radarr" in response  # unhealthy container listed
