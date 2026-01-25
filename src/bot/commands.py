from typing import Callable, Awaitable

from aiogram.types import Message

from src.state import ContainerStateManager


HELP_TEXT = """📋 *Available Commands*

/status - Container status overview
/status <name> - Details for specific container
/help - Show this help message

_Partial container names work: /status rad → radarr_"""


def help_command(state: ContainerStateManager) -> Callable[[Message], Awaitable[None]]:
    """Factory for /help command handler."""
    async def handler(message: Message) -> None:
        await message.answer(HELP_TEXT, parse_mode="Markdown")
    return handler


def format_status_summary(state: ContainerStateManager) -> str:
    """Format container status summary."""
    summary = state.get_summary()
    all_containers = state.get_all()

    stopped = [c.name for c in all_containers if c.status != "running"]
    unhealthy = [c.name for c in all_containers if c.health == "unhealthy"]

    lines = [
        "📊 *Container Status*",
        "",
        f"✅ Running: {summary['running']}",
        f"🔴 Stopped: {summary['stopped']}",
        f"⚠️ Unhealthy: {summary['unhealthy']}",
    ]

    if stopped:
        lines.append("")
        lines.append(f"*Stopped:* {', '.join(stopped)}")

    if unhealthy:
        lines.append(f"*Unhealthy:* {', '.join(unhealthy)}")

    if not stopped and not unhealthy:
        lines.append("")
        lines.append("_All containers healthy_ ✨")
    else:
        lines.append("")
        lines.append("_Use /status <name> for details_")

    return "\n".join(lines)


def status_command(state: ContainerStateManager) -> Callable[[Message], Awaitable[None]]:
    """Factory for /status command handler."""
    async def handler(message: Message) -> None:
        text = message.text or ""
        parts = text.strip().split(maxsplit=1)

        if len(parts) == 1:
            # No argument - show summary
            response = format_status_summary(state)
            await message.answer(response, parse_mode="Markdown")
        else:
            # Has argument - will handle in next task
            await message.answer("Container details not yet implemented")

    return handler
