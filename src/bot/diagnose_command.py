"""Diagnose command handler for AI-powered container analysis."""

import html
import logging
from typing import Callable, Awaitable

from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ChatAction

from src.state import ContainerStateManager
from src.services.diagnostic import DiagnosticService
from src.utils.formatting import extract_alert_container, safe_reply
from src.utils.telegram_format import markdown_to_telegram_html

logger = logging.getLogger(__name__)


def _extract_from_reply(reply_message: Message) -> tuple[str | None, str]:
    """Extract container name and alert context from an alert message.

    Shares one pattern list with /mute and /ignore (`utils.formatting`). The
    copy that used to live here required literal asterisks, which Telegram
    strips before handing `Message.text` back, so reply-to-alert /diagnose
    only ever worked on resource alerts.
    """
    if not reply_message or not reply_message.text:
        return None, ""

    return extract_alert_container(reply_message.text)


def diagnose_command(
    state: ContainerStateManager,
    diagnostic_service: DiagnosticService,
    max_lines: int = 500,
) -> Callable[[Message], Awaitable[None]]:
    """Factory for /diagnose command handler."""

    async def handler(message: Message) -> None:
        if not message.from_user:
            return
        text = message.text or ""
        parts = text.strip().split()
        user_id = message.from_user.id

        container_name = None
        alert_context = ""
        lines = 50

        # Check for explicit container name in command
        if len(parts) >= 2:
            container_name = parts[1]

            # Check for optional line count
            if len(parts) >= 3:
                try:
                    lines = int(parts[2])
                    lines = min(lines, max_lines)
                except ValueError:
                    pass

        # If no container name, try to extract from reply
        if not container_name and message.reply_to_message:
            container_name, alert_context = _extract_from_reply(message.reply_to_message)

        # If still no container name, show usage
        if not container_name:
            await safe_reply(
                message,
                "Usage: `/diagnose <container> [lines]`\n\n"
                "Or reply to an alert with `/diagnose`",
            )
            return

        # Find container in state
        matches = state.find_by_name(container_name)
        if not matches:
            await message.answer(f"No container found matching '{container_name}'")
            return

        if len(matches) > 1:
            names = ", ".join(m.name for m in matches)
            await safe_reply(message, f"Multiple matches found: {names}\n\n_Be more specific_")
            return

        actual_name = matches[0].name

        if message.bot:
            await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
        await message.answer(f"Analyzing {actual_name}...")

        # Gather context with alert info
        context = await diagnostic_service.gather_context(
            actual_name, lines=lines, alert_context=alert_context,
        )
        if not context:
            await message.answer(f"Could not get container info for '{actual_name}'")
            return

        # Analyze
        analysis = await diagnostic_service.analyze(context)

        # Store context for follow-up
        context.brief_summary = analysis
        diagnostic_service.store_context(user_id, context)

        # Build action buttons
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="📋 More Details", callback_data=f"diag_details:{actual_name}"),
                InlineKeyboardButton(text="🔄 Restart", callback_data=f"restart:{actual_name}"),
            ],
            [
                InlineKeyboardButton(text="📋 Logs", callback_data=f"logs:{actual_name}:50"),
            ],
        ])

        # analysis is LLM Markdown; render the whole reply as Telegram HTML.
        response = (
            f"<b>Diagnosis: {html.escape(actual_name)}</b>\n\n"
            f"{markdown_to_telegram_html(analysis)}"
        )

        await safe_reply(message, response, parse_mode="HTML", reply_markup=keyboard)

    return handler


def diag_details_callback(
    diagnostic_service: DiagnosticService,
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for diagnosis details callback handler."""

    async def handler(callback: CallbackQuery) -> None:
        if not callback.from_user:
            await callback.answer("Could not identify user")
            return

        user_id = callback.from_user.id

        if not diagnostic_service.has_pending(user_id):
            await callback.answer("No pending diagnosis. Run /diagnose first.")
            return

        await callback.answer()

        if isinstance(callback.message, Message):
            if callback.message.bot:
                await callback.message.bot.send_chat_action(chat_id=callback.message.chat.id, action=ChatAction.TYPING)

        details = await diagnostic_service.get_details(user_id)
        if details:
            response = f"<b>Detailed Analysis</b>\n\n{markdown_to_telegram_html(details)}"
            if isinstance(callback.message, Message):
                await safe_reply(callback.message, response, parse_mode="HTML")
        else:
            if callback.message:
                await callback.message.answer("Could not generate detailed analysis.")

    return handler
