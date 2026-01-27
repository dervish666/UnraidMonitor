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

# Common timestamp patterns to strip from log lines
TIMESTAMP_PATTERNS = [
    # ISO format: 2024-01-27T10:30:45.123456Z or with timezone
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\s*",
    # Bracketed datetime: [2024-01-27 10:30:45] or [2024/01/27 10:30:45]
    r"^\[\d{4}[-/]\d{2}[-/]\d{2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?\]\s*",
    # Date time: 2024-01-27 10:30:45 or 2024/01/27 10:30:45
    r"^\d{4}[-/]\d{2}[-/]\d{2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?\s*",
    # Bracketed time only: [10:30:45]
    r"^\[\d{2}:\d{2}:\d{2}(?:\.\d+)?\]\s*",
    # Time only at start: 10:30:45
    r"^\d{2}:\d{2}:\d{2}(?:\.\d+)?\s+",
]

# Compiled pattern combining all timestamp formats
TIMESTAMP_RE = re.compile("|".join(f"({p})" for p in TIMESTAMP_PATTERNS))


def extract_pattern_from_log(log_line: str) -> str:
    """Extract meaningful pattern from log line by stripping timestamps.

    Removes common timestamp prefixes so the ignore pattern matches
    future log lines regardless of when they occur.
    """
    # Strip leading/trailing whitespace
    result = log_line.strip()

    # Remove timestamp prefix if present
    result = TIMESTAMP_RE.sub("", result)

    # Strip again in case timestamp removal left leading whitespace
    result = result.strip()

    # If we stripped everything or almost everything, use original
    if len(result) < 10 and len(log_line.strip()) > 10:
        return log_line.strip()

    return result if result else log_line.strip()


def extract_container_from_alert(text: str) -> str | None:
    """Extract container name from error alert message."""
    match = ALERT_PATTERN.search(text)
    return match.group(1) if match else None


class IgnoreSelectionState:
    """Shared state for ignore selections across handlers."""

    def __init__(self):
        self.pending_selections: dict[int, tuple[str, list[str]]] = {}

    def has_pending(self, user_id: int) -> bool:
        return user_id in self.pending_selections

    def get_pending(self, user_id: int) -> tuple[str, list[str]] | None:
        return self.pending_selections.get(user_id)

    def set_pending(self, user_id: int, container: str, errors: list[str]) -> None:
        self.pending_selections[user_id] = (container, errors)

    def clear_pending(self, user_id: int) -> None:
        if user_id in self.pending_selections:
            del self.pending_selections[user_id]


def ignore_command(
    recent_buffer: "RecentErrorsBuffer",
    ignore_manager: "IgnoreManager",
    selection_state: "IgnoreSelectionState",
) -> Callable[[Message], Awaitable[None]]:
    """Factory for /ignore command handler."""

    async def handler(message: Message) -> None:
        user_id = message.from_user.id if message.from_user else 0

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

        # Extract patterns from raw log lines
        extracted_patterns = [extract_pattern_from_log(e) for e in recent_errors]

        # Build numbered list showing the patterns that will be matched
        lines = [f"🔇 Recent errors in {container} (last 15 min):\n"]
        for i, pattern in enumerate(extracted_patterns, 1):
            # Truncate long patterns for display
            display = pattern[:80] + "..." if len(pattern) > 80 else pattern
            lines.append(f"{i}. {display}")

        lines.append("")
        lines.append("Reply with numbers to ignore (e.g. \"1,3\" or \"all\")")

        # Store extracted patterns (not raw logs) for selection
        selection_state.set_pending(user_id, container, extracted_patterns)

        await message.answer("\n".join(lines), parse_mode="Markdown")

    return handler


def ignore_selection_handler(
    ignore_manager: "IgnoreManager",
    selection_state: "IgnoreSelectionState",
) -> Callable[[Message], Awaitable[None]]:
    """Factory for ignore selection follow-up handler."""

    async def handler(message: Message) -> None:
        user_id = message.from_user.id if message.from_user else 0

        if not selection_state.has_pending(user_id):
            # No pending selection - don't respond
            return

        pending = selection_state.get_pending(user_id)
        if not pending:
            return

        container, errors = pending
        text = (message.text or "").strip().lower()

        # Parse the selection first, before clearing pending state
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

        # Only clear pending selection after successful parse
        selection_state.clear_pending(user_id)

        # Add ignores
        added = []
        for i in indices:
            error = errors[i]
            if ignore_manager.add_ignore(container, error):
                added.append(error)

        if added:
            lines = [f"✅ {container}: {len(added)} pattern(s) ignored\n"]
            for pattern in added:
                # Show the pattern that will be matched (truncate if very long)
                display = pattern[:80] + "..." if len(pattern) > 80 else pattern
                lines.append(f"  \"{display}\"")
            lines.append("")
            lines.append("Logs containing these strings will be hidden.")
            await message.answer("\n".join(lines))
        else:
            await message.answer("Those errors are already ignored.")

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
            await message.answer("🔇 No ignored errors configured.\n\nUse /ignore to add some.")
            return

        lines = ["🔇 Ignored Errors\n"]

        for container in sorted(all_containers):
            ignores = ignore_manager.get_all_ignores(container)
            if ignores:
                lines.append(f"{container} ({len(ignores)}):")
                for pattern, source in ignores:
                    display = pattern[:50] + "..." if len(pattern) > 50 else pattern
                    source_tag = " (config)" if source == "config" else ""
                    lines.append(f"  • {display}{source_tag}")
                lines.append("")

        lines.append("Use /ignore to add more")

        # Don't use Markdown - patterns may contain special characters
        await message.answer("\n".join(lines))

    return handler
