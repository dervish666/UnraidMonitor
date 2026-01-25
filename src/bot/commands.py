from typing import Callable, Awaitable

from aiogram.types import Message

from src.models import ContainerInfo
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


def format_container_details(container: ContainerInfo) -> str:
    """Format detailed container info."""
    health_emoji = {
        "healthy": "✅",
        "unhealthy": "⚠️",
        "starting": "🔄",
        None: "➖",
    }
    status_emoji = "🟢" if container.status == "running" else "🔴"

    lines = [
        f"*{container.name}*",
        "",
        f"Status: {status_emoji} {container.status}",
        f"Health: {health_emoji.get(container.health, '➖')} {container.health or 'no healthcheck'}",
        f"Image: `{container.image}`",
    ]

    if container.started_at:
        lines.append(f"Started: {container.started_at.strftime('%Y-%m-%d %H:%M:%S')}")

    return "\n".join(lines)


def status_command(state: ContainerStateManager) -> Callable[[Message], Awaitable[None]]:
    """Factory for /status command handler."""
    async def handler(message: Message) -> None:
        text = message.text or ""
        parts = text.strip().split(maxsplit=1)

        if len(parts) == 1:
            # No argument - show summary
            response = format_status_summary(state)
        else:
            # Search for container
            query = parts[1].strip()
            matches = state.find_by_name(query)

            if not matches:
                response = f"❌ No container found matching '{query}'"
            elif len(matches) == 1:
                response = format_container_details(matches[0])
            else:
                names = ", ".join(m.name for m in matches)
                response = f"Multiple matches found: {names}\n\n_Be more specific_"

        await message.answer(response, parse_mode="Markdown")

    return handler
