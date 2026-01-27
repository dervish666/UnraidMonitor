"""Natural language message handler for Telegram bot."""
import logging
from typing import Any, Awaitable, Callable

from aiogram.filters import BaseFilter
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

logger = logging.getLogger(__name__)


class NLFilter(BaseFilter):
    """Filter that matches non-command text messages."""

    async def __call__(self, message: Message) -> bool:
        if message.text is None:
            return False
        text = message.text.strip()
        if not text:
            return False
        if text.startswith("/"):
            return False
        return True


def create_nl_handler(processor: Any) -> Callable[[Message], Awaitable[None]]:
    """Create a message handler for natural language queries.

    Args:
        processor: NLProcessor instance

    Returns:
        Async handler function for aiogram
    """
    async def handler(message: Message) -> None:
        if message.text is None or message.from_user is None:
            return

        user_id = message.from_user.id
        text = message.text.strip()

        logger.debug(f"NL query from {user_id}: {text[:50]}...")

        result = await processor.process(user_id=user_id, message=text)

        # Build response
        reply_markup = None
        if result.pending_action:
            action = result.pending_action["action"]
            container = result.pending_action["container"]

            # Create confirmation buttons
            reply_markup = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Yes",
                        callback_data=f"nl_confirm:{action}:{container}",
                    ),
                    InlineKeyboardButton(
                        text="❌ No",
                        callback_data="nl_cancel",
                    ),
                ]
            ])

        await message.answer(result.response, reply_markup=reply_markup)

    return handler
