"""Callback handlers for alert action buttons."""

import asyncio
import html
import logging
import re
from datetime import timedelta
from typing import Callable, Awaitable, Any, TYPE_CHECKING

from aiogram.types import CallbackQuery, Message
from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramBadRequest
import docker

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from src.config import MemoryConfig, ResourceConfig, UnraidConfig
from src.constants import (
    CPU_THRESHOLD_STEPS,
    MEMORY_THRESHOLD_STEPS,
    UNRAID_ARRAY_USAGE_THRESHOLD,
    UNRAID_CPU_TEMP_THRESHOLD,
    UNRAID_CPU_USAGE_THRESHOLD,
    UNRAID_DISK_TEMP_THRESHOLD,
)
from src.state import ContainerStateManager
from src.services.container_control import ContainerController
from src.services.diagnostic import DiagnosticService
from src.utils.formatting import validate_container_name, escape_markdown, truncate_callback_data, format_duration_minutes, format_bytes
from src.utils.sanitize import sanitize_logs_for_display
from src.utils.telegram_format import markdown_to_telegram_html

if TYPE_CHECKING:
    from src.alerts.array_mute_manager import ArrayMuteManager
    from src.monitors.memory_monitor import MemoryMonitor

logger = logging.getLogger(__name__)


async def _validate_and_resolve(
    callback: CallbackQuery,
    container_name: str,
    state: ContainerStateManager | None,
) -> str | None:
    """Shared tail of callback parsing: name-format check + optional state lookup.

    Answers the callback with the error itself; returns the resolved container
    name (or the validated raw name when *state* is None), else None.
    """
    if not validate_container_name(container_name):
        logger.warning(f"Invalid container name in callback: {container_name[:50]}")
        await callback.answer("Invalid container name")
        return None
    if state is None:
        return container_name
    matches = state.find_by_name(container_name)
    if not matches:
        await callback.answer(f"Container '{container_name}' not found")
        return None
    return matches[0].name


async def _parse_container_callback(
    callback: CallbackQuery,
    state: ContainerStateManager | None = None,
) -> str | None:
    """Parse ``prefix:container_name`` callback data.

    maxsplit=1 keeps container names containing colons intact. Answers the
    callback with the error itself; returns the (resolved) name or None.
    """
    if not callback.data:
        return None
    parts = callback.data.split(":", 1)
    if len(parts) < 2:
        await callback.answer("Invalid callback data")
        return None
    return await _validate_and_resolve(callback, parts[1], state)


async def _parse_valued_callback(
    callback: CallbackQuery,
    state: ContainerStateManager | None = None,
    default: int = 60,
    max_value: int | None = None,
) -> tuple[str, int] | None:
    """Parse ``prefix:container_name:value`` callback data.

    The value is split from the right so container names containing colons
    stay intact. A non-numeric value falls back to *default*; *max_value*
    clamps into [1, max_value]. Answers the callback with the error itself;
    returns (resolved_name, value) or None.
    """
    if not callback.data:
        return None
    parts = callback.data.rsplit(":", 1)
    if len(parts) < 2:
        await callback.answer("Invalid callback data")
        return None
    try:
        value = int(parts[1])
    except ValueError:
        value = default
    if max_value is not None:
        value = max(1, min(value, max_value))
    prefix_parts = parts[0].split(":", 1)
    if len(prefix_parts) < 2:
        await callback.answer("Invalid callback data")
        return None
    name = await _validate_and_resolve(callback, prefix_parts[1], state)
    if name is None:
        return None
    return name, value


async def _parse_metric_callback(
    callback: CallbackQuery,
    default: int | None = None,
) -> tuple[str, str, int] | None:
    """Parse ``prefix:container_name:metric:value`` (metric: cpu|memory).

    A non-numeric value falls back to *default*, or answers "Invalid threshold
    value" when *default* is None. Returns (name, metric, value) or None.
    """
    if not callback.data:
        return None
    parts = callback.data.rsplit(":", 2)
    if len(parts) < 3:
        await callback.answer("Invalid callback data")
        return None
    try:
        value = int(parts[2])
    except ValueError:
        if default is None:
            await callback.answer("Invalid threshold value")
            return None
        value = default
    metric = parts[1]
    if metric not in ("cpu", "memory"):
        await callback.answer("Invalid metric")
        return None
    prefix_parts = parts[0].split(":", 1)
    if len(prefix_parts) < 2:
        await callback.answer("Invalid callback data")
        return None
    name = await _validate_and_resolve(callback, prefix_parts[1], None)
    if name is None:
        return None
    return name, metric, value


async def _parse_minutes_callback(callback: CallbackQuery) -> int | None:
    """Parse ``prefix:minutes`` mute callbacks; clamps to [1, 43200] (30 days)."""
    if not callback.data:
        return None
    parts = callback.data.split(":", 1)
    if len(parts) < 2:
        await callback.answer("Invalid callback data")
        return None
    try:
        return max(1, min(int(parts[1]), 43200))
    except ValueError:
        return 60


def restart_callback(
    state: ContainerStateManager,
    controller: ContainerController,
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for restart button callback handler."""

    async def handler(callback: CallbackQuery) -> None:
        actual_name = await _parse_container_callback(callback, state)
        if actual_name is None:
            return

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
        # Format: logs:container_name:lines
        parsed = await _parse_valued_callback(callback, state, default=50)
        if parsed is None:
            return
        actual_name, lines = parsed

        # Cap at reasonable limit
        lines = min(lines, max_lines)

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


_ALERT_TYPE_PATTERNS = [
    (re.compile(r"CONTAINER CRASHED", re.IGNORECASE), "Container crash alert"),
    (re.compile(r"ERRORS IN", re.IGNORECASE), "Error alert (container still running with errors)"),
    (re.compile(r"RESTART LOOP", re.IGNORECASE), "Restart loop alert"),
    (re.compile(r"HIGH .+ USAGE", re.IGNORECASE), "High resource usage alert"),
]


def _extract_alert_context(message: Message | None) -> str:
    """Extract what kind of alert triggered this diagnosis."""
    if not message or not message.text:
        return ""
    text = message.text
    for pattern, description in _ALERT_TYPE_PATTERNS:
        if pattern.search(text):
            return description
    return ""


def diagnose_callback(
    state: ContainerStateManager,
    diagnostic_service: DiagnosticService | None,
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for diagnose button callback handler."""

    async def handler(callback: CallbackQuery) -> None:
        if not diagnostic_service:
            await callback.answer("AI diagnostics not configured")
            return

        actual_name = await _parse_container_callback(callback, state)
        if actual_name is None:
            return

        # Extract alert context from the parent message (the alert)
        alert_context = ""
        if isinstance(callback.message, Message):
            alert_context = _extract_alert_context(callback.message)

        # Acknowledge button press
        await callback.answer(f"Analyzing {actual_name}...")

        if isinstance(callback.message, Message):
            if callback.message.bot:
                await callback.message.bot.send_chat_action(chat_id=callback.message.chat.id, action=ChatAction.TYPING)
            await callback.message.answer(f"Analyzing {actual_name}...")

        # Gather context with alert info
        context = await diagnostic_service.gather_context(
            actual_name, lines=50, alert_context=alert_context,
        )
        if not context:
            if callback.message:
                await callback.message.answer(f"Could not get container info for '{actual_name}'")
            return

        # Analyze
        analysis = await diagnostic_service.analyze(context)

        # Store context for follow-up
        user_id = callback.from_user.id if callback.from_user else 0
        context.brief_summary = analysis
        diagnostic_service.store_context(user_id, context)

        # analysis is LLM Markdown; render the whole reply as Telegram HTML.
        response = (
            f"<b>Diagnosis: {html.escape(actual_name)}</b>\n\n"
            f"{markdown_to_telegram_html(analysis)}\n\n"
            f"<i>Want more details?</i>"
        )

        if callback.message:
            try:
                await callback.message.answer(response, parse_mode="HTML")
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
        if not mute_manager:
            await callback.answer("Mute manager not configured")
            return

        # Format: mute:container_name:minutes
        parsed = await _parse_valued_callback(callback, state, default=60, max_value=43200)
        if parsed is None:
            return
        actual_name, minutes = parsed

        # Mute the container
        mute_manager.add_mute(actual_name, timedelta(minutes=minutes))

        # Format duration for display
        duration_str = format_duration_minutes(minutes)

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
        # Format: mem_kill:container_name
        container_name = await _parse_container_callback(callback)
        if container_name is None:
            return

        if container_name in _protected:
            await callback.answer(f"{container_name} is a protected container")
            return

        await callback.answer(f"Stopping {container_name}...")

        result = await memory_monitor.kill_container(container_name)

        if callback.message:
            if result.success:
                lines = [f"⏹ Stopped *{escape_markdown(container_name)}* to free memory."]
                if result.freed_bytes:
                    lines.append(f"It was using ~{format_bytes(result.freed_bytes)}.")
                if result.system_percent is not None:
                    free = (
                        format_bytes(result.system_available_bytes)
                        if result.system_available_bytes is not None
                        else "?"
                    )
                    lines.append(
                        f"System memory now {result.system_percent:.0f}% ({free} free)."
                    )
                await callback.message.answer(" ".join(lines), parse_mode="Markdown")
            else:
                await callback.message.answer(
                    f"❌ Failed to stop {container_name}. It may already be stopped."
                )

    return handler


def mem_restart_callback(
    memory_monitor: "MemoryMonitor",
    memory_config: MemoryConfig,
    protected_containers: list[str] | None = None,
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for the memory restart button callback handler (mem_restart:<name>).

    The gentle alternative to a kill for services that free memory after a
    bounce. Only names currently opted in via
    memory_management.restart_containers are accepted -- the config object is
    shared with the /manage picker, so list changes apply live. Protected
    containers are rejected even if hand-edited into the restart list.
    """
    _protected = set(protected_containers or [])

    async def handler(callback: CallbackQuery) -> None:
        container_name = await _parse_container_callback(callback)
        if container_name is None:
            return

        if container_name in _protected:
            await callback.answer(f"{container_name} is a protected container")
            return

        if container_name not in memory_config.restart_containers:
            await callback.answer(f"{container_name} is not in the memory restart list")
            return

        await callback.answer(f"Restarting {container_name}...")

        result = await memory_monitor.restart_container(container_name)

        if callback.message:
            if result.success:
                lines = [f"🔄 Restarted *{escape_markdown(container_name)}* to reclaim memory."]
                if result.freed_bytes:
                    lines.append(f"It was using ~{format_bytes(result.freed_bytes)}.")
                if result.system_percent is not None:
                    free = (
                        format_bytes(result.system_available_bytes)
                        if result.system_available_bytes is not None
                        else "?"
                    )
                    lines.append(
                        f"System memory now {result.system_percent:.0f}% ({free} free)."
                    )
                await callback.message.answer(" ".join(lines), parse_mode="Markdown")
            else:
                await callback.message.answer(
                    f"❌ Failed to restart {container_name}. Check its logs."
                )

    return handler


def mem_cancel_kill_callback(
    memory_monitor: "MemoryMonitor",
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for cancel auto-kill button callback handler."""

    async def handler(callback: CallbackQuery) -> None:
        cancelled = await memory_monitor.cancel_pending_kill()

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
        container_name = await _parse_container_callback(callback)
        if container_name is None:
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
        container_name = await _parse_container_callback(callback)
        if container_name is None:
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



_UNRAID_METRICS: dict[str, tuple[list[int], str, str, int, str]] = {
    # metric_key: (steps, unit, label, default, config_attr)
    "capacity":  ([80, 85, 90, 95, 98], "%", "Array Capacity", UNRAID_ARRAY_USAGE_THRESHOLD, "array_usage_threshold"),
    "disk_temp": ([45, 50, 55, 60, 65, 70], "°C", "Disk Temperature", UNRAID_DISK_TEMP_THRESHOLD, "disk_temp_threshold"),
    "cpu_temp":  ([70, 80, 85, 90, 95, 100], "°C", "CPU Temperature", UNRAID_CPU_TEMP_THRESHOLD, "cpu_temp_threshold"),
    "cpu_usage": ([80, 85, 90, 95, 98], "%", "CPU Usage", UNRAID_CPU_USAGE_THRESHOLD, "cpu_usage_threshold"),
}

_METRIC_TO_SET_KEY: dict[str, str] = {
    "capacity": "array_usage",
    "disk_temp": "disk_temp",
    "cpu_temp": "cpu_temp",
    "cpu_usage": "cpu_usage",
}

# Buttons carry the set-key (what UnraidConfig.set_threshold expects), while
# _UNRAID_METRICS is keyed on the picker's metric name -- these differ for
# capacity/array_usage, so _apply_threshold has to map back.
_SET_KEY_TO_METRIC: dict[str, str] = {v: k for k, v in _METRIC_TO_SET_KEY.items()}


async def _show_threshold_picker(
    callback: CallbackQuery,
    metric: str,
    current: int,
    set_prefix: str,
    allowed_metrics: set[str],
) -> None:
    """Show threshold picker keyboard for any Unraid metric."""
    if metric not in allowed_metrics or metric not in _UNRAID_METRICS:
        await callback.answer("Unknown metric")
        return

    steps, unit, label, default, _ = _UNRAID_METRICS[metric]
    set_key = _METRIC_TO_SET_KEY.get(metric, metric)

    await callback.answer()

    options = [v for v in steps if v > current] or [steps[-1]]

    row = [
        InlineKeyboardButton(
            text=f"{v}{unit}", callback_data=f"{set_prefix}:{set_key}:{v}",
        )
        for v in options
    ]
    reset_row = [InlineKeyboardButton(
        text=f"↩️ Reset to default ({default}{unit})",
        callback_data=f"{set_prefix}:{set_key}:0",
    )]

    if callback.message:
        try:
            await callback.message.answer(
                f"⚙️ Set *{label}* threshold\nCurrent: {current}{unit}",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[row, reset_row]),
            )
        except TelegramBadRequest:
            await callback.message.answer(
                f"Set {label} threshold\nCurrent: {current}{unit}",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[row, reset_row]),
            )


async def _apply_threshold(
    callback: CallbackQuery,
    metric: str,
    value: int,
    unraid_config: UnraidConfig,
    allowed_metrics: set[str],
) -> None:
    """Apply a threshold and send confirmation."""
    if metric not in allowed_metrics:
        await callback.answer("Invalid metric")
        return

    # Resolve display metadata before persisting: raising after the write leaves
    # the config changed with the user told nothing, which is how the array
    # capacity button used to fail.
    metric_key = _SET_KEY_TO_METRIC.get(metric, metric)
    if metric_key not in _UNRAID_METRICS:
        logger.error(f"No display metadata for threshold metric {metric!r}")
        await callback.answer("Unknown metric")
        return

    unraid_config.set_threshold(metric, value)

    _, unit, label, _, config_attr = _UNRAID_METRICS[metric_key]
    actual = getattr(unraid_config, config_attr)

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


def raise_limit_callback(
    resource_config: ResourceConfig,
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for 'Raise Limit' button — shows threshold options."""

    async def handler(callback: CallbackQuery) -> None:
        # Format: res_limit:container_name:metric:current_threshold
        parsed = await _parse_metric_callback(callback, default=80)
        if parsed is None:
            return
        container_name, metric, current_threshold = parsed

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
        # Format: res_set:container_name:metric:value
        parsed = await _parse_metric_callback(callback)
        if parsed is None:
            return
        container_name, metric, value = parsed

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
        minutes = await _parse_minutes_callback(callback)
        if minutes is None:
            return

        array_mute_manager.mute_array(timedelta(minutes=minutes))

        duration_str = format_duration_minutes(minutes)

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
        parts = callback.data.split(":")
        if len(parts) < 3:
            await callback.answer("Invalid callback data")
            return
        try:
            current = int(parts[2])
        except ValueError:
            current = 0
        await _show_threshold_picker(
            callback, parts[1], current, "arr_set", {"capacity", "disk_temp"},
        )

    return handler


def array_set_threshold_callback(
    unraid_config: UnraidConfig,
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for applying a selected array threshold."""

    async def handler(callback: CallbackQuery) -> None:
        if not callback.data:
            return
        parts = callback.data.split(":")
        if len(parts) < 3:
            await callback.answer("Invalid callback data")
            return
        try:
            value = int(parts[2])
        except ValueError:
            await callback.answer("Invalid threshold value")
            return
        await _apply_threshold(
            callback, parts[1], value, unraid_config, {"array_usage", "disk_temp"},
        )

    return handler


def server_mute_callback(
    server_mute_manager: Any,
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for server mute button callback handler."""

    async def handler(callback: CallbackQuery) -> None:
        minutes = await _parse_minutes_callback(callback)
        if minutes is None:
            return

        server_mute_manager.mute_server(timedelta(minutes=minutes))

        duration_str = format_duration_minutes(minutes)

        await callback.answer(f"Muted server alerts for {duration_str}")

        if callback.message:
            try:
                await callback.message.answer(
                    f"🔇 *Muted server alerts* for {duration_str}\n"
                    f"Use `/unmute-server` to unmute early.",
                    parse_mode="Markdown",
                )
            except TelegramBadRequest:
                await callback.message.answer(
                    f"🔇 Muted server alerts for {duration_str}\n"
                    f"Use /unmute-server to unmute early."
                )

    return handler


def server_threshold_callback(
    unraid_config: UnraidConfig,
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for server threshold adjustment button — shows options."""

    async def handler(callback: CallbackQuery) -> None:
        if not callback.data:
            return
        parts = callback.data.split(":")
        if len(parts) < 3:
            await callback.answer("Invalid callback data")
            return
        try:
            current = int(parts[2])
        except ValueError:
            current = 0
        await _show_threshold_picker(
            callback, parts[1], current, "srv_set", {"cpu_temp", "cpu_usage"},
        )

    return handler


def server_set_threshold_callback(
    unraid_config: UnraidConfig,
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for applying a selected server threshold."""

    async def handler(callback: CallbackQuery) -> None:
        if not callback.data:
            return
        parts = callback.data.split(":")
        if len(parts) < 3:
            await callback.answer("Invalid callback data")
            return
        try:
            value = int(parts[2])
        except ValueError:
            await callback.answer("Invalid threshold value")
            return
        await _apply_threshold(
            callback, parts[1], value, unraid_config, {"cpu_temp", "cpu_usage"},
        )

    return handler


def pull_callback(
    state: ContainerStateManager,
    controller: ContainerController,
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for the image-update digest 'Pull' button - shows a confirmation."""

    async def handler(callback: CallbackQuery) -> None:
        actual_name = await _parse_container_callback(callback, state)
        if actual_name is None:
            return
        if controller.is_protected(actual_name):
            await callback.answer(f"{actual_name} is protected", show_alert=True)
            return
        await callback.answer()
        info = state.get(actual_name)
        status = info.status if info else "unknown"
        from src.bot.control_commands import build_confirmation
        text, keyboard = build_confirmation("pull", actual_name, status)
        if callback.message:
            await callback.message.answer(text, reply_markup=keyboard, parse_mode="Markdown")

    return handler
