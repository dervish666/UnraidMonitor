"""Manage command for ignores and mutes."""

import logging
from typing import Any, Callable, Awaitable, TYPE_CHECKING

from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from src.constants import (
    NOTIFICATION_IMPORTANCE_LEVELS,
    UNRAID_NOTIFICATION_MIN_IMPORTANCE,
)
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
            [
                InlineKeyboardButton(text="⚙️ Features", callback_data="manage:features"),
            ],
        ]
    )


def _back_button() -> list[InlineKeyboardButton]:
    """Return a row with a Back button pointing to manage dashboard."""
    return [InlineKeyboardButton(text="⬅️ Back", callback_data="manage:back")]


def _panel_keyboard(section: str) -> InlineKeyboardMarkup:
    """Return the Refresh + Back row for a read-only /manage panel.

    Editing a message without reply_markup makes Telegram drop the keyboard
    entirely, which turns a panel into a dead end -- every leaf panel needs one.
    """
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔄 Refresh", callback_data=f"manage:{section}"),
        *_back_button(),
    ]])


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
            await safe_edit(callback.message, summary, reply_markup=_panel_keyboard("status"))

    return handler


def manage_resources_callback(
    resource_monitor: "ResourceMonitor | None",
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for resources button callback."""

    async def handler(callback: CallbackQuery) -> None:
        await callback.answer()

        keyboard = _panel_keyboard("resources")
        if not resource_monitor:
            if callback.message:
                await safe_edit(callback.message, "Resource monitoring not enabled.", reply_markup=keyboard)
            return

        summary = await format_resources_summary(resource_monitor)
        if summary:
            if callback.message:
                await safe_edit(callback.message, summary, reply_markup=keyboard)
        else:
            if callback.message:
                await safe_edit(callback.message, "📊 No running containers found", reply_markup=keyboard)

    return handler


def manage_server_callback(
    system_monitor: "UnraidSystemMonitor | None",
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for server button callback (shows detailed info)."""

    async def handler(callback: CallbackQuery) -> None:
        await callback.answer()

        keyboard = _panel_keyboard("server")
        if not system_monitor:
            if callback.message:
                await safe_edit(callback.message, "🖥️ Unraid monitoring not configured.", reply_markup=keyboard)
            return

        response = await format_server_detailed(system_monitor)
        if response:
            if callback.message:
                await safe_edit(callback.message, response, reply_markup=keyboard)
        else:
            if callback.message:
                await safe_edit(callback.message, "🖥️ Unraid server unavailable.", reply_markup=keyboard)

    return handler


def manage_disks_callback(
    system_monitor: "UnraidSystemMonitor | None",
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for disks button callback."""

    async def handler(callback: CallbackQuery) -> None:
        await callback.answer()

        keyboard = _panel_keyboard("disks")
        if not system_monitor:
            if callback.message:
                await safe_edit(callback.message, "💾 Unraid monitoring not configured.", reply_markup=keyboard)
            return

        response = await format_disks(system_monitor)
        if response:
            if callback.message:
                await safe_edit(callback.message, response, reply_markup=keyboard)
        else:
            if callback.message:
                await safe_edit(callback.message, "💾 Disk status unavailable.", reply_markup=keyboard)

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


# ---------------------------------------------------------------------------
# Features section — enable/configure optional monitors
# ---------------------------------------------------------------------------


class ContainerSelectionState:
    """Per-user container selections while a Features picker is open.

    Shared by the auto-heal and memory-restart pickers (one instance each).
    Mirrors the wizard's in-memory session pattern: selections are seeded from
    the current config when the picker opens and discarded once saved.
    """

    def __init__(self) -> None:
        self._selections: dict[int, set[str]] = {}

    def init(self, user_id: int, containers: list[str]) -> None:
        self._selections[user_id] = set(containers)

    def get(self, user_id: int) -> set[str]:
        return self._selections.setdefault(user_id, set())

    def toggle(self, user_id: int, container: str) -> None:
        selection = self.get(user_id)
        if container in selection:
            selection.discard(container)
        else:
            selection.add(container)

    def clear(self, user_id: int) -> None:
        self._selections.pop(user_id, None)


def _image_updates_state(image_update_monitor: Any) -> str:
    """One-word state label for image-update detection."""
    return "✅ On" if image_update_monitor is not None else "⚪ Off"


def _auto_heal_state(auto_heal_config: Any) -> str:
    """State label for auto-heal, including opted-in container count."""
    if auto_heal_config is not None and auto_heal_config.enabled and auto_heal_config.containers:
        return f"✅ {len(auto_heal_config.containers)} container(s)"
    return "⚪ Off"


def _memory_restart_state(memory_config: Any) -> str:
    """State label for the memory restart list, including container count."""
    if memory_config is not None and memory_config.restart_containers:
        return f"✅ {len(memory_config.restart_containers)} container(s)"
    return "⚪ Off"


def _notifications_state(unraid_config: Any) -> str:
    """State label for the Unraid notification relay, including its floor."""
    if unraid_config is not None and unraid_config.notifications_enabled:
        return f"✅ On ({unraid_config.notifications_min_importance}+)"
    return "⚪ Off"


def _ups_state(nut_config: Any, ups_monitor: Any) -> str:
    """State label for UPS monitoring, saying plainly when it cannot see a UPS."""
    if nut_config is None or not nut_config.enabled:
        return "⚪ Off"
    if ups_monitor is None:
        return "⚪ On, no NUT host set"
    if ups_monitor.is_available:
        return "✅ On"
    return "⚠️ On, server unreachable"


def _build_features_view(
    image_update_monitor: Any,
    auto_heal_config: Any,
    memory_config: Any = None,
    unraid_config: Any = None,
    nut_config: Any = None,
    ups_monitor: Any = None,
) -> tuple[str, InlineKeyboardMarkup]:
    """Build the Features panel text and keyboard."""
    image_on = image_update_monitor is not None
    text = (
        "⚙️ *Optional Features*\n\n"
        f"🔄 *Image updates* — {_image_updates_state(image_update_monitor)}\n"
        "_Daily check for newer versions of your container images. You'll get a "
        "digest listing what's outdated, each with a one-tap pull button._\n\n"
        f"🩹 *Auto-heal* — {_auto_heal_state(auto_heal_config)}\n"
        "_Automatically restarts a container when it reports an unhealthy "
        "healthcheck. Opt in per-container; a storm guard stops restart loops._"
    )

    if memory_config is not None:
        text += (
            f"\n\n🧠 *Memory restart list* — {_memory_restart_state(memory_config)}\n"
            "_Containers offered a one-tap Restart button on memory pressure "
            "alerts — for services that grab memory and only give it back "
            "after a bounce._"
        )
        if not memory_config.enabled:
            text += "\n_(Memory management is disabled in config, so no alerts will fire.)_"

    if unraid_config is not None and unraid_config.enabled:
        text += (
            f"\n\n🔔 *Unraid notifications* — {_notifications_state(unraid_config)}\n"
            "_Forwards Unraid's own notification feed (SMART, disk errors, "
            "share full, parity results). Raise the level if it gets chatty._"
        )

    if nut_config is not None:
        text += (
            f"\n\n\U0001f50c *UPS monitoring* — {_ups_state(nut_config, ups_monitor)}\n"
            "_Reads your UPS from a NUT server and alerts on mains loss, low "
            "battery and overload. On by default; it stays quiet if there is no "
            "NUT server to talk to._"
        )

    if image_on:
        image_button = InlineKeyboardButton(
            text="⚪ Disable image updates", callback_data="feat:img:off",
        )
    else:
        image_button = InlineKeyboardButton(
            text="✅ Enable image updates", callback_data="feat:img:on",
        )

    rows = [
        [image_button],
        [InlineKeyboardButton(text="🩹 Configure auto-heal", callback_data="feat:heal")],
    ]
    if memory_config is not None:
        rows.append([InlineKeyboardButton(
            text="🧠 Configure memory restarts", callback_data="feat:memres",
        )])
    if unraid_config is not None and unraid_config.enabled:
        if unraid_config.notifications_enabled:
            rows.append([
                InlineKeyboardButton(
                    text="⚪ Disable notifications", callback_data="feat:notif:off",
                ),
                InlineKeyboardButton(
                    text=f"🔔 {unraid_config.notifications_min_importance}+",
                    callback_data="feat:notif:level",
                ),
            ])
        else:
            rows.append([InlineKeyboardButton(
                text="🔔 Enable Unraid notifications", callback_data="feat:notif:on",
            )])
    if nut_config is not None:
        if nut_config.enabled:
            rows.append([InlineKeyboardButton(
                text="⚪ Disable UPS monitoring", callback_data="feat:ups:off",
            )])
        else:
            rows.append([InlineKeyboardButton(
                text="\U0001f50c Enable UPS monitoring", callback_data="feat:ups:on",
            )])
    rows.append(_back_button())
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def manage_features_callback(
    image_update_monitor: Any = None,
    auto_heal_config: Any = None,
    memory_config: Any = None,
    unraid_config: Any = None,
    nut_config: Any = None,
    ups_monitor: Any = None,
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for the Features panel (manage:features)."""

    async def handler(callback: CallbackQuery) -> None:
        await callback.answer()
        if callback.message:
            text, keyboard = _build_features_view(
                image_update_monitor, auto_heal_config, memory_config, unraid_config,
                nut_config, ups_monitor,
            )
            await safe_edit(callback.message, text, reply_markup=keyboard)

    return handler


def feat_notifications_callback(
    unraid_config: Any,
    image_update_monitor: Any = None,
    auto_heal_config: Any = None,
    memory_config: Any = None,
    restart_cb: Callable[[], Awaitable[None]] | None = None,
    nut_config: Any = None,
    ups_monitor: Any = None,
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for the notification relay buttons (feat:notif:on|off|level).

    Enabling or disabling needs a restart -- the monitor is only constructed at
    startup. Cycling the importance floor applies live, because the running
    monitor reads it off this same config object on every poll.
    """

    async def handler(callback: CallbackQuery) -> None:
        action = (callback.data or "").rsplit(":", 1)[-1]
        if unraid_config is None:
            await callback.answer("Unraid monitoring is not configured")
            return

        if action == "level":
            levels = list(NOTIFICATION_IMPORTANCE_LEVELS)
            try:
                current = levels.index(unraid_config.notifications_min_importance)
            except ValueError:
                current = levels.index(UNRAID_NOTIFICATION_MIN_IMPORTANCE)
            new_level = levels[(current + 1) % len(levels)]
            unraid_config.set_notifications_min_importance(new_level)
            await callback.answer(f"Now relaying {new_level} and above")
            if callback.message:
                text, keyboard = _build_features_view(
                    image_update_monitor, auto_heal_config, memory_config, unraid_config,
                    nut_config, ups_monitor,
                )
                await safe_edit(callback.message, text, reply_markup=keyboard)
            return

        enable = action == "on"
        unraid_config.set_notifications_enabled(enable)
        await callback.answer()

        if restart_cb is not None:
            if callback.message:
                verb = "Enabling" if enable else "Disabling"
                await safe_edit(
                    callback.message,
                    f"♻️ {verb} Unraid notifications — restarting to apply…",
                )
            await restart_cb()
        elif callback.message:
            word = "enabled" if enable else "disabled"
            await safe_edit(
                callback.message,
                f"Unraid notifications {word}. Restart the bot to apply.",
            )

    return handler


def feat_image_toggle_callback(
    image_updates_config: Any,
    restart_cb: Callable[[], Awaitable[None]] | None = None,
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for the image-update enable/disable button (feat:img:on|off).

    The image-update monitor is only constructed at startup, so the change is
    persisted and the bot restarts to apply it.
    """

    async def handler(callback: CallbackQuery) -> None:
        enable = (callback.data or "").endswith(":on")
        if image_updates_config is not None:
            image_updates_config.set_enabled(enable)
        await callback.answer()

        if restart_cb is not None:
            if callback.message:
                verb = "Enabling" if enable else "Disabling"
                await safe_edit(
                    callback.message,
                    f"♻️ {verb} image updates — restarting to apply…",
                )
            await restart_cb()
        elif callback.message:
            word = "enabled" if enable else "disabled"
            await safe_edit(
                callback.message,
                f"Image updates {word}. Restart the bot to apply.",
            )

    return handler


def _picker_candidates(
    state: "ContainerStateManager",
    protected_containers: list[str] | None,
) -> list[str]:
    """Controllable container names eligible for a Features picker, sorted."""
    protected = set(protected_containers or [])
    names = {c.name for c in state.get_all() if c.name and c.name not in protected}
    return sorted(names)


def _build_container_picker(
    intro: str,
    candidates: list[str],
    selected: set[str],
    toggle_prefix: str,
    save_data: str,
) -> tuple[str, InlineKeyboardMarkup]:
    """Build a container toggle-picker: one row per candidate plus Save/Back."""
    text = intro
    buttons: list[list[InlineKeyboardButton]] = []
    if not candidates:
        text += "\n\n_No controllable containers found._"
    for name in candidates:
        mark = "✅" if name in selected else "❌"
        buttons.append([
            InlineKeyboardButton(text=f"{mark} {name}", callback_data=f"{toggle_prefix}:{name}")
        ])

    buttons.append([
        InlineKeyboardButton(text="💾 Save", callback_data=save_data),
        InlineKeyboardButton(text="⬅️ Back", callback_data="manage:features"),
    ])
    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


def _build_heal_picker(
    candidates: list[str],
    selected: set[str],
) -> tuple[str, InlineKeyboardMarkup]:
    """Build the auto-heal container picker text and toggle keyboard."""
    return _build_container_picker(
        "🩹 *Auto-heal containers*\n\n"
        "Tap to choose which containers get auto-restarted when they report an "
        "unhealthy healthcheck. Protected containers are excluded.",
        candidates, selected, "fh_tog", "fh_save",
    )


def _build_memres_picker(
    candidates: list[str],
    selected: set[str],
) -> tuple[str, InlineKeyboardMarkup]:
    """Build the memory-restart container picker text and toggle keyboard."""
    return _build_container_picker(
        "🧠 *Memory restart list*\n\n"
        "Tap to choose which containers get a one-tap Restart button on memory "
        "pressure alerts. Restarting is gentler than stopping — ideal for "
        "services that hog memory but recover after a bounce. Protected "
        "containers are excluded.",
        candidates, selected, "mr_tog", "mr_save",
    )


def feat_heal_open_callback(
    state: "ContainerStateManager",
    auto_heal_config: Any,
    selection_state: ContainerSelectionState,
    protected_containers: list[str] | None = None,
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory that opens the auto-heal container picker (feat:heal)."""

    async def handler(callback: CallbackQuery) -> None:
        user_id = callback.from_user.id if callback.from_user else 0
        current = list(auto_heal_config.containers) if auto_heal_config is not None else []
        selection_state.init(user_id, current)

        candidates = _picker_candidates(state, protected_containers)
        text, keyboard = _build_heal_picker(candidates, selection_state.get(user_id))

        await callback.answer()
        if callback.message:
            await safe_edit(callback.message, text, reply_markup=keyboard)

    return handler


def _toggle_in_picker(
    state: "ContainerStateManager",
    selection_state: ContainerSelectionState,
    protected_containers: list[str] | None,
    build_picker: Callable[[list[str], set[str]], tuple[str, InlineKeyboardMarkup]],
    list_label: str,
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Shared toggle handler for the Features container pickers."""

    async def handler(callback: CallbackQuery) -> None:
        data = callback.data or ""
        # split(":", 1) keeps container names that contain colons intact
        parts = data.split(":", 1)
        if len(parts) < 2 or not parts[1]:
            await callback.answer("Invalid selection")
            return
        container = parts[1]
        user_id = callback.from_user.id if callback.from_user else 0

        candidates = _picker_candidates(state, protected_containers)
        if container not in candidates:
            # Spoofed or stale callback data — only real, non-protected
            # containers may be toggled into the list.
            logger.warning(f"Rejected {list_label} toggle for unknown container: {container[:50]!r}")
            await callback.answer("Unknown container")
            return

        selection_state.toggle(user_id, container)
        await callback.answer()

        _, keyboard = build_picker(candidates, selection_state.get(user_id))
        if isinstance(callback.message, Message):
            try:
                await callback.message.edit_reply_markup(reply_markup=keyboard)
            except Exception:
                pass

    return handler


def feat_heal_toggle_callback(
    state: "ContainerStateManager",
    selection_state: ContainerSelectionState,
    protected_containers: list[str] | None = None,
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for auto-heal picker toggle buttons (fh_tog:<container>)."""
    return _toggle_in_picker(
        state, selection_state, protected_containers, _build_heal_picker, "auto-heal",
    )


def feat_heal_save_callback(
    auto_heal_config: Any,
    selection_state: ContainerSelectionState,
    image_update_monitor: Any = None,
    memory_config: Any = None,
    unraid_config: Any = None,
    nut_config: Any = None,
    ups_monitor: Any = None,
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for the picker Save button (fh_save).

    Auto-heal applies live: the running AutoHealer shares this config object,
    so no restart is needed.
    """

    async def handler(callback: CallbackQuery) -> None:
        user_id = callback.from_user.id if callback.from_user else 0
        selected = sorted(selection_state.get(user_id))
        saved = selected
        if auto_heal_config is not None:
            auto_heal_config.set_containers(selected)
            saved = list(auto_heal_config.containers)
        selection_state.clear(user_id)

        count = len(saved)
        answer = f"Auto-heal {'on for ' + str(count) + ' container(s)' if count else 'disabled'}"
        # set_containers drops names that fail validation — unreachable via the
        # picker, but if it ever happens the user should hear about it.
        dropped = len(selected) - len(saved)
        if dropped:
            answer += f" ({dropped} invalid name(s) skipped)"
        await callback.answer(answer)
        if callback.message:
            text, keyboard = _build_features_view(
                image_update_monitor, auto_heal_config, memory_config, unraid_config,
                nut_config, ups_monitor,
            )
            await safe_edit(callback.message, text, reply_markup=keyboard)

    return handler


def feat_memres_open_callback(
    state: "ContainerStateManager",
    memory_config: Any,
    selection_state: ContainerSelectionState,
    protected_containers: list[str] | None = None,
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory that opens the memory-restart container picker (feat:memres)."""

    async def handler(callback: CallbackQuery) -> None:
        user_id = callback.from_user.id if callback.from_user else 0
        current = list(memory_config.restart_containers) if memory_config is not None else []
        selection_state.init(user_id, current)

        candidates = _picker_candidates(state, protected_containers)
        text, keyboard = _build_memres_picker(candidates, selection_state.get(user_id))

        await callback.answer()
        if callback.message:
            await safe_edit(callback.message, text, reply_markup=keyboard)

    return handler


def feat_memres_toggle_callback(
    state: "ContainerStateManager",
    selection_state: ContainerSelectionState,
    protected_containers: list[str] | None = None,
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for memory-restart picker toggle buttons (mr_tog:<container>)."""
    return _toggle_in_picker(
        state, selection_state, protected_containers, _build_memres_picker, "memory-restart",
    )


def feat_memres_save_callback(
    memory_config: Any,
    selection_state: ContainerSelectionState,
    image_update_monitor: Any = None,
    auto_heal_config: Any = None,
    unraid_config: Any = None,
    nut_config: Any = None,
    ups_monitor: Any = None,
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for the memory-restart picker Save button (mr_save).

    Applies live: the running MemoryMonitor shares this config object, so the
    next pressure alert offers the updated restart buttons without a bot
    restart.
    """

    async def handler(callback: CallbackQuery) -> None:
        user_id = callback.from_user.id if callback.from_user else 0
        selected = sorted(selection_state.get(user_id))
        saved = selected
        if memory_config is not None:
            memory_config.set_restart_containers(selected)
            saved = list(memory_config.restart_containers)
        selection_state.clear(user_id)

        count = len(saved)
        answer = f"Memory restarts {'on for ' + str(count) + ' container(s)' if count else 'off'}"
        dropped = len(selected) - len(saved)
        if dropped:
            answer += f" ({dropped} invalid name(s) skipped)"
        await callback.answer(answer)
        if callback.message:
            text, keyboard = _build_features_view(
                image_update_monitor, auto_heal_config, memory_config, unraid_config,
                nut_config, ups_monitor,
            )
            await safe_edit(callback.message, text, reply_markup=keyboard)

    return handler


def feat_ups_toggle_callback(
    nut_config: Any,
    restart_cb: Callable[[], Awaitable[None]] | None = None,
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for the UPS monitoring toggle (feat:ups:on|off).

    The monitor is only built at startup, so the setting is persisted and the
    bot restarts to apply it, exactly like the notification relay.
    """

    async def handler(callback: CallbackQuery) -> None:
        if nut_config is None:
            await callback.answer("UPS monitoring is not configured")
            return

        enable = (callback.data or "").rsplit(":", 1)[-1] == "on"
        nut_config.set_enabled(enable)
        await callback.answer()

        if restart_cb is not None:
            if callback.message:
                verb = "Enabling" if enable else "Disabling"
                await safe_edit(
                    callback.message,
                    f"♻️ {verb} UPS monitoring — restarting to apply…",
                )
            await restart_cb()
        elif callback.message:
            word = "enabled" if enable else "disabled"
            await safe_edit(
                callback.message,
                f"UPS monitoring {word}. Restart the bot to apply.",
            )

    return handler
