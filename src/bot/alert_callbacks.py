"""Callback handlers for alert action buttons."""

import asyncio
import logging
from datetime import timedelta
from typing import Callable, Awaitable, Any, TYPE_CHECKING

from aiogram.types import CallbackQuery, Message
from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramBadRequest
import docker

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from src.config import ResourceConfig, UnraidConfig
from src.constants import UNRAID_ARRAY_USAGE_THRESHOLD, UNRAID_DISK_TEMP_THRESHOLD
from src.state import ContainerStateManager
from src.services.container_control import ContainerController
from src.services.diagnostic import DiagnosticService
from src.utils.formatting import validate_container_name, escape_markdown, truncate_callback_data
from src.utils.sanitize import sanitize_logs_for_display

if TYPE_CHECKING:
    from src.alerts.array_mute_manager import ArrayMuteManager
    from src.monitors.memory_monitor import MemoryMonitor

logger = logging.getLogger(__name__)


def restart_callback(
    state: ContainerStateManager,
    controller: ContainerController,
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for restart button callback handler."""

    async def handler(callback: CallbackQuery) -> None:
        if not callback.data:
            return

        # Parse callback data: restart:container_name
        # Use maxsplit=1 to handle container names with colons
        parts = callback.data.split(":", 1)
        if len(parts) < 2:
            await callback.answer("Invalid callback data")
            return

        container_name = parts[1]

        # Validate container name format
        if not validate_container_name(container_name):
            logger.warning(f"Invalid container name in callback: {container_name[:50]}")
            await callback.answer("Invalid container name")
            return

        # Find container
        matches = state.find_by_name(container_name)
        if not matches:
            await callback.answer(f"Container '{container_name}' not found")
            return

        actual_name = matches[0].name

        # Check if container is protected
        if controller.is_protected(actual_name):
            await callback.answer(f"{actual_name} is protected", show_alert=True)
            return

        # Acknowledge button press
        await callback.answer(f"Restarting {actual_name}...")

        # Perform restart
        message = await controller.restart(actual_name)

        # Send result message (message already contains emoji indicator)
        if callback.message:
            await callback.message.answer(message)

    return handler


def logs_callback(
    state: ContainerStateManager,
    docker_client: docker.DockerClient,
    max_lines: int = 100,
    max_chars: int = 4000,
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for logs button callback handler."""

    async def handler(callback: CallbackQuery) -> None:
        if not callback.data:
            return

        # Parse callback data: logs:container_name:lines
        # Split from the right to handle container names with colons
        # Format: logs:container_name:50 -> ["logs", "container_name", "50"]
        parts = callback.data.rsplit(":", 1)  # Split off the lines count
        if len(parts) < 2:
            await callback.answer("Invalid callback data")
            return

        try:
            lines = int(parts[1])
        except ValueError:
            lines = 50

        # Now split the prefix to get container name
        prefix_parts = parts[0].split(":", 1)  # Split off "logs"
        if len(prefix_parts) < 2:
            await callback.answer("Invalid callback data")
            return

        container_name = prefix_parts[1]

        # Validate container name format
        if not validate_container_name(container_name):
            logger.warning(f"Invalid container name in callback: {container_name[:50]}")
            await callback.answer("Invalid container name")
            return

        # Cap at reasonable limit
        lines = min(lines, max_lines)

        # Find container
        matches = state.find_by_name(container_name)
        if not matches:
            await callback.answer(f"Container '{container_name}' not found")
            return

        actual_name = matches[0].name

        # Acknowledge button press
        await callback.answer(f"Fetching logs for {actual_name}...")

        try:
            docker_container = await asyncio.to_thread(
                docker_client.containers.get, actual_name
            )
            log_bytes = await asyncio.to_thread(
                docker_container.logs, tail=lines, timestamps=False
            )
            log_text = log_bytes.decode("utf-8", errors="replace")

            # Truncate if too long for Telegram
            if len(log_text) > max_chars:
                log_text = log_text[-max_chars:]
                log_text = "...(truncated)\n" + log_text

            # Sanitize to remove sensitive data before display
            log_text = sanitize_logs_for_display(log_text)

            response = f"*Logs: {escape_markdown(actual_name)}* (last {lines} lines)\n\n```\n{log_text}\n```"

            if callback.message:
                try:
                    await callback.message.answer(response, parse_mode="Markdown")
                except TelegramBadRequest:
                    # Fall back to plain text
                    plain_response = f"Logs: {actual_name} (last {lines} lines)\n\n{log_text}"
                    await callback.message.answer(plain_response)

        except docker.errors.NotFound:
            if callback.message:
                await callback.message.answer(f"Container '{actual_name}' not found in Docker")
        except Exception as e:
            logger.error(f"Error getting logs for {actual_name}: {e}", exc_info=True)
            if callback.message:
                await callback.message.answer(
                    f"❌ Error getting logs for {actual_name}. Check bot logs for details."
                )

    return handler


def diagnose_callback(
    state: ContainerStateManager,
    diagnostic_service: DiagnosticService | None,
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for diagnose button callback handler."""

    async def handler(callback: CallbackQuery) -> None:
        if not callback.data:
            return

        if not diagnostic_service:
            await callback.answer("AI diagnostics not configured")
            return

        # Parse callback data: diagnose:container_name
        # Use maxsplit=1 to handle container names with colons
        parts = callback.data.split(":", 1)
        if len(parts) < 2:
            await callback.answer("Invalid callback data")
            return

        container_name = parts[1]

        # Validate container name format
        if not validate_container_name(container_name):
            logger.warning(f"Invalid container name in callback: {container_name[:50]}")
            await callback.answer("Invalid container name")
            return

        # Find container
        matches = state.find_by_name(container_name)
        if not matches:
            await callback.answer(f"Container '{container_name}' not found")
            return

        actual_name = matches[0].name

        # Acknowledge button press
        await callback.answer(f"Analyzing {actual_name}...")

        if isinstance(callback.message, Message):
            if callback.message.bot:
                await callback.message.bot.send_chat_action(chat_id=callback.message.chat.id, action=ChatAction.TYPING)
            await callback.message.answer(f"Analyzing {actual_name}...")

        # Gather context
        context = await diagnostic_service.gather_context(actual_name, lines=50)
        if not context:
            if callback.message:
                await callback.message.answer(f"Could not get container info for '{actual_name}'")
            return

        # Analyze with Claude
        analysis = await diagnostic_service.analyze(context)

        # Store context for follow-up
        user_id = callback.from_user.id if callback.from_user else 0
        context.brief_summary = analysis
        diagnostic_service.store_context(user_id, context)

        response = f"""*Diagnosis: {escape_markdown(actual_name)}*

{analysis}

_Want more details?_"""

        if callback.message:
            try:
                await callback.message.answer(response, parse_mode="Markdown")
            except TelegramBadRequest:
                plain_response = f"Diagnosis: {actual_name}\n\n{analysis}\n\nWant more details?"
                await callback.message.answer(plain_response)

    return handler


def mute_callback(
    state: ContainerStateManager,
    mute_manager: Any,
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for mute button callback handler."""

    async def handler(callback: CallbackQuery) -> None:
        if not callback.data:
            return

        if not mute_manager:
            await callback.answer("Mute manager not configured")
            return

        # Parse callback data: mute:container_name:minutes
        # Split from the right to handle container names with colons
        parts = callback.data.rsplit(":", 1)  # Split off the minutes count
        if len(parts) < 2:
            await callback.answer("Invalid callback data")
            return

        try:
            minutes = int(parts[1])
        except ValueError:
            minutes = 60

        # Now split the prefix to get container name
        prefix_parts = parts[0].split(":", 1)  # Split off "mute"
        if len(prefix_parts) < 2:
            await callback.answer("Invalid callback data")
            return

        container_name = prefix_parts[1]

        # Validate container name format
        if not validate_container_name(container_name):
            logger.warning(f"Invalid container name in callback: {container_name[:50]}")
            await callback.answer("Invalid container name")
            return

        # Find container
        matches = state.find_by_name(container_name)
        if not matches:
            await callback.answer(f"Container '{container_name}' not found")
            return

        actual_name = matches[0].name

        # Mute the container
        mute_manager.add_mute(actual_name, timedelta(minutes=minutes))

        # Format duration for display
        if minutes >= 1440:
            duration_str = f"{minutes // 1440} day(s)"
        elif minutes >= 60:
            duration_str = f"{minutes // 60} hour(s)"
        else:
            duration_str = f"{minutes} minute(s)"

        await callback.answer(f"Muted {actual_name} for {duration_str}")

        if callback.message:
            await callback.message.answer(f"🔕 Muted *{escape_markdown(actual_name)}* for {duration_str}", parse_mode="Markdown")

    return handler


def mem_kill_callback(
    memory_monitor: "MemoryMonitor",
    protected_containers: list[str] | None = None,
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for memory kill button callback handler."""
    _protected = set(protected_containers or [])

    async def handler(callback: CallbackQuery) -> None:
        if not callback.data:
            return

        # Parse callback data: mem_kill:container_name
        parts = callback.data.split(":", 1)
        if len(parts) < 2:
            await callback.answer("Invalid callback data")
            return

        container_name = parts[1]

        if not validate_container_name(container_name):
            logger.warning(f"Invalid container name in mem_kill callback: {container_name[:50]}")
            await callback.answer("Invalid container name")
            return

        if container_name in _protected:
            await callback.answer(f"{container_name} is a protected container")
            return

        await callback.answer(f"Stopping {container_name}...")

        success = await memory_monitor.kill_container(container_name)

        if callback.message:
            if success:
                await callback.message.answer(
                    f"⏹ Stopped *{escape_markdown(container_name)}* to free memory.", parse_mode="Markdown"
                )
            else:
                await callback.message.answer(
                    f"❌ Failed to stop {container_name}. It may already be stopped."
                )

    return handler


def mem_cancel_kill_callback(
    memory_monitor: "MemoryMonitor",
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for cancel auto-kill button callback handler."""

    async def handler(callback: CallbackQuery) -> None:
        cancelled = memory_monitor.cancel_pending_kill()

        if cancelled:
            await callback.answer("Auto-kill cancelled")
            if callback.message:
                await callback.message.answer("❌ Auto-kill cancelled.")
        else:
            await callback.answer("No pending kill to cancel")

    return handler


def mem_restart_yes_callback(
    memory_monitor: "MemoryMonitor",
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for memory restart Yes button callback handler."""

    async def handler(callback: CallbackQuery) -> None:
        if not callback.data:
            return

        parts = callback.data.split(":", 1)
        if len(parts) < 2:
            await callback.answer("Invalid callback data")
            return

        container_name = parts[1]

        if not validate_container_name(container_name):
            logger.warning(f"Invalid container name in mem_restart_yes callback: {container_name[:50]}")
            await callback.answer("Invalid container name")
            return

        await callback.answer(f"Restarting {container_name}...")

        success = await memory_monitor.confirm_restart(container_name)

        if isinstance(callback.message, Message):
            if success:
                try:
                    await callback.message.edit_text(
                        f"💾 Restarted {container_name} after memory recovery."
                    )
                except TelegramBadRequest:
                    await callback.message.answer(f"✅ Restarted {container_name}.")
            else:
                try:
                    await callback.message.edit_text(
                        f"❌ Failed to restart {container_name}. It may need manual attention."
                    )
                except TelegramBadRequest:
                    await callback.message.answer(f"❌ Failed to restart {container_name}.")

    return handler


def mem_restart_no_callback(
    memory_monitor: "MemoryMonitor",
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for memory restart No button callback handler."""

    async def handler(callback: CallbackQuery) -> None:
        if not callback.data:
            return

        parts = callback.data.split(":", 1)
        if len(parts) < 2:
            await callback.answer("Invalid callback data")
            return

        container_name = parts[1]

        if not validate_container_name(container_name):
            logger.warning(f"Invalid container name in mem_restart_no callback: {container_name[:50]}")
            await callback.answer("Invalid container name")
            return

        await memory_monitor.decline_restart(container_name)
        await callback.answer(f"Won't restart {container_name}")

        if isinstance(callback.message, Message):
            try:
                await callback.message.edit_text(
                    f"💾 Declined restart of {container_name}. It will stay stopped."
                )
            except TelegramBadRequest:
                await callback.message.answer(f"Won't restart {container_name}.")

    return handler


CPU_THRESHOLD_STEPS = [90, 120, 150, 200, 300, 400]
MEMORY_THRESHOLD_STEPS = [85, 90, 95, 99]
ARRAY_CAPACITY_STEPS = [80, 85, 90, 95, 98]
DISK_TEMP_STEPS = [45, 50, 55, 60, 65, 70]


def raise_limit_callback(
    resource_config: ResourceConfig,
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for 'Raise Limit' button — shows threshold options."""

    async def handler(callback: CallbackQuery) -> None:
        if not callback.data:
            return

        # Format: res_limit:container_name:metric:current_threshold
        # Split from right to get trailing fields
        parts = callback.data.rsplit(":", 2)
        if len(parts) < 3:
            await callback.answer("Invalid callback data")
            return

        try:
            current_threshold = int(parts[2])
        except ValueError:
            current_threshold = 80

        metric = parts[1]
        if metric not in ("cpu", "memory"):
            await callback.answer("Invalid metric")
            return

        prefix_parts = parts[0].split(":", 1)
        if len(prefix_parts) < 2:
            await callback.answer("Invalid callback data")
            return

        container_name = prefix_parts[1]

        if not validate_container_name(container_name):
            logger.warning(f"Invalid container name in res_limit callback: {container_name[:50]}")
            await callback.answer("Invalid container name")
            return

        await callback.answer()

        # Build option buttons — only show values above current threshold
        # CPU uses per-core reporting, so >100% is normal on multi-core systems
        steps = CPU_THRESHOLD_STEPS if metric == "cpu" else MEMORY_THRESHOLD_STEPS
        options = [v for v in steps if v > current_threshold]
        if not options:
            options = [steps[-1]]

        buttons: list[list[InlineKeyboardButton]] = []
        row: list[InlineKeyboardButton] = []
        for value in options:
            row.append(InlineKeyboardButton(
                text=f"{value}%",
                callback_data=truncate_callback_data("res_set:", f"{container_name}:{metric}:{value}"),
            ))
        buttons.append(row)

        # Reset to default option
        buttons.append([InlineKeyboardButton(
            text="↩️ Reset to default",
            callback_data=truncate_callback_data("res_set:", f"{container_name}:{metric}:0"),
        )])

        safe_name = escape_markdown(container_name)
        metric_label = "CPU" if metric == "cpu" else "Memory"

        if callback.message:
            try:
                await callback.message.answer(
                    f"⚙️ Set *{metric_label}* threshold for *{safe_name}*\nCurrent: {current_threshold}%",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
                )
            except TelegramBadRequest:
                await callback.message.answer(
                    f"Set {metric_label} threshold for {container_name}\nCurrent: {current_threshold}%",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
                )

    return handler


def set_limit_callback(
    resource_config: ResourceConfig,
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for applying a selected threshold."""

    async def handler(callback: CallbackQuery) -> None:
        if not callback.data:
            return

        # Format: res_set:container_name:metric:value
        parts = callback.data.rsplit(":", 2)
        if len(parts) < 3:
            await callback.answer("Invalid callback data")
            return

        try:
            value = int(parts[2])
        except ValueError:
            await callback.answer("Invalid threshold value")
            return

        metric = parts[1]
        if metric not in ("cpu", "memory"):
            await callback.answer("Invalid metric")
            return

        prefix_parts = parts[0].split(":", 1)
        if len(prefix_parts) < 2:
            await callback.answer("Invalid callback data")
            return

        container_name = prefix_parts[1]

        if not validate_container_name(container_name):
            logger.warning(f"Invalid container name in res_set callback: {container_name[:50]}")
            await callback.answer("Invalid container name")
            return

        resource_config.set_threshold(container_name, metric, value)

        metric_label = "CPU" if metric == "cpu" else "Memory"
        safe_name = escape_markdown(container_name)

        if value == 0:
            cpu_default, mem_default = resource_config.get_thresholds(container_name)
            default_val = cpu_default if metric == "cpu" else mem_default
            msg = f"↩️ *{metric_label}* threshold for *{safe_name}* reset to default ({default_val}%)"
        else:
            msg = f"✅ *{metric_label}* threshold for *{safe_name}* set to {value}%"

        await callback.answer(f"{'Reset' if value == 0 else 'Set'} to {value or 'default'}%")

        if isinstance(callback.message, Message):
            try:
                await callback.message.edit_text(msg, parse_mode="Markdown")
            except TelegramBadRequest:
                await callback.message.answer(msg.replace("*", ""))

    return handler


def array_mute_callback(
    array_mute_manager: "ArrayMuteManager",
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for array mute button callback handler."""

    async def handler(callback: CallbackQuery) -> None:
        if not callback.data:
            return

        parts = callback.data.split(":", 1)
        if len(parts) < 2:
            await callback.answer("Invalid callback data")
            return

        try:
            minutes = int(parts[1])
        except ValueError:
            minutes = 60

        array_mute_manager.mute_array(timedelta(minutes=minutes))

        if minutes >= 1440:
            duration_str = f"{minutes // 1440} day(s)"
        elif minutes >= 60:
            duration_str = f"{minutes // 60} hour(s)"
        else:
            duration_str = f"{minutes} minute(s)"

        await callback.answer(f"Muted array alerts for {duration_str}")

        if callback.message:
            try:
                await callback.message.answer(
                    f"🔇 *Muted array alerts* for {duration_str}\n"
                    f"Use `/unmute-array` to unmute early.",
                    parse_mode="Markdown",
                )
            except TelegramBadRequest:
                await callback.message.answer(
                    f"🔇 Muted array alerts for {duration_str}\n"
                    f"Use /unmute-array to unmute early."
                )

    return handler


def array_threshold_callback(
    unraid_config: UnraidConfig,
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for array threshold adjustment button — shows options."""

    async def handler(callback: CallbackQuery) -> None:
        if not callback.data:
            return

        # Format: arr_thresh:metric:current_value
        parts = callback.data.split(":")
        if len(parts) < 3:
            await callback.answer("Invalid callback data")
            return

        metric = parts[1]
        try:
            current = int(parts[2])
        except ValueError:
            current = 0

        await callback.answer()

        if metric == "capacity":
            steps = ARRAY_CAPACITY_STEPS
            unit = "%"
            label = "Array Capacity"
            default = UNRAID_ARRAY_USAGE_THRESHOLD
        elif metric == "disk_temp":
            steps = DISK_TEMP_STEPS
            unit = "°C"
            label = "Disk Temperature"
            default = UNRAID_DISK_TEMP_THRESHOLD
        else:
            await callback.answer("Unknown metric")
            return

        options = [v for v in steps if v > current]
        if not options:
            options = [steps[-1]]

        buttons: list[list[InlineKeyboardButton]] = []
        row: list[InlineKeyboardButton] = []
        for value in options:
            arr_metric = "array_usage" if metric == "capacity" else "disk_temp"
            row.append(InlineKeyboardButton(
                text=f"{value}{unit}",
                callback_data=f"arr_set:{arr_metric}:{value}",
            ))
        buttons.append(row)

        arr_metric = "array_usage" if metric == "capacity" else "disk_temp"
        buttons.append([InlineKeyboardButton(
            text=f"↩️ Reset to default ({default}{unit})",
            callback_data=f"arr_set:{arr_metric}:0",
        )])

        if callback.message:
            try:
                await callback.message.answer(
                    f"⚙️ Set *{label}* threshold\nCurrent: {current}{unit}",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
                )
            except TelegramBadRequest:
                await callback.message.answer(
                    f"Set {label} threshold\nCurrent: {current}{unit}",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
                )

    return handler


def array_set_threshold_callback(
    unraid_config: UnraidConfig,
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for applying a selected array threshold."""

    async def handler(callback: CallbackQuery) -> None:
        if not callback.data:
            return

        # Format: arr_set:metric:value
        parts = callback.data.split(":")
        if len(parts) < 3:
            await callback.answer("Invalid callback data")
            return

        metric = parts[1]
        if metric not in ("array_usage", "disk_temp"):
            await callback.answer("Invalid metric")
            return

        try:
            value = int(parts[2])
        except ValueError:
            await callback.answer("Invalid threshold value")
            return

        unraid_config.set_threshold(metric, value)

        if metric == "array_usage":
            label = "Array Capacity"
            unit = "%"
            actual = unraid_config.array_usage_threshold
        else:
            label = "Disk Temperature"
            unit = "°C"
            actual = unraid_config.disk_temp_threshold

        if value == 0:
            msg = f"↩️ *{label}* threshold reset to default ({actual}{unit})"
        else:
            msg = f"✅ *{label}* threshold set to {actual}{unit}"

        await callback.answer(f"{'Reset' if value == 0 else 'Set'} to {actual}{unit}")

        if isinstance(callback.message, Message):
            try:
                await callback.message.edit_text(msg, parse_mode="Markdown")
            except TelegramBadRequest:
                await callback.message.answer(msg.replace("*", ""))

    return handler
