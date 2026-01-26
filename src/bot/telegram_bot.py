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
from src.bot.diagnose_command import diagnose_command
from src.bot.ignore_command import ignore_command, ignores_command, ignore_selection_handler, IgnoreSelectionState
from src.bot.mute_command import mute_command, mutes_command, unmute_command
from src.bot.resources_command import resources_command
from src.bot.unraid_commands import (
    server_command,
    mute_server_command,
    unmute_server_command,
    array_command,
    disks_command,
    mute_array_command,
    unmute_array_command,
)
from src.services.container_control import ContainerController
from src.services.diagnostic import DiagnosticService

logger = logging.getLogger(__name__)


class YesFilter(Filter):
    """Filter for 'yes' confirmation messages."""

    async def __call__(self, message: Message) -> bool:
        if not message.text:
            return False
        return message.text.strip().lower() == "yes"


class DetailsFilter(Filter):
    """Filter for 'yes', 'more', 'details' follow-up messages."""

    TRIGGERS = {"yes", "more", "details", "more details", "tell me more", "expand"}

    async def __call__(self, message: Message) -> bool:
        if not message.text:
            return False
        return message.text.strip().lower() in self.TRIGGERS


class IgnoreSelectionFilter(Filter):
    """Filter for ignore selection responses (numbers like '1', '1,3', or 'all')."""

    def __init__(self, selection_state: IgnoreSelectionState):
        self.selection_state = selection_state

    async def __call__(self, message: Message) -> bool:
        if not message.text:
            return False
        # Don't intercept commands - let them be processed normally
        if message.text.startswith("/"):
            return False
        user_id = message.from_user.id if message.from_user else 0
        # Only match if user has a pending selection
        return self.selection_state.has_pending(user_id)


def create_details_handler(
    diagnostic_service: DiagnosticService,
) -> Callable[[Message], Awaitable[None]]:
    """Factory for details follow-up handler."""

    async def handler(message: Message) -> None:
        user_id = message.from_user.id

        if not diagnostic_service.has_pending(user_id):
            # No pending context - don't respond (might be unrelated)
            return

        details = await diagnostic_service.get_details(user_id)
        if details:
            response = f"*Detailed Analysis*\n\n{details}"
            await message.answer(response, parse_mode="Markdown")

    return handler


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
    anthropic_client: Any | None = None,
    resource_monitor: Any | None = None,
    ignore_manager: Any | None = None,
    recent_errors_buffer: Any | None = None,
    mute_manager: Any | None = None,
    unraid_system_monitor: Any | None = None,
    server_mute_manager: Any | None = None,
    array_mute_manager: Any | None = None,
) -> tuple[ConfirmationManager | None, DiagnosticService | None]:
    """Register all command handlers.

    Returns tuple of (ConfirmationManager, DiagnosticService) if docker_client provided.
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

        # Set up diagnostic service
        diagnostic_service = DiagnosticService(docker_client, anthropic_client)

        dp.message.register(
            diagnose_command(state, diagnostic_service),
            Command("diagnose"),
        )

        # Register details follow-up handler
        dp.message.register(
            create_details_handler(diagnostic_service),
            DetailsFilter(),
        )

        # Register /resources command
        if resource_monitor is not None:
            dp.message.register(
                resources_command(resource_monitor),
                Command("resources"),
            )

        # Register /ignore and /ignores commands
        if ignore_manager is not None and recent_errors_buffer is not None:
            # Create shared state for ignore selections
            selection_state = IgnoreSelectionState()

            dp.message.register(
                ignore_command(recent_errors_buffer, ignore_manager, selection_state),
                Command("ignore"),
            )
            dp.message.register(
                ignores_command(ignore_manager),
                Command("ignores"),
            )
            # Register handler for selection follow-up (numbers like "1,3" or "all")
            dp.message.register(
                ignore_selection_handler(ignore_manager, selection_state),
                IgnoreSelectionFilter(selection_state),
            )

        # Register /mute, /mutes, /unmute commands
        if mute_manager is not None:
            dp.message.register(
                mute_command(state, mute_manager),
                Command("mute"),
            )
            dp.message.register(
                mutes_command(mute_manager, server_mute_manager, array_mute_manager),
                Command("mutes"),
            )
            dp.message.register(
                unmute_command(state, mute_manager),
                Command("unmute"),
            )

        # Register Unraid commands
        if unraid_system_monitor is not None:
            dp.message.register(
                server_command(unraid_system_monitor),
                Command("server"),
            )
            dp.message.register(
                array_command(unraid_system_monitor),
                Command("array"),
            )
            dp.message.register(
                disks_command(unraid_system_monitor),
                Command("disks"),
            )

        if server_mute_manager is not None:
            dp.message.register(
                mute_server_command(server_mute_manager),
                Command("mute-server"),
            )
            dp.message.register(
                unmute_server_command(server_mute_manager),
                Command("unmute-server"),
            )

        if array_mute_manager is not None:
            dp.message.register(
                mute_array_command(array_mute_manager),
                Command("mute-array"),
            )
            dp.message.register(
                unmute_array_command(array_mute_manager),
                Command("unmute-array"),
            )

        return confirmation, diagnostic_service

    return None, None
