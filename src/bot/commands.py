from typing import Callable, Awaitable

from aiogram.types import Message
import docker

from src.models import ContainerInfo
from src.state import ContainerStateManager


HELP_TEXT = """📋 *Available Commands*

/status - Container status overview
/status <name> - Details for specific container
/logs <name> [n] - Last n log lines (default 20)
/diagnose <name> [n] - AI analysis of container logs
/restart <name> - Restart a container
/stop <name> - Stop a container
/start <name> - Start a container
/pull <name> - Pull latest image and recreate
/help - Show this help message

_Partial container names work: /status rad → radarr_
_Control commands require confirmation_
_Reply /diagnose to a crash alert for quick analysis_"""


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


def logs_command(
    state: ContainerStateManager,
    docker_client: docker.DockerClient,
) -> Callable[[Message], Awaitable[None]]:
    """Factory for /logs command handler."""
    async def handler(message: Message) -> None:
        text = message.text or ""
        parts = text.strip().split()

        if len(parts) < 2:
            await message.answer("Usage: /logs <container> [lines]\n\nExample: /logs radarr 50")
            return

        container_name = parts[1]

        # Parse optional line count
        try:
            lines = int(parts[2]) if len(parts) > 2 else 20
        except ValueError:
            lines = 20

        # Cap at reasonable limit
        lines = min(lines, 100)

        # Find container
        matches = state.find_by_name(container_name)

        if not matches:
            await message.answer(f"❌ No container found matching '{container_name}'")
            return

        if len(matches) > 1:
            names = ", ".join(m.name for m in matches)
            await message.answer(f"Multiple matches found: {names}\n\n_Be more specific_", parse_mode="Markdown")
            return

        container = matches[0]

        try:
            docker_container = docker_client.containers.get(container.name)
            log_bytes = docker_container.logs(tail=lines, timestamps=False)
            log_text = log_bytes.decode("utf-8", errors="replace")

            # Truncate if too long for Telegram
            if len(log_text) > 4000:
                log_text = log_text[-4000:]
                log_text = "...(truncated)\n" + log_text

            response = f"📋 *Logs: {container.name}* (last {lines} lines)\n\n```\n{log_text}\n```"
            await message.answer(response, parse_mode="Markdown")

        except docker.errors.NotFound:
            await message.answer(f"❌ Container '{container.name}' not found in Docker")
        except Exception as e:
            await message.answer(f"❌ Error getting logs: {e}")

    return handler
