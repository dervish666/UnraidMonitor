import re
import logging
from typing import Callable, Awaitable, TYPE_CHECKING

from aiogram.types import Message, CallbackQuery

if TYPE_CHECKING:
    from src.alerts.recent_errors import RecentErrorsBuffer
    from src.alerts.ignore_manager import IgnoreManager
    from src.analysis.pattern_analyzer import PatternAnalyzer

logger = logging.getLogger(__name__)

# Pattern to extract container name from error alert
ALERT_PATTERN = re.compile(r"ERRORS IN[:\s]+(\w+)", re.IGNORECASE)


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

        # Build numbered list
        lines = [f"🔇 *Recent errors in {container}* (last 15 min):\n"]
        for i, error in enumerate(recent_errors, 1):
            # Truncate long errors
            display = error[:80] + "..." if len(error) > 80 else error
            lines.append(f"`{i}.` {display}")

        lines.append("")
        lines.append('_Reply with numbers to ignore (e.g., "1,3" or "all")_')

        # Store pending selection
        selection_state.set_pending(user_id, container, recent_errors)

        await message.answer("\n".join(lines), parse_mode="Markdown")

    return handler


def ignore_selection_handler(
    ignore_manager: "IgnoreManager",
    selection_state: "IgnoreSelectionState",
    pattern_analyzer: "PatternAnalyzer | None" = None,
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

        # Process each selected error
        added = []
        for i in indices:
            error = errors[i]

            # Try to analyze with Haiku
            if pattern_analyzer is not None:
                result = await pattern_analyzer.analyze_error(
                    container=container,
                    error_message=error,
                    recent_logs=[],  # Could pass more context here
                )

                if result:
                    if ignore_manager.add_ignore_pattern(
                        container=container,
                        pattern=result["pattern"],
                        match_type=result["match_type"],
                        explanation=result["explanation"],
                    ):
                        added.append((result["pattern"], result["explanation"]))
                    continue

            # Fallback to simple substring
            if ignore_manager.add_ignore(container, error):
                added.append((error, ""))

        if added:
            lines = [f"✅ *Ignored for {container}:*\n"]
            for pattern, explanation in added:
                display = pattern[:60] + "..." if len(pattern) > 60 else pattern
                lines.append(f"  • `{display}`")
                if explanation:
                    lines.append(f"    _{explanation}_")
            await message.answer("\n".join(lines), parse_mode="Markdown")
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
                for pattern, source, explanation in ignores:
                    display = pattern[:50] + "..." if len(pattern) > 50 else pattern
                    source_tag = " (config)" if source == "config" else ""
                    lines.append(f"  * {display}{source_tag}")
                    if explanation:
                        lines.append(f"    ({explanation})")
                lines.append("")

        lines.append("Use /ignore to add more")

        # Don't use Markdown - patterns may contain special characters
        await message.answer("\n".join(lines))

    return handler


def ignore_similar_callback(
    ignore_manager: "IgnoreManager",
    pattern_analyzer: "PatternAnalyzer | None",
    recent_errors_buffer: "RecentErrorsBuffer",
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for ignore similar button callback handler."""

    async def handler(callback: CallbackQuery) -> None:
        data = callback.data or ""
        parts = data.split(":", 2)
        if len(parts) < 3:
            await callback.answer("Invalid callback data")
            return

        _, container, error_preview = parts

        # Get full error from recent buffer
        recent = recent_errors_buffer.get_recent(container)
        full_error = None
        for error in recent:
            if error.startswith(error_preview):
                full_error = error
                break

        if not full_error:
            full_error = error_preview

        # Analyze with Haiku if available
        if pattern_analyzer:
            result = await pattern_analyzer.analyze_error(
                container=container,
                error_message=full_error,
                recent_logs=recent,
            )

            if result:
                ignore_manager.add_ignore_pattern(
                    container=container,
                    pattern=result["pattern"],
                    match_type=result["match_type"],
                    explanation=result["explanation"],
                )
                await callback.message.answer(
                    f"✅ Ignoring: {result['explanation']}\n"
                    f"Pattern: `{result['pattern']}`",
                    parse_mode="Markdown",
                )
                await callback.answer("Pattern added")
                return

        # Fallback to substring
        ignore_manager.add_ignore(container, full_error)
        display = full_error[:60] + "..." if len(full_error) > 60 else full_error
        await callback.message.answer(
            f"✅ Ignoring: `{display}`",
            parse_mode="Markdown",
        )
        await callback.answer("Added to ignore list")

    return handler
