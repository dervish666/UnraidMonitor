"""Natural language message handler for Telegram bot."""
import logging
import re
from typing import Any, Awaitable, Callable

from aiogram.filters import BaseFilter
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from src.utils.formatting import safe_reply, truncate_message
from src.utils.telegram_format import markdown_to_telegram_html

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

        # The LLM returns CommonMark Markdown; render it as Telegram HTML so
        # bold/italic/headings display instead of leaking literal '*' characters.
        # Truncate after conversion so the final HTML stays within Telegram's
        # length limit (a cut tag degrades to plain text via safe_reply).
        html_response = truncate_message(
            markdown_to_telegram_html(result.response),
            suffix="\n\n<i>(truncated)</i>",
        )
        await safe_reply(
            message, html_response, parse_mode="HTML", reply_markup=reply_markup
        )

    return handler


def create_nl_confirm_callback(processor: Any, controller: Any) -> Callable[[Any], Awaitable[None]]:
    """Create callback handler for NL confirmation buttons.

    Args:
        processor: NLProcessor instance (for memory access)
        controller: ContainerController instance

    Returns:
        Async callback handler for aiogram
    """
    async def handler(callback: Any) -> None:
        if callback.data is None or callback.from_user is None:
            return

        # Parse callback data: nl_confirm:action:container
        parts = callback.data.split(":", 2)
        if len(parts) != 3:
            await callback.answer("Invalid action")
            return

        _, action, container = parts
        user_id = callback.from_user.id

        # Validate action and container name to prevent forged callback data
        _VALID_ACTIONS = {"restart", "stop", "start", "pull"}
        if action not in _VALID_ACTIONS:
            await callback.answer("Invalid action")
            return

        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.\-]*", container):
            await callback.answer("Invalid container name")
            return

        # Clear pending action from memory
        memory = processor.memory_store.get(user_id)
        if memory:
            memory.pending_action = None

        # Check if container is protected
        if controller.is_protected(container):
            result = f"❌ {container} is a protected container and cannot be controlled via Telegram."
            if callback.message:
                await callback.message.edit_text(result)
            await callback.answer()
            return

        # Execute the action
        try:
            if action == "restart":
                result = await controller.restart(container)
            elif action == "stop":
                result = await controller.stop(container)
            elif action == "start":
                result = await controller.start(container)
            elif action == "pull":
                result = await controller.pull_and_recreate(container)
            else:
                result = f"Unknown action: {action}"
        except Exception as e:
            logger.error(f"Action {action} on {container} failed: {e}")
            result = f"Failed to {action} {container}: unexpected error"

        # Update the message
        if callback.message:
            await callback.message.edit_text(result)
        await callback.answer()

    return handler


def create_nl_cancel_callback(processor: Any) -> Callable[[Any], Awaitable[None]]:
    """Create callback handler for NL cancel buttons.

    Args:
        processor: NLProcessor instance (for memory access)

    Returns:
        Async callback handler for aiogram
    """
    async def handler(callback: Any) -> None:
        if callback.from_user is None:
            return

        user_id = callback.from_user.id

        # Clear pending action from memory
        memory = processor.memory_store.get(user_id)
        if memory:
            memory.pending_action = None

        if callback.message:
            await callback.message.edit_text("Action cancelled.")
        await callback.answer()

    return handler
