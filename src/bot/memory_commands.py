"""Bot commands for memory management."""

import logging
from typing import Callable, Awaitable, TYPE_CHECKING

from aiogram.types import Message

if TYPE_CHECKING:
    from src.monitors.memory_monitor import MemoryMonitor

logger = logging.getLogger(__name__)


def cancel_kill_command(
    memory_monitor: "MemoryMonitor | None",
) -> Callable[[Message], Awaitable[None]]:
    """Factory for /cancel-kill command handler."""

    async def handler(message: Message) -> None:
        if memory_monitor is None:
            await message.answer("Memory management is not enabled.")
            return

        pending = memory_monitor.get_pending_kill()
        if memory_monitor.cancel_pending_kill():
            await message.answer(f"Cancelled pending kill of {pending}.")
            logger.info(f"User cancelled kill of {pending}")
        else:
            await message.answer("No pending kill to cancel.")

    return handler
