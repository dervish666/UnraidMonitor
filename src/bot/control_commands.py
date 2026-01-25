import logging
from typing import Callable, Awaitable

from aiogram.types import Message

from src.state import ContainerStateManager
from src.bot.confirmation import ConfirmationManager
from src.services.container_control import ContainerController

logger = logging.getLogger(__name__)


def _find_container(state: ContainerStateManager, query: str) -> tuple[str | None, str | None]:
    """Find container by name, return (container_name, error_message)."""
    matches = state.find_by_name(query)

    if not matches:
        return None, f"❌ No container found matching '{query}'"

    if len(matches) > 1:
        names = ", ".join(m.name for m in matches)
        return None, f"Multiple matches found: {names}\n\n_Be more specific_"

    return matches[0].name, None


def _format_confirmation_message(action: str, container_name: str, status: str) -> str:
    """Format the confirmation request message."""
    action_emoji = {
        "restart": "🔄",
        "stop": "🛑",
        "start": "▶️",
        "pull": "⬇️",
    }
    emoji = action_emoji.get(action, "⚠️")

    return f"""{emoji} *{action.capitalize()} {container_name}?*

Current status: {status}

Reply 'yes' to confirm (expires in 60s)"""


def restart_command(
    state: ContainerStateManager,
    controller: ContainerController,
    confirmation: ConfirmationManager,
) -> Callable[[Message], Awaitable[None]]:
    """Factory for /restart command handler."""
    async def handler(message: Message) -> None:
        text = message.text or ""
        parts = text.strip().split()

        if len(parts) < 2:
            await message.answer("Usage: /restart <container>\n\nExample: /restart radarr")
            return

        query = parts[1]
        container_name, error = _find_container(state, query)

        if error:
            await message.answer(error, parse_mode="Markdown")
            return

        if controller.is_protected(container_name):
            await message.answer(f"🔒 {container_name} is protected and cannot be controlled via Telegram")
            return

        container_info = state.get(container_name)
        status = container_info.status if container_info else "unknown"

        user_id = message.from_user.id
        confirmation.request(user_id, action="restart", container_name=container_name)

        await message.answer(
            _format_confirmation_message("restart", container_name, status),
            parse_mode="Markdown",
        )

    return handler


def stop_command(
    state: ContainerStateManager,
    controller: ContainerController,
    confirmation: ConfirmationManager,
) -> Callable[[Message], Awaitable[None]]:
    """Factory for /stop command handler."""
    async def handler(message: Message) -> None:
        text = message.text or ""
        parts = text.strip().split()

        if len(parts) < 2:
            await message.answer("Usage: /stop <container>\n\nExample: /stop radarr")
            return

        query = parts[1]
        container_name, error = _find_container(state, query)

        if error:
            await message.answer(error, parse_mode="Markdown")
            return

        if controller.is_protected(container_name):
            await message.answer(f"🔒 {container_name} is protected and cannot be controlled via Telegram")
            return

        container_info = state.get(container_name)
        status = container_info.status if container_info else "unknown"

        user_id = message.from_user.id
        confirmation.request(user_id, action="stop", container_name=container_name)

        await message.answer(
            _format_confirmation_message("stop", container_name, status),
            parse_mode="Markdown",
        )

    return handler


def start_command(
    state: ContainerStateManager,
    controller: ContainerController,
    confirmation: ConfirmationManager,
) -> Callable[[Message], Awaitable[None]]:
    """Factory for /start command handler."""
    async def handler(message: Message) -> None:
        text = message.text or ""
        parts = text.strip().split()

        if len(parts) < 2:
            await message.answer("Usage: /start <container>\n\nExample: /start radarr")
            return

        query = parts[1]
        container_name, error = _find_container(state, query)

        if error:
            await message.answer(error, parse_mode="Markdown")
            return

        if controller.is_protected(container_name):
            await message.answer(f"🔒 {container_name} is protected and cannot be controlled via Telegram")
            return

        container_info = state.get(container_name)
        status = container_info.status if container_info else "unknown"

        user_id = message.from_user.id
        confirmation.request(user_id, action="start", container_name=container_name)

        await message.answer(
            _format_confirmation_message("start", container_name, status),
            parse_mode="Markdown",
        )

    return handler


def pull_command(
    state: ContainerStateManager,
    controller: ContainerController,
    confirmation: ConfirmationManager,
) -> Callable[[Message], Awaitable[None]]:
    """Factory for /pull command handler."""
    async def handler(message: Message) -> None:
        text = message.text or ""
        parts = text.strip().split()

        if len(parts) < 2:
            await message.answer("Usage: /pull <container>\n\nExample: /pull radarr")
            return

        query = parts[1]
        container_name, error = _find_container(state, query)

        if error:
            await message.answer(error, parse_mode="Markdown")
            return

        if controller.is_protected(container_name):
            await message.answer(f"🔒 {container_name} is protected and cannot be controlled via Telegram")
            return

        container_info = state.get(container_name)
        status = container_info.status if container_info else "unknown"

        user_id = message.from_user.id
        confirmation.request(user_id, action="pull", container_name=container_name)

        await message.answer(
            _format_confirmation_message("pull", container_name, status),
            parse_mode="Markdown",
        )

    return handler


def create_confirm_handler(
    controller: ContainerController,
    confirmation: ConfirmationManager,
) -> Callable[[Message], Awaitable[None]]:
    """Factory for confirmation handler (responds to 'yes')."""
    async def handler(message: Message) -> None:
        user_id = message.from_user.id
        pending = confirmation.confirm(user_id)

        if pending is None:
            await message.answer("❌ No pending action. Use /restart, /stop, /start, or /pull first.")
            return

        action = pending.action
        container_name = pending.container_name

        await message.answer(f"🔄 Executing {action} on {container_name}...")

        if action == "restart":
            result = await controller.restart(container_name)
        elif action == "stop":
            result = await controller.stop(container_name)
        elif action == "start":
            result = await controller.start(container_name)
        elif action == "pull":
            result = await controller.pull_and_recreate(container_name)
        else:
            result = f"❌ Unknown action: {action}"

        await message.answer(result)

    return handler
