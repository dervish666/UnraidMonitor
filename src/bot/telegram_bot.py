import logging
from typing import Any, Awaitable, Callable

from aiogram import Bot, Dispatcher, BaseMiddleware
from aiogram.filters import Command
from aiogram.types import Message

from src.state import ContainerStateManager
from src.bot.commands import help_command, status_command

logger = logging.getLogger(__name__)


class AuthMiddleware(BaseMiddleware):
    def __init__(self, allowed_users: list[int]):
        self.allowed_users = set(allowed_users)
        super().__init__()

    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        user_id = event.from_user.id if event.from_user else None

        if user_id not in self.allowed_users:
            logger.warning(f"Unauthorized access attempt from user {user_id}")
            return None

        return await handler(event, data)


def create_auth_middleware(allowed_users: list[int]) -> AuthMiddleware:
    """Factory function for auth middleware."""
    return AuthMiddleware(allowed_users)


def create_bot(token: str) -> Bot:
    """Create Telegram bot instance."""
    return Bot(token=token)


def create_dispatcher(allowed_users: list[int]) -> Dispatcher:
    """Create dispatcher with auth middleware."""
    dp = Dispatcher()
    dp.message.middleware(AuthMiddleware(allowed_users))
    return dp


def register_commands(dp: Dispatcher, state: ContainerStateManager) -> None:
    """Register all command handlers."""
    dp.message.register(help_command(state), Command("help"))
    dp.message.register(status_command(state), Command("status"))
