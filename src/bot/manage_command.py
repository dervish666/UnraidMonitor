"""Manage command for ignores and mutes."""

import logging
from typing import Callable, Awaitable, TYPE_CHECKING

from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from src.bot.commands import format_status_summary
from src.bot.resources_command import format_resources_summary
from src.bot.unraid_commands import format_server_brief, format_server_detailed, format_disks

if TYPE_CHECKING:
    from src.alerts.ignore_manager import IgnoreManager
    from src.alerts.mute_manager import MuteManager
    from src.alerts.server_mute_manager import ServerMuteManager
    from src.alerts.array_mute_manager import ArrayMuteManager
    from src.state import ContainerStateManager
    from src.monitors.resource_monitor import ResourceMonitor
    from src.unraid.monitors.system_monitor import UnraidSystemMonitor

logger = logging.getLogger(__name__)


class ManageSelectionState:
    """Shared state for manage selections across handlers."""

    def __init__(self):
        # For ignore removal: user_id -> (container, [(index, pattern, explanation)])
        self.pending_ignore_removal: dict[int, tuple[str, list[tuple[int, str, str | None]]]] = {}
        # For mute removal: user_id -> list of (mute_type, key) where mute_type is 'container', 'server', or 'array'
        self.pending_mute_removal: dict[int, list[tuple[str, str]]] = {}

    def set_pending_ignore(
        self, user_id: int, container: str, ignores: list[tuple[int, str, str | None]]
    ) -> None:
        self.pending_ignore_removal[user_id] = (container, ignores)

    def get_pending_ignore(self, user_id: int) -> tuple[str, list[tuple[int, str, str | None]]] | None:
        return self.pending_ignore_removal.get(user_id)

    def clear_pending_ignore(self, user_id: int) -> None:
        self.pending_ignore_removal.pop(user_id, None)

    def set_pending_mute(self, user_id: int, mutes: list[tuple[str, str]]) -> None:
        self.pending_mute_removal[user_id] = mutes

    def get_pending_mute(self, user_id: int) -> list[tuple[str, str]] | None:
        return self.pending_mute_removal.get(user_id)

    def clear_pending_mute(self, user_id: int) -> None:
        self.pending_mute_removal.pop(user_id, None)

    def has_pending(self, user_id: int) -> bool:
        return user_id in self.pending_ignore_removal or user_id in self.pending_mute_removal


def manage_command(
    system_monitor: "UnraidSystemMonitor | None" = None,
) -> Callable[[Message], Awaitable[None]]:
    """Factory for /manage command handler."""

    async def handler(message: Message) -> None:
        # Get brief server status if available
        server_info = ""
        if system_monitor:
            brief = await format_server_brief(system_monitor)
            if brief:
                server_info = brief + "\n\n"

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="📊 Status", callback_data="manage:status"),
                    InlineKeyboardButton(text="📈 Resources", callback_data="manage:resources"),
                ],
                [
                    InlineKeyboardButton(text="🖥️ Server", callback_data="manage:server"),
                    InlineKeyboardButton(text="💾 Disks", callback_data="manage:disks"),
                ],
                [
                    InlineKeyboardButton(text="📝 Manage Ignores", callback_data="manage:ignores"),
                    InlineKeyboardButton(text="🔕 Manage Mutes", callback_data="manage:mutes"),
                ],
            ]
        )

        await message.answer(
            f"{server_info}What would you like to do?",
            reply_markup=keyboard,
            parse_mode="Markdown",
        )

    return handler


def manage_status_callback(
    state: "ContainerStateManager",
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for status button callback."""

    async def handler(callback: CallbackQuery) -> None:
        await callback.answer()

        summary = format_status_summary(state)
        if callback.message:
            await callback.message.answer(summary, parse_mode="Markdown")

    return handler


def manage_resources_callback(
    resource_monitor: "ResourceMonitor | None",
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for resources button callback."""

    async def handler(callback: CallbackQuery) -> None:
        await callback.answer()

        if not resource_monitor:
            if callback.message:
                await callback.message.answer("Resource monitoring not enabled.")
            return

        summary = await format_resources_summary(resource_monitor)
        if summary:
            if callback.message:
                await callback.message.answer(summary, parse_mode="Markdown")
        else:
            if callback.message:
                await callback.message.answer("📊 No running containers found")

    return handler


def manage_server_callback(
    system_monitor: "UnraidSystemMonitor | None",
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for server button callback (shows detailed info)."""

    async def handler(callback: CallbackQuery) -> None:
        await callback.answer()

        if not system_monitor:
            if callback.message:
                await callback.message.answer("🖥️ Unraid monitoring not configured.")
            return

        response = await format_server_detailed(system_monitor)
        if response:
            if callback.message:
                await callback.message.answer(response, parse_mode="Markdown")
        else:
            if callback.message:
                await callback.message.answer("🖥️ Unraid server unavailable.")

    return handler


def manage_disks_callback(
    system_monitor: "UnraidSystemMonitor | None",
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for disks button callback."""

    async def handler(callback: CallbackQuery) -> None:
        await callback.answer()

        if not system_monitor:
            if callback.message:
                await callback.message.answer("💾 Unraid monitoring not configured.")
            return

        response = await format_disks(system_monitor)
        if response:
            if callback.message:
                await callback.message.answer(response, parse_mode="Markdown")
        else:
            if callback.message:
                await callback.message.answer("💾 Disk status unavailable.")

    return handler


def manage_ignores_callback(
    ignore_manager: "IgnoreManager",
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for manage ignores button callback."""

    async def handler(callback: CallbackQuery) -> None:
        containers = ignore_manager.get_containers_with_runtime_ignores()

        if not containers:
            await callback.answer("No runtime ignores to manage")
            if callback.message:
                await callback.message.answer(
                    "No runtime ignores configured.\n\n"
                    "Use the 🔇 Ignore Similar button on alerts or /ignore to add some."
                )
            return

        # Build buttons for each container
        buttons = []
        for container in sorted(containers):
            count = len(ignore_manager.get_runtime_ignores(container))
            buttons.append([
                InlineKeyboardButton(
                    text=f"{container} ({count})",
                    callback_data=f"manage:ignores:{container}",
                )
            ])

        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

        await callback.answer()
        if callback.message:
            await callback.message.answer(
                "Select a container to manage ignores:",
                reply_markup=keyboard,
            )

    return handler


def manage_ignores_container_callback(
    ignore_manager: "IgnoreManager",
    selection_state: "ManageSelectionState",
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for container selection in ignore management."""

    async def handler(callback: CallbackQuery) -> None:
        data = callback.data or ""
        parts = data.split(":")
        if len(parts) < 3:
            await callback.answer("Invalid callback data")
            return

        container = parts[2]
        user_id = callback.from_user.id if callback.from_user else 0

        ignores = ignore_manager.get_runtime_ignores(container)

        if not ignores:
            await callback.answer("No ignores found")
            if callback.message:
                await callback.message.answer(f"No runtime ignores for {container}.")
            return

        # Build numbered list
        lines = [f"📝 *Ignores for {container}:*\n"]
        for i, (index, pattern, explanation) in enumerate(ignores, 1):
            display = pattern[:60] + "..." if len(pattern) > 60 else pattern
            lines.append(f"`{i}.` {display}")
            if explanation:
                lines.append(f"    _{explanation}_")

        lines.append("")
        lines.append("_Type a number to remove, or 'cancel' to abort._")

        # Store pending selection
        selection_state.set_pending_ignore(user_id, container, ignores)

        await callback.answer()
        if callback.message:
            await callback.message.answer("\n".join(lines), parse_mode="Markdown")

    return handler


def manage_mutes_callback(
    mute_manager: "MuteManager",
    server_mute_manager: "ServerMuteManager | None",
    array_mute_manager: "ArrayMuteManager | None",
    selection_state: "ManageSelectionState",
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for manage mutes button callback."""

    async def handler(callback: CallbackQuery) -> None:
        user_id = callback.from_user.id if callback.from_user else 0

        # Collect all mutes
        mutes: list[tuple[str, str, str]] = []  # (type, key, display)

        # Container mutes
        for container, expiry in mute_manager.get_active_mutes():
            time_str = expiry.strftime("%H:%M")
            mutes.append(("container", container, f"{container} - until {time_str}"))

        # Server mutes
        if server_mute_manager:
            for category, expiry in server_mute_manager.get_active_mutes():
                if category == "server":
                    time_str = expiry.strftime("%H:%M")
                    mutes.append(("server", "server", f"Server alerts - until {time_str}"))

        # Array mutes
        if array_mute_manager:
            expiry = array_mute_manager.get_mute_expiry()
            if expiry:
                time_str = expiry.strftime("%H:%M")
                mutes.append(("array", "array", f"Array alerts - until {time_str}"))

        if not mutes:
            await callback.answer("No active mutes")
            if callback.message:
                await callback.message.answer("No active mutes to manage.")
            return

        # Build numbered list
        lines = ["🔕 *Active Mutes:*\n"]
        for i, (mute_type, key, display) in enumerate(mutes, 1):
            lines.append(f"`{i}.` {display}")

        lines.append("")
        lines.append("_Type a number to unmute, or 'cancel' to abort._")

        # Store pending selection (just type and key, not display)
        selection_state.set_pending_mute(user_id, [(t, k) for t, k, _ in mutes])

        await callback.answer()
        if callback.message:
            await callback.message.answer("\n".join(lines), parse_mode="Markdown")

    return handler


def manage_selection_handler(
    ignore_manager: "IgnoreManager",
    mute_manager: "MuteManager",
    server_mute_manager: "ServerMuteManager | None",
    array_mute_manager: "ArrayMuteManager | None",
    selection_state: "ManageSelectionState",
) -> Callable[[Message], Awaitable[None]]:
    """Factory for manage selection follow-up handler."""

    async def handler(message: Message) -> None:
        user_id = message.from_user.id if message.from_user else 0
        text = (message.text or "").strip().lower()

        # Check for cancel
        if text == "cancel":
            selection_state.clear_pending_ignore(user_id)
            selection_state.clear_pending_mute(user_id)
            await message.answer("Cancelled.")
            return

        # Check for pending ignore removal
        pending_ignore = selection_state.get_pending_ignore(user_id)
        if pending_ignore:
            container, ignores = pending_ignore

            try:
                selection = int(text)
                if selection < 1 or selection > len(ignores):
                    await message.answer(f"Invalid selection. Choose 1-{len(ignores)} or 'cancel'.")
                    return
            except ValueError:
                await message.answer("Invalid input. Type a number or 'cancel'.")
                return

            # Get the actual index and remove
            actual_index, pattern, _ = ignores[selection - 1]
            selection_state.clear_pending_ignore(user_id)

            if ignore_manager.remove_runtime_ignore(container, actual_index):
                display = pattern[:50] + "..." if len(pattern) > 50 else pattern
                await message.answer(f"✅ Removed ignore from {container}:\n`{display}`", parse_mode="Markdown")
            else:
                await message.answer("Failed to remove ignore.")
            return

        # Check for pending mute removal
        pending_mute = selection_state.get_pending_mute(user_id)
        if pending_mute:
            try:
                selection = int(text)
                if selection < 1 or selection > len(pending_mute):
                    await message.answer(f"Invalid selection. Choose 1-{len(pending_mute)} or 'cancel'.")
                    return
            except ValueError:
                await message.answer("Invalid input. Type a number or 'cancel'.")
                return

            mute_type, key = pending_mute[selection - 1]
            selection_state.clear_pending_mute(user_id)

            # Remove the mute
            if mute_type == "container":
                if mute_manager.remove_mute(key):
                    await message.answer(f"🔔 Unmuted *{key}*", parse_mode="Markdown")
                else:
                    await message.answer("Failed to unmute.")
            elif mute_type == "server" and server_mute_manager:
                if server_mute_manager.remove_mute("server"):
                    await message.answer("🔔 *Server alerts unmuted*", parse_mode="Markdown")
                else:
                    await message.answer("Failed to unmute server alerts.")
            elif mute_type == "array" and array_mute_manager:
                if array_mute_manager.unmute():
                    await message.answer("🔔 *Array alerts unmuted*", parse_mode="Markdown")
                else:
                    await message.answer("Failed to unmute array alerts.")
            return

    return handler
