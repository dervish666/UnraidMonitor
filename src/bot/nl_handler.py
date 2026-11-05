"""Natural language message handler for Telegram bot."""
import html
import logging
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


_VALID_ACTIONS = frozenset({"restart", "stop", "start", "pull"})


def _message_ref(callback: Any) -> tuple[int | None, int | None]:
    msg = getattr(callback, "message", None)
    chat = getattr(msg, "chat", None)
    chat_id = getattr(chat, "id", None)
    message_id = getattr(msg, "message_id", None)
    return (
        chat_id if isinstance(chat_id, int) else None,
        message_id if isinstance(message_id, int) else None,
    )


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

        # The LLM returns CommonMark Markdown; render it as Telegram HTML so
        # bold/italic/headings display instead of leaking literal '*' characters.
        # Truncate after conversion so the final HTML stays within Telegram's
        # length limit (a cut tag degrades to plain text via safe_reply).
        html_response = truncate_message(
            markdown_to_telegram_html(result.response),
            suffix="\n\n<i>(truncated)</i>",
        )

        reply_markup = None
        pending = result.pending_action
        if pending:
            verb = pending.action.capitalize()
            # The model's prose can say anything; this line and the button are
            # written by the bot from the action that will actually run.
            html_response += (
                f"\n\n<b>Confirm:</b> {html.escape(pending.action)} "
                f"<code>{html.escape(pending.container)}</code>"
            )
            reply_markup = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=f"✅ {verb} {pending.container}"[:60],
                        callback_data=f"nl_confirm:{pending.token}",
                    ),
                    InlineKeyboardButton(
                        text="❌ Cancel",
                        callback_data=f"nl_cancel:{pending.token}",
                    ),
                ]
            ])

        sent = await safe_reply(
            message, html_response, parse_mode="HTML", reply_markup=reply_markup
        )
        sent_chat = getattr(getattr(sent, "chat", None), "id", None)
        sent_id = getattr(sent, "message_id", None)
        if pending and isinstance(sent_chat, int) and isinstance(sent_id, int):
            processor.bind_action(user_id, pending.token, sent_chat, sent_id)

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

        # nl_confirm:<token>. The token names a pending action held server
        # side; the button carries no action or container a client could forge.
        token = callback.data.split(":", 1)[1]
        chat_id, message_id = _message_ref(callback)
        pending = processor.claim_action(callback.from_user.id, token, chat_id, message_id)
        if pending is None:
            await callback.answer("This confirmation has expired or was already used.")
            return
        action, container = pending.action, pending.container
        if action not in _VALID_ACTIONS:
            await callback.answer("Invalid action")
            return

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

        data = callback.data or ""
        token = data.split(":", 1)[1] if ":" in data else ""
        chat_id, message_id = _message_ref(callback)
        if processor.claim_action(callback.from_user.id, token, chat_id, message_id) is None:
            await callback.answer("Nothing to cancel.")
            return

        if callback.message:
            await callback.message.edit_text("Action cancelled.")
        await callback.answer()

    return handler
