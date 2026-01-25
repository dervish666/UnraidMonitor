import logging
from typing import Any, Awaitable, Callable

from aiogram import Bot, Dispatcher, BaseMiddleware
from aiogram.filters import Command, Filter
from aiogram.types import Message
import docker

from src.state import ContainerStateManager
from src.bot.commands import help_command, status_command, logs_command
from src.bot.control_commands import (
    restart_command,
    stop_command,
    start_command,
    pull_command,
    create_confirm_handler,
)
from src.bot.confirmation import ConfirmationManager
from src.services.container_control import ContainerController

logger = logging.getLogger(__name__)


class YesFilter(Filter):
    """Filter for 'yes' confirmation messages."""

    async def __call__(self, message: Message) -> bool:
        if not message.text:
            return False
        return message.text.strip().lower() == "yes"


class AuthMiddleware(BaseMiddleware):
    def __init__(self, allowed_users: list[int], chat_id_store=None):
        self.allowed_users = set(allowed_users)
        self.chat_id_store = chat_id_store
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

        # Capture chat ID for alerts if store is provided
        if self.chat_id_store is not None and event.chat:
            self.chat_id_store.set_chat_id(event.chat.id)

        return await handler(event, data)


def create_auth_middleware(allowed_users: list[int], chat_id_store=None) -> AuthMiddleware:
    """Factory function for auth middleware."""
    return AuthMiddleware(allowed_users, chat_id_store=chat_id_store)


def create_bot(token: str) -> Bot:
    """Create Telegram bot instance."""
    return Bot(token=token)


def create_dispatcher(allowed_users: list[int], chat_id_store=None) -> Dispatcher:
    """Create dispatcher with auth middleware."""
    dp = Dispatcher()
    dp.message.middleware(AuthMiddleware(allowed_users, chat_id_store=chat_id_store))
    return dp


def register_commands(
    dp: Dispatcher,
    state: ContainerStateManager,
    docker_client: docker.DockerClient | None = None,
    protected_containers: list[str] | None = None,
) -> ConfirmationManager | None:
    """Register all command handlers.

    Returns ConfirmationManager if docker_client is provided, for use with the "yes" handler.
    """
    dp.message.register(help_command(state), Command("help"))
    dp.message.register(status_command(state), Command("status"))

    if docker_client:
        dp.message.register(logs_command(state, docker_client), Command("logs"))

        # Create controller and confirmation manager for control commands
        controller = ContainerController(docker_client, protected_containers or [])
        confirmation = ConfirmationManager()

        # Register control commands
        dp.message.register(restart_command(state, controller, confirmation), Command("restart"))
        dp.message.register(stop_command(state, controller, confirmation), Command("stop"))
        dp.message.register(start_command(state, controller, confirmation), Command("start"))
        dp.message.register(pull_command(state, controller, confirmation), Command("pull"))

        # Register "yes" handler for confirmations
        dp.message.register(
            create_confirm_handler(controller, confirmation),
            YesFilter(),
        )

        return confirmation

    return None
