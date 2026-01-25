"""Diagnose command handler for AI-powered container analysis."""

import logging
import re
from typing import Callable, Awaitable

from aiogram.types import Message

from src.state import ContainerStateManager
from src.services.diagnostic import DiagnosticService

logger = logging.getLogger(__name__)

# Pattern to extract container name from crash alert
CRASH_ALERT_PATTERN = re.compile(r"\*CONTAINER CRASHED:\*\s+(\w+)")


def _extract_container_from_reply(reply_message: Message) -> str | None:
    """Extract container name from a crash alert message."""
    if not reply_message or not reply_message.text:
        return None

    match = CRASH_ALERT_PATTERN.search(reply_message.text)
    if match:
        return match.group(1)
    return None


def diagnose_command(
    state: ContainerStateManager,
    diagnostic_service: DiagnosticService,
) -> Callable[[Message], Awaitable[None]]:
    """Factory for /diagnose command handler."""

    async def handler(message: Message) -> None:
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
                    lines = min(lines, 500)  # Cap at 500
                except ValueError:
                    pass

        # If no container name, try to extract from reply
        if not container_name and message.reply_to_message:
            container_name = _extract_container_from_reply(message.reply_to_message)

        # If still no container name, show usage
        if not container_name:
            await message.answer(
                "Usage: `/diagnose <container> [lines]`\n\n"
                "Or reply to a crash alert with `/diagnose`",
                parse_mode="Markdown",
            )
            return

        # Find container in state
        matches = state.find_by_name(container_name)
        if not matches:
            await message.answer(f"No container found matching '{container_name}'")
            return

        if len(matches) > 1:
            names = ", ".join(m.name for m in matches)
            await message.answer(
                f"Multiple matches found: {names}\n\n_Be more specific_",
                parse_mode="Markdown",
            )
            return

        actual_name = matches[0].name

        await message.answer(f"Analyzing {actual_name}...")

        # Gather context
        context = diagnostic_service.gather_context(actual_name, lines=lines)
        if not context:
            await message.answer(f"Could not get container info for '{actual_name}'")
            return

        # Analyze with Claude
        analysis = await diagnostic_service.analyze(context)

        # Store context for follow-up
        context.brief_summary = analysis
        diagnostic_service.store_context(user_id, context)

        response = f"""*Diagnosis: {actual_name}*

{analysis}

_Want more details?_"""

        await message.answer(response, parse_mode="Markdown")

    return handler
