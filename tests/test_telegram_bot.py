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
