"""Diagnose command handler for AI-powered container analysis."""

import logging
import re
from typing import Callable, Awaitable

from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ChatAction

from src.state import ContainerStateManager
from src.services.diagnostic import DiagnosticService
from src.utils.formatting import safe_reply

logger = logging.getLogger(__name__)

# Patterns to extract container name from various alert types
_ALERT_PATTERNS = [
    re.compile(r"\*CONTAINER CRASHED:\*\s+([\w.\-]+)"),
    re.compile(r"\*ERRORS IN:\s+([\w.\-]+)\*"),
    re.compile(r"\*RESTART LOOP:\s+([\w.\-]+)\*"),
    re.compile(r"HIGH .+ USAGE[:\s]+([\w.\-]+)", re.IGNORECASE),
]


def _extract_container_from_reply(reply_message: Message) -> str | None:
    """Extract container name from any alert message."""
    if not reply_message or not reply_message.text:
        return None

    for pattern in _ALERT_PATTERNS:
        match = pattern.search(reply_message.text)
        if match:
            return match.group(1)
    return None


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
            container_name = _extract_container_from_reply(message.reply_to_message)

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

        await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
        await message.answer(f"Analyzing {actual_name}...")

        # Gather context
        context = await diagnostic_service.gather_context(actual_name, lines=lines)
        if not context:
            await message.answer(f"Could not get container info for '{actual_name}'")
            return

        # Analyze with Claude
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

        response = f"""*Diagnosis: {actual_name}*

{analysis}"""

        await safe_reply(message, response, reply_markup=keyboard)

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

        if callback.message:
            await callback.message.bot.send_chat_action(chat_id=callback.message.chat.id, action=ChatAction.TYPING)

        details = await diagnostic_service.get_details(user_id)
        if details:
            response = f"*Detailed Analysis*\n\n{details}"
            if callback.message:
                await safe_reply(callback.message, response)
        else:
            if callback.message:
                await callback.message.answer("Could not generate detailed analysis.")

    return handler
