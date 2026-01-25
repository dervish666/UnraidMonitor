import pytest
from unittest.mock import MagicMock, AsyncMock


@pytest.mark.asyncio
async def test_auth_middleware_allows_authorized_user():
    from src.bot.telegram_bot import create_auth_middleware

    middleware = create_auth_middleware(allowed_users=[123, 456])

    # Mock message from authorized user
    message = MagicMock()
    message.from_user.id = 123

    handler = AsyncMock(return_value="ok")

    result = await middleware(handler, message, {})

    handler.assert_called_once()
    assert result == "ok"


@pytest.mark.asyncio
async def test_auth_middleware_blocks_unauthorized_user():
    from src.bot.telegram_bot import create_auth_middleware

    middleware = create_auth_middleware(allowed_users=[123, 456])

    # Mock message from unauthorized user
    message = MagicMock()
    message.from_user.id = 999

    handler = AsyncMock(return_value="ok")

    result = await middleware(handler, message, {})

    handler.assert_not_called()
    assert result is None


@pytest.mark.asyncio
async def test_register_commands_adds_handlers():
    from src.bot.telegram_bot import create_dispatcher, register_commands
    from src.state import ContainerStateManager

    state = ContainerStateManager()
    dp = create_dispatcher(allowed_users=[123])

    register_commands(dp, state)

    # Check that handlers were registered
    # aiogram 3.x stores handlers in router
    message_handlers = dp.message.handlers
    assert len(message_handlers) >= 2  # /status and /help


def test_register_commands_with_resource_monitor():
    """Test register_commands accepts resource_monitor parameter."""
    from src.bot.telegram_bot import create_dispatcher, register_commands
    from src.state import ContainerStateManager
    from unittest.mock import MagicMock

    state = ContainerStateManager()
    mock_docker = MagicMock()
    mock_resource_monitor = MagicMock()

    dp = create_dispatcher([123])
    result = register_commands(
        dp,
        state,
        docker_client=mock_docker,
        protected_containers=[],
        resource_monitor=mock_resource_monitor,
    )

    # Should return tuple with confirmation manager and diagnostic service
    assert isinstance(result, tuple)
