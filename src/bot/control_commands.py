import logging
from typing import Callable, Awaitable

from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ChatAction

from src.state import ContainerStateManager
from src.services.container_control import ContainerController
from src.utils.formatting import safe_reply, safe_edit, validate_container_name, escape_markdown

logger = logging.getLogger(__name__)

# Emoji mapping for control actions
ACTION_EMOJI = {
    "restart": "🔄",
    "stop": "🛑",
    "start": "▶️",
    "pull": "⬇️",
}

VALID_ACTIONS = {"restart", "stop", "start", "pull"}


def _find_container(state: ContainerStateManager, query: str) -> tuple[str | None, str | None]:
    """Find container by name, return (container_name, error_message)."""
    matches = state.find_by_name(query)

    if not matches:
        return None, f"❌ No container found matching '{query}'"

    if len(matches) > 1:
        names = ", ".join(m.name for m in matches)
        return None, f"Multiple matches found: {names}\n\n_Be more specific_"

    return matches[0].name, None


def _build_confirmation(action: str, container_name: str, status: str) -> tuple[str, InlineKeyboardMarkup]:
    """Build confirmation message and inline keyboard."""
    emoji = ACTION_EMOJI.get(action, "⚠️")

    text = f"""{emoji} *{action.capitalize()} {escape_markdown(container_name)}?*

Current status: {status}"""

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Confirm", callback_data=f"ctrl_confirm:{action}:{container_name}"),
            InlineKeyboardButton(text="❌ Cancel", callback_data="ctrl_cancel"),
        ]
    ])

    return text, keyboard


def _control_command(
    action: str,
    state: ContainerStateManager,
    controller: ContainerController,
) -> Callable[[Message], Awaitable[None]]:
    """Generic factory for container control command handlers."""
    async def handler(message: Message) -> None:
        text = message.text or ""
        parts = text.strip().split()

        if len(parts) < 2:
            await safe_reply(
                message,
                f"Usage: `/{action} <container>`\n\n"
                f"Example: `/{action} radarr`\n"
                f"_Partial names work: /{action} rad → radarr_",
            )
            return

        query = parts[1]
        container_name, error = _find_container(state, query)

        if error:
            await safe_reply(message, error)
            return

        if container_name is None:
            return

        if controller.is_protected(container_name):
            await message.answer(f"🔒 {container_name} is protected and cannot be controlled via Telegram")
            return

        container_info = state.get(container_name)
        status = container_info.status if container_info else "unknown"

        confirm_msg, keyboard = _build_confirmation(action, container_name, status)
        await safe_reply(message, confirm_msg, reply_markup=keyboard)

    return handler


def restart_command(
    state: ContainerStateManager,
    controller: ContainerController,
) -> Callable[[Message], Awaitable[None]]:
    """Factory for /restart command handler."""
    return _control_command("restart", state, controller)


def stop_command(
    state: ContainerStateManager,
    controller: ContainerController,
) -> Callable[[Message], Awaitable[None]]:
    """Factory for /stop command handler."""
    return _control_command("stop", state, controller)


def start_command(
    state: ContainerStateManager,
    controller: ContainerController,
) -> Callable[[Message], Awaitable[None]]:
    """Factory for /start command handler."""
    return _control_command("start", state, controller)


def pull_command(
    state: ContainerStateManager,
    controller: ContainerController,
) -> Callable[[Message], Awaitable[None]]:
    """Factory for /pull command handler."""
    return _control_command("pull", state, controller)


def create_ctrl_confirm_callback(
    state: ContainerStateManager,
    controller: ContainerController,
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for control confirmation callback handler."""

    async def handler(callback: CallbackQuery) -> None:
        data = callback.data or ""
        # Format: ctrl_confirm:{action}:{container_name}
        parts = data.split(":", 2)
        if len(parts) < 3:
            await callback.answer("Invalid callback data")
            return

        action = parts[1]
        container_name = parts[2]

        if action not in VALID_ACTIONS:
            await callback.answer(f"Unknown action: {action}")
            return

        if not validate_container_name(container_name):
            await callback.answer("Invalid container name")
            return

        if controller.is_protected(container_name):
            await callback.answer(f"🔒 {container_name} is protected")
            return

        await callback.answer()

        # Update the message to show we're executing
        emoji = ACTION_EMOJI.get(action, "⚠️")
        if isinstance(callback.message, Message):
            await safe_edit(
                callback.message,
                f"{emoji} Executing {action} on *{escape_markdown(container_name)}*...",
            )
            if callback.message.bot:
                await callback.message.bot.send_chat_action(chat_id=callback.message.chat.id, action=ChatAction.TYPING)

        # Execute the action
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

        # Update with result
        if isinstance(callback.message, Message):
            await safe_edit(callback.message, result)

    return handler


def create_ctrl_cancel_callback() -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for control cancel callback handler."""

    async def handler(callback: CallbackQuery) -> None:
        await callback.answer("Cancelled")
        if isinstance(callback.message, Message):
            await safe_edit(callback.message, "Action cancelled.")

    return handler
