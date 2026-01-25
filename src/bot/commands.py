from typing import Callable, Awaitable

from aiogram.types import Message

from src.state import ContainerStateManager


HELP_TEXT = """📋 *Available Commands*

/status - Container status overview
/status <name> - Details for specific container
/help - Show this help message

_Partial container names work: /status rad → radarr_"""


def help_command(state: ContainerStateManager) -> Callable[[Message], Awaitable[None]]:
    """Factory for /help command handler."""
    async def handler(message: Message) -> None:
        await message.answer(HELP_TEXT, parse_mode="Markdown")
    return handler
