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
