import re
import logging
from typing import Callable, Awaitable, TYPE_CHECKING

from aiogram.types import Message

if TYPE_CHECKING:
    from src.alerts.recent_errors import RecentErrorsBuffer
    from src.alerts.ignore_manager import IgnoreManager

logger = logging.getLogger(__name__)

# Pattern to extract container name from error alert
ALERT_PATTERN = re.compile(r"ERRORS IN[:\s]+(\w+)", re.IGNORECASE)


def extract_container_from_alert(text: str) -> str | None:
    """Extract container name from error alert message."""
    match = ALERT_PATTERN.search(text)
    return match.group(1) if match else None


def ignore_command(
    recent_buffer: "RecentErrorsBuffer",
    ignore_manager: "IgnoreManager",
) -> Callable[[Message], Awaitable[None]]:
    """Factory for /ignore command handler."""

    # Store pending ignore selections per user
    pending_selections: dict[int, tuple[str, list[str]]] = {}

    async def handler(message: Message) -> None:
        text = message.text or ""
        user_id = message.from_user.id if message.from_user else 0

        # Check if this is a selection response (numbers like "1,3" or "all")
        if user_id in pending_selections:
            container, errors = pending_selections[user_id]
            await handle_selection(message, container, errors, ignore_manager, pending_selections)
            return

        # Must be replying to an error alert
        if not message.reply_to_message or not message.reply_to_message.text:
            await message.answer("Reply to an error alert to ignore errors from it.")
            return

        reply_text = message.reply_to_message.text

        # Extract container from alert
        container = extract_container_from_alert(reply_text)
        if not container:
            await message.answer("Can only ignore errors from error alerts. Reply to a ⚠️ ERRORS IN message.")
            return

        # Get recent errors for this container
        recent_errors = recent_buffer.get_recent(container)

        if not recent_errors:
            await message.answer(f"No recent errors found for {container}.")
            return

        # Build numbered list
        lines = [f"🔇 *Recent errors in {container}* (last 15 min):\n"]
        for i, error in enumerate(recent_errors, 1):
            # Truncate long errors
            display = error[:80] + "..." if len(error) > 80 else error
            lines.append(f"`{i}.` {display}")

        lines.append("")
        lines.append('_Reply with numbers to ignore (e.g., "1,3" or "all")_')

        # Store pending selection
        pending_selections[user_id] = (container, recent_errors)

        await message.answer("\n".join(lines), parse_mode="Markdown")

    return handler


def ignores_command(
    ignore_manager: "IgnoreManager",
) -> Callable[[Message], Awaitable[None]]:
    """Factory for /ignores command handler."""

    async def handler(message: Message) -> None:
        # Collect all ignores across containers
        all_containers: set[str] = set()

        # Get containers from config ignores
        all_containers.update(ignore_manager._config_ignores.keys())

        # Get containers from runtime ignores
        all_containers.update(ignore_manager._runtime_ignores.keys())

        if not all_containers:
            await message.answer("🔇 No ignored errors configured.\n\n_Use /ignore to add some._", parse_mode="Markdown")
            return

        lines = ["🔇 *Ignored Errors*\n"]

        for container in sorted(all_containers):
            ignores = ignore_manager.get_all_ignores(container)
            if ignores:
                lines.append(f"*{container}* ({len(ignores)}):")
                for pattern, source in ignores:
                    display = pattern[:50] + "..." if len(pattern) > 50 else pattern
                    source_tag = " (config)" if source == "config" else ""
                    lines.append(f"  • {display}{source_tag}")
                lines.append("")

        lines.append("_Use /ignore to add more_")

        await message.answer("\n".join(lines), parse_mode="Markdown")

    return handler


async def handle_selection(
    message: Message,
    container: str,
    errors: list[str],
    ignore_manager: "IgnoreManager",
    pending_selections: dict,
) -> None:
    """Handle user's selection of errors to ignore."""
    text = (message.text or "").strip().lower()
    user_id = message.from_user.id if message.from_user else 0

    # Clear pending selection
    del pending_selections[user_id]

    if text == "all":
        indices = list(range(len(errors)))
    else:
        # Parse comma-separated numbers
        try:
            indices = [int(x.strip()) - 1 for x in text.split(",")]
            # Validate indices
            if any(i < 0 or i >= len(errors) for i in indices):
                await message.answer("Invalid selection. Numbers must be from the list.")
                return
        except ValueError:
            await message.answer("Invalid input. Use numbers like '1,3' or 'all'.")
            return

    # Add ignores
    added = []
    for i in indices:
        error = errors[i]
        if ignore_manager.add_ignore(container, error):
            added.append(error)

    if added:
        lines = [f"✅ *Ignored for {container}:*\n"]
        for error in added:
            display = error[:60] + "..." if len(error) > 60 else error
            lines.append(f"  • {display}")
        await message.answer("\n".join(lines), parse_mode="Markdown")
    else:
        await message.answer("Those errors are already ignored.")
