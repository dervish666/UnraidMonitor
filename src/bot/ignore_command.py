import logging
import time
from typing import Callable, Awaitable, TYPE_CHECKING

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from src.utils.formatting import extract_container_from_alert, truncate_message, safe_edit, strip_log_timestamps

if TYPE_CHECKING:
    from src.alerts.recent_errors import RecentErrorsBuffer
    from src.alerts.ignore_manager import IgnoreManager
    from src.analysis.pattern_analyzer import PatternAnalyzer

logger = logging.getLogger(__name__)

# TTL for pending selections (10 minutes)
_SELECTION_TTL = 600


class IgnoreSelectionState:
    """Shared state for ignore selections across handlers."""

    def __init__(self) -> None:
        # user_id -> (timestamp, container, errors, selected_indices)
        self.pending_selections: dict[int, tuple[float, str, list[str], set[int]]] = {}

    def has_pending(self, user_id: int) -> bool:
        entry = self.pending_selections.get(user_id)
        if entry is None:
            return False
        if time.monotonic() - entry[0] > _SELECTION_TTL:
            del self.pending_selections[user_id]
            return False
        return True

    def get_pending(self, user_id: int) -> tuple[str, list[str]] | None:
        entry = self.pending_selections.get(user_id)
        if entry is None:
            return None
        if time.monotonic() - entry[0] > _SELECTION_TTL:
            del self.pending_selections[user_id]
            return None
        return entry[1], entry[2]

    def get_selected(self, user_id: int) -> set[int]:
        """Get the set of selected indices for this user."""
        entry = self.pending_selections.get(user_id)
        if entry is None:
            return set()
        if time.monotonic() - entry[0] > _SELECTION_TTL:
            del self.pending_selections[user_id]
            return set()
        return entry[3]

    def toggle_selection(self, user_id: int, index: int) -> None:
        """Toggle a single index in the user's selection."""
        entry = self.pending_selections.get(user_id)
        if entry is None:
            return
        selected = entry[3]
        if index in selected:
            selected.discard(index)
        else:
            selected.add(index)

    def select_all(self, user_id: int) -> None:
        """Select all or deselect all (toggles between all-selected and none)."""
        entry = self.pending_selections.get(user_id)
        if entry is None:
            return
        errors = entry[2]
        selected = entry[3]
        all_indices = set(range(len(errors)))
        if selected == all_indices:
            selected.clear()
        else:
            selected.clear()
            selected.update(all_indices)

    def set_pending(self, user_id: int, container: str, errors: list[str]) -> None:
        self._cleanup()
        self.pending_selections[user_id] = (time.monotonic(), container, errors, set())

    def clear_pending(self, user_id: int) -> None:
        self.pending_selections.pop(user_id, None)

    def _cleanup(self) -> None:
        """Remove expired entries."""
        now = time.monotonic()
        expired = [uid for uid, entry in self.pending_selections.items() if now - entry[0] > _SELECTION_TTL]
        for uid in expired:
            del self.pending_selections[uid]


def _build_ignore_keyboard(errors: list[str], selected: set[int]) -> InlineKeyboardMarkup:
    """Build inline keyboard with toggle buttons for error selection.

    Toggle buttons shown in rows of 4, plus Select All, Done, and Cancel.
    """
    buttons: list[list[InlineKeyboardButton]] = []

    # Toggle buttons in rows of 4
    row: list[InlineKeyboardButton] = []
    for i, _error in enumerate(errors):
        check = "\u2611" if i in selected else "\u2610"  # ☑ or ☐
        row.append(InlineKeyboardButton(
            text=f"{check} {i + 1}",
            callback_data=f"ign_toggle:{i}",
        ))
        if len(row) == 4:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    # Select All / Deselect All
    all_selected = selected == set(range(len(errors)))
    all_label = "Deselect All" if all_selected else "Select All"
    buttons.append([InlineKeyboardButton(text=all_label, callback_data="ign_all")])

    # Done + Cancel row
    buttons.append([
        InlineKeyboardButton(text="\u2705 Ignore Selected", callback_data="ign_done"),
        InlineKeyboardButton(text="\u274c Cancel", callback_data="ign_cancel"),
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def ignore_command(
    recent_buffer: "RecentErrorsBuffer",
    ignore_manager: "IgnoreManager",
    selection_state: "IgnoreSelectionState",
) -> Callable[[Message], Awaitable[None]]:
    """Factory for /ignore command handler."""

    async def handler(message: Message) -> None:
        if not message.from_user:
            return
        user_id = message.from_user.id

        # Must be replying to an error alert
        if not message.reply_to_message or not message.reply_to_message.text:
            await message.answer("Reply to an error alert to ignore errors from it.")
            return

        reply_text = message.reply_to_message.text

        # Extract container from alert
        container = extract_container_from_alert(reply_text)
        if not container:
            await message.answer("Can only ignore errors from error alerts. Reply to a \u26a0\ufe0f ERRORS IN message.")
            return

        # Get recent errors for this container
        recent_errors = recent_buffer.get_recent(container)

        if not recent_errors:
            await message.answer(f"No recent errors found for {container}.")
            return

        # Build numbered list
        lines = [f"\U0001f507 *Recent errors in {container}* (last 15 min):\n"]
        for i, error in enumerate(recent_errors, 1):
            # Truncate long errors
            display = error[:80] + "..." if len(error) > 80 else error
            lines.append(f"`{i}.` {display}")

        # Store pending selection (with empty selected set)
        selection_state.set_pending(user_id, container, recent_errors)

        keyboard = _build_ignore_keyboard(recent_errors, set())

        await message.answer(
            "\n".join(lines),
            parse_mode="Markdown",
            reply_markup=keyboard,
        )

    return handler


def ignore_toggle_callback(
    selection_state: "IgnoreSelectionState",
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for ignore toggle button callback (ign_toggle:{index})."""

    async def handler(callback: CallbackQuery) -> None:
        user_id = callback.from_user.id if callback.from_user else 0

        if not selection_state.has_pending(user_id):
            await callback.answer("Selection expired. Use /ignore again.")
            return

        data = callback.data or ""
        parts = data.split(":", 1)
        if len(parts) < 2:
            await callback.answer("Invalid callback data")
            return

        try:
            index = int(parts[1])
        except ValueError:
            await callback.answer("Invalid callback data")
            return

        pending = selection_state.get_pending(user_id)
        if not pending:
            await callback.answer("Selection expired.")
            return

        _container, errors = pending

        if index < 0 or index >= len(errors):
            await callback.answer("Invalid selection")
            return

        selection_state.toggle_selection(user_id, index)
        selected = selection_state.get_selected(user_id)

        # Update keyboard only (don't change message text)
        keyboard = _build_ignore_keyboard(errors, selected)
        await callback.answer()
        if isinstance(callback.message, Message):
            try:
                await callback.message.edit_reply_markup(reply_markup=keyboard)
            except TelegramBadRequest:
                pass  # Ignore if message hasn't changed

    return handler


def ignore_all_callback(
    selection_state: "IgnoreSelectionState",
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for ignore select-all button callback (ign_all)."""

    async def handler(callback: CallbackQuery) -> None:
        user_id = callback.from_user.id if callback.from_user else 0

        if not selection_state.has_pending(user_id):
            await callback.answer("Selection expired. Use /ignore again.")
            return

        pending = selection_state.get_pending(user_id)
        if not pending:
            await callback.answer("Selection expired.")
            return

        _container, errors = pending

        selection_state.select_all(user_id)
        selected = selection_state.get_selected(user_id)

        keyboard = _build_ignore_keyboard(errors, selected)
        await callback.answer()
        if isinstance(callback.message, Message):
            try:
                await callback.message.edit_reply_markup(reply_markup=keyboard)
            except Exception:
                pass

    return handler


def ignore_done_callback(
    ignore_manager: "IgnoreManager",
    selection_state: "IgnoreSelectionState",
    pattern_analyzer: "PatternAnalyzer | None" = None,
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for ignore done button callback (ign_done)."""

    async def handler(callback: CallbackQuery) -> None:
        user_id = callback.from_user.id if callback.from_user else 0

        if not selection_state.has_pending(user_id):
            await callback.answer("Selection expired. Use /ignore again.")
            return

        pending = selection_state.get_pending(user_id)
        if not pending:
            await callback.answer("Selection expired.")
            return

        container, errors = pending
        selected = selection_state.get_selected(user_id)

        if not selected:
            await callback.answer("No errors selected. Toggle some first.")
            return

        # Clear pending state
        selection_state.clear_pending(user_id)

        # Process each selected error (same logic as old ignore_selection_handler)
        added: list[tuple[str, str]] = []
        for i in sorted(selected):
            error = errors[i]

            # Try to analyze with pattern analyzer
            if pattern_analyzer is not None:
                result = await pattern_analyzer.analyze_error(
                    container=container,
                    error_message=error,
                    recent_logs=[],
                )

                if result:
                    success, msg = ignore_manager.add_ignore_pattern(
                        container=container,
                        pattern=result["pattern"],
                        match_type=result["match_type"],
                        explanation=result["explanation"],
                    )
                    if success:
                        added.append((result["pattern"], result["explanation"]))
                    elif msg != "Pattern already exists":
                        logger.warning(f"Failed to add pattern for {container}: {msg}")
                    continue

            # Fallback to simple substring
            if ignore_manager.add_ignore(container, error):
                added.append((error, ""))

        if added:
            lines = [f"\u2705 *Ignored for {container}:*\n"]
            for pattern, explanation in added:
                display = pattern[:60] + "..." if len(pattern) > 60 else pattern
                lines.append(f"  \u2022 `{display}`")
                if explanation:
                    lines.append(f"    _{explanation}_")
            text = "\n".join(lines)
        else:
            text = "Those errors are already ignored."

        await callback.answer()
        if callback.message:
            await safe_edit(callback.message, text, reply_markup=None)

    return handler


def ignore_cancel_callback(
    selection_state: "IgnoreSelectionState",
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for ignore cancel button callback (ign_cancel)."""

    async def handler(callback: CallbackQuery) -> None:
        user_id = callback.from_user.id if callback.from_user else 0
        selection_state.clear_pending(user_id)
        await callback.answer()
        if callback.message:
            await safe_edit(callback.message, "Cancelled.", reply_markup=None)

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
            await message.answer("\U0001f507 No ignored errors configured.\n\nUse /ignore to add some.")
            return

        lines = ["\U0001f507 Ignored Errors\n"]

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
        await message.answer(truncate_message("\n".join(lines), suffix="\n\n(truncated)"))

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
        # The preview has timestamps stripped (done in send_log_error_alert),
        # so strip timestamps from buffer entries too when comparing
        recent = recent_errors_buffer.get_recent(container)
        full_error = None
        for error in recent:
            if strip_log_timestamps(error).startswith(error_preview):
                full_error = error
                break

        if not full_error:
            full_error = error_preview

        # Analyze with AI if available
        if pattern_analyzer:
            result = await pattern_analyzer.analyze_error(
                container=container,
                error_message=full_error,
                recent_logs=recent,
            )

            if result:
                success, msg = ignore_manager.add_ignore_pattern(
                    container=container,
                    pattern=result["pattern"],
                    match_type=result["match_type"],
                    explanation=result["explanation"],
                )
                if success:
                    if callback.message:
                        await callback.message.answer(
                            f"\u2705 Ignoring: {result['explanation']}\n"
                            f"Pattern: `{result['pattern']}`",
                            parse_mode="Markdown",
                        )
                    await callback.answer("Pattern added")
                else:
                    await callback.answer(f"Failed: {msg}")
                return

        # Fallback to substring — strip timestamps so the pattern matches
        # future errors regardless of when they occur
        pattern = strip_log_timestamps(full_error)
        ignore_manager.add_ignore(container, pattern)
        display = pattern[:60] + "..." if len(pattern) > 60 else pattern
        if callback.message:
            await callback.message.answer(
                f"\u2705 Ignoring: `{display}`",
                parse_mode="Markdown",
            )
        await callback.answer("Added to ignore list")

    return handler
