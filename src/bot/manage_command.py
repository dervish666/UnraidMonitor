"""Manage command for ignores and mutes."""

import logging
from typing import Callable, Awaitable, TYPE_CHECKING

from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from src.utils.formatting import format_mute_expiry, safe_edit, escape_markdown
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


def _build_manage_keyboard() -> InlineKeyboardMarkup:
    """Build the main /manage dashboard keyboard."""
    return InlineKeyboardMarkup(
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


def _back_button() -> list[InlineKeyboardButton]:
    """Return a row with a Back button pointing to manage dashboard."""
    return [InlineKeyboardButton(text="⬅️ Back", callback_data="manage:back")]


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

        keyboard = _build_manage_keyboard()

        await message.answer(
            f"{server_info}What would you like to do?",
            reply_markup=keyboard,
            parse_mode="Markdown",
        )

    return handler


def manage_back_callback(
    system_monitor: "UnraidSystemMonitor | None" = None,
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for manage back button callback — re-renders dashboard."""

    async def handler(callback: CallbackQuery) -> None:
        await callback.answer()

        # Get brief server status if available
        server_info = ""
        if system_monitor:
            brief = await format_server_brief(system_monitor)
            if brief:
                server_info = brief + "\n\n"

        keyboard = _build_manage_keyboard()

        if callback.message:
            await safe_edit(
                callback.message,
                f"{server_info}What would you like to do?",
                reply_markup=keyboard,
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
            await safe_edit(callback.message, summary)

    return handler


def manage_resources_callback(
    resource_monitor: "ResourceMonitor | None",
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for resources button callback."""

    async def handler(callback: CallbackQuery) -> None:
        await callback.answer()

        if not resource_monitor:
            if callback.message:
                await safe_edit(callback.message, "Resource monitoring not enabled.")
            return

        summary = await format_resources_summary(resource_monitor)
        if summary:
            if callback.message:
                await safe_edit(callback.message, summary)
        else:
            if callback.message:
                await safe_edit(callback.message, "📊 No running containers found")

    return handler


def manage_server_callback(
    system_monitor: "UnraidSystemMonitor | None",
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for server button callback (shows detailed info)."""

    async def handler(callback: CallbackQuery) -> None:
        await callback.answer()

        if not system_monitor:
            if callback.message:
                await safe_edit(callback.message, "🖥️ Unraid monitoring not configured.")
            return

        response = await format_server_detailed(system_monitor)
        if response:
            if callback.message:
                await safe_edit(callback.message, response)
        else:
            if callback.message:
                await safe_edit(callback.message, "🖥️ Unraid server unavailable.")

    return handler


def manage_disks_callback(
    system_monitor: "UnraidSystemMonitor | None",
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for disks button callback."""

    async def handler(callback: CallbackQuery) -> None:
        await callback.answer()

        if not system_monitor:
            if callback.message:
                await safe_edit(callback.message, "💾 Unraid monitoring not configured.")
            return

        response = await format_disks(system_monitor)
        if response:
            if callback.message:
                await safe_edit(callback.message, response)
        else:
            if callback.message:
                await safe_edit(callback.message, "💾 Disk status unavailable.")

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
                await safe_edit(
                    callback.message,
                    "No runtime ignores configured.\n\n"
                    "Use the 🔇 Ignore Similar button on alerts or /ignore to add some.",
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

        # Add back button
        buttons.append(_back_button())

        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

        await callback.answer()
        if callback.message:
            await safe_edit(
                callback.message,
                "Select a container to manage ignores:",
                reply_markup=keyboard,
            )

    return handler


def _build_ignore_detail_keyboard(
    container: str,
    ignores: list[tuple[int, str, str | None]],
) -> tuple[str, InlineKeyboardMarkup]:
    """Build text and keyboard for ignore detail view with delete buttons."""
    lines = [f"📝 *Ignores for {escape_markdown(container)}:*\n"]
    buttons = []

    for i, (actual_index, pattern, explanation) in enumerate(ignores, 1):
        display = pattern[:60] + "..." if len(pattern) > 60 else pattern
        lines.append(f"`{i}.` {display}")
        if explanation:
            lines.append(f"    _{explanation}_")
        buttons.append([
            InlineKeyboardButton(
                text=f"❌ {i}. {display[:30]}",
                callback_data=f"mdi:{container}:{actual_index}",
            )
        ])

    buttons.append(_back_button())
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons)


def manage_ignores_container_callback(
    ignore_manager: "IgnoreManager",
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for container selection in ignore management."""

    async def handler(callback: CallbackQuery) -> None:
        data = callback.data or ""
        # Split with limit of 3 to handle container names containing colons
        parts = data.split(":", 2)
        if len(parts) < 3:
            await callback.answer("Invalid callback data")
            return

        container = parts[2]

        ignores = ignore_manager.get_runtime_ignores(container)

        if not ignores:
            await callback.answer("No ignores found")
            if callback.message:
                await safe_edit(callback.message, f"No runtime ignores for {escape_markdown(container)}.")
            return

        text, keyboard = _build_ignore_detail_keyboard(container, ignores)

        await callback.answer()
        if callback.message:
            await safe_edit(callback.message, text, reply_markup=keyboard)

    return handler


def manage_delete_ignore_callback(
    ignore_manager: "IgnoreManager",
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for delete ignore button callback (mdi:{container}:{index})."""

    async def handler(callback: CallbackQuery) -> None:
        data = callback.data or ""
        # Use rsplit to handle container names with colons: mdi:{container}:{index}
        parts = data.rsplit(":", 1)
        if len(parts) < 2:
            await callback.answer("Invalid callback data")
            return

        try:
            actual_index = int(parts[1])
        except ValueError:
            await callback.answer("Invalid callback data")
            return

        # Extract container from prefix: "mdi:{container}"
        prefix = parts[0]
        if not prefix.startswith("mdi:"):
            await callback.answer("Invalid callback data")
            return
        container = prefix[4:]  # Strip "mdi:"

        if ignore_manager.remove_runtime_ignore(container, actual_index):
            await callback.answer("Ignore removed")

            # Re-render the ignore list for this container
            ignores = ignore_manager.get_runtime_ignores(container)

            if callback.message:
                if not ignores:
                    keyboard = InlineKeyboardMarkup(inline_keyboard=[_back_button()])
                    await safe_edit(
                        callback.message,
                        f"All ignores cleared for {escape_markdown(container)}.",
                        reply_markup=keyboard,
                    )
                else:
                    text, keyboard = _build_ignore_detail_keyboard(container, ignores)
                    await safe_edit(callback.message, text, reply_markup=keyboard)
        else:
            await callback.answer("Failed to remove ignore")

    return handler


def _build_mutes_keyboard(
    mutes: list[tuple[str, str, str]],
) -> tuple[str, InlineKeyboardMarkup]:
    """Build text and keyboard for mutes view with delete buttons."""
    lines = ["🔕 *Active Mutes:*\n"]
    buttons = []

    for i, (mute_type, key, display) in enumerate(mutes, 1):
        lines.append(f"`{i}.` {display}")
        buttons.append([
            InlineKeyboardButton(
                text=f"❌ {display[:40]}",
                callback_data=f"mdm:{mute_type}:{key}",
            )
        ])

    buttons.append(_back_button())
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons)


def _collect_mutes(
    mute_manager: "MuteManager",
    server_mute_manager: "ServerMuteManager | None",
    array_mute_manager: "ArrayMuteManager | None",
) -> list[tuple[str, str, str]]:
    """Collect all active mutes as (type, key, display) tuples."""
    mutes: list[tuple[str, str, str]] = []

    # Container mutes
    for container, expiry in mute_manager.get_active_mutes():
        mutes.append(("container", container, f"{container} - {format_mute_expiry(expiry)}"))

    # Server mutes
    if server_mute_manager:
        for category, expiry in server_mute_manager.get_active_mutes():
            if category == "server":
                mutes.append(("server", "server", f"Server alerts - {format_mute_expiry(expiry)}"))

    # Array mutes
    if array_mute_manager:
        array_expiry = array_mute_manager.get_mute_expiry()
        if array_expiry:
            mutes.append(("array", "array", f"Array alerts - {format_mute_expiry(array_expiry)}"))

    return mutes


def manage_mutes_callback(
    mute_manager: "MuteManager",
    server_mute_manager: "ServerMuteManager | None",
    array_mute_manager: "ArrayMuteManager | None",
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for manage mutes button callback."""

    async def handler(callback: CallbackQuery) -> None:
        mutes = _collect_mutes(mute_manager, server_mute_manager, array_mute_manager)

        if not mutes:
            await callback.answer("No active mutes")
            if callback.message:
                await safe_edit(callback.message, "No active mutes to manage.")
            return

        text, keyboard = _build_mutes_keyboard(mutes)

        await callback.answer()
        if callback.message:
            await safe_edit(callback.message, text, reply_markup=keyboard)

    return handler


def manage_delete_mute_callback(
    mute_manager: "MuteManager",
    server_mute_manager: "ServerMuteManager | None",
    array_mute_manager: "ArrayMuteManager | None",
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for delete mute button callback (mdm:{mute_type}:{key})."""

    async def handler(callback: CallbackQuery) -> None:
        data = callback.data or ""
        # Parse mdm:{mute_type}:{key} with split(":", 2)
        parts = data.split(":", 2)
        if len(parts) < 3:
            await callback.answer("Invalid callback data")
            return

        mute_type = parts[1]
        key = parts[2]

        success = False
        label = ""

        if mute_type == "container":
            success = mute_manager.remove_mute(key)
            label = key
        elif mute_type == "server" and server_mute_manager:
            success = server_mute_manager.unmute_server()
            label = "Server alerts"
        elif mute_type == "array" and array_mute_manager:
            success = array_mute_manager.unmute_array()
            label = "Array alerts"

        if success:
            await callback.answer(f"Unmuted {label}")

            # Re-render mutes list
            mutes = _collect_mutes(mute_manager, server_mute_manager, array_mute_manager)

            if callback.message:
                if not mutes:
                    keyboard = InlineKeyboardMarkup(inline_keyboard=[_back_button()])
                    await safe_edit(
                        callback.message,
                        "All mutes cleared.",
                        reply_markup=keyboard,
                    )
                else:
                    text, keyboard = _build_mutes_keyboard(mutes)
                    await safe_edit(callback.message, text, reply_markup=keyboard)
        else:
            await callback.answer("Failed to unmute")

    return handler
