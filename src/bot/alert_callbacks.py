"""Callback handlers for alert action buttons."""

import asyncio
import logging
from datetime import timedelta
from typing import Callable, Awaitable, Any, TYPE_CHECKING

from aiogram.types import CallbackQuery
from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramBadRequest
import docker

from src.state import ContainerStateManager
from src.services.container_control import ContainerController
from src.services.diagnostic import DiagnosticService
from src.utils.formatting import validate_container_name, escape_markdown
from src.utils.sanitize import sanitize_logs_for_display

if TYPE_CHECKING:
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

        if callback.message:
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

        if callback.message:
            if success:
                # Edit the original message to show the result
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

        if callback.message:
            try:
                await callback.message.edit_text(
                    f"💾 Declined restart of {container_name}. It will stay stopped."
                )
            except TelegramBadRequest:
                await callback.message.answer(f"Won't restart {container_name}.")

    return handler
