import asyncio
import logging
from typing import Callable, Awaitable, TYPE_CHECKING

from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
import docker

from src.models import ContainerInfo
from src.state import ContainerStateManager
from src.utils.formatting import format_bytes, format_uptime, escape_markdown, safe_reply, safe_edit, truncate_callback_data
from src.utils.sanitize import sanitize_logs_for_display
from src.bot.resources_command import format_progress_bar

if TYPE_CHECKING:
    from src.monitors.resource_monitor import ResourceMonitor

logger = logging.getLogger(__name__)


# Help sections — each key is a callback suffix, value is (emoji, title, content)
_HELP_SECTIONS: dict[str, tuple[str, str, str]] = {
    "containers": (
        "📦",
        "Containers",
        "/status [name] — Container status summary or details\n"
        "/resources [name] — CPU & memory usage\n"
        "/logs <name> [n] — View recent container logs\n"
        "/diagnose <name> — AI-powered log analysis\n"
        "/restart <name> — Restart a container\n"
        "/stop <name> — Stop a container\n"
        "/start <name> — Start a container\n"
        "/pull <name> — Pull latest image & recreate\n"
        "\n_Partial names work: /status rad → radarr_",
    ),
    "server": (
        "🖥",
        "Unraid Server",
        "/server — Quick server overview\n"
        "/server detailed — Full server stats\n"
        "/array — Array status and disk health\n"
        "/disks — Individual disk usage details\n"
        "/cancel-kill — Cancel pending memory kill",
    ),
    "alerts": (
        "🔔",
        "Alerts & Ignores",
        "/mute <name> <dur> — Mute container alerts\n"
        "/unmute <name> — Unmute container\n"
        "/mute-server <dur> — Mute server alerts\n"
        "/unmute-server — Unmute server\n"
        "/mute-array <dur> — Mute array alerts\n"
        "/unmute-array — Unmute array\n"
        "/mutes — List active mutes\n"
        "/ignore — Ignore error patterns\n"
        "/ignores — List active ignores\n"
        "\n_Reply /diagnose to crash alerts for AI analysis_\n"
        "_Click \"Ignore Similar\" on alerts for smart patterns_",
    ),
    "setup": (
        "⚙️",
        "Setup & Management",
        "/setup — Re-run the setup wizard\n"
        "/cancel — Exit the setup wizard\n"
        "/manage — Dashboard with status, resources, ignores & mutes\n"
        "/health — Bot version, uptime & monitor status\n"
        "/model — Switch LLM provider and model at runtime\n"
        "\n_AI features need at least one LLM key configured_",
    ),
}

HELP_OVERVIEW = "📋 *Commands*\n\nChoose a category:"


def _build_help_keyboard() -> InlineKeyboardMarkup:
    """Build the help category selection keyboard."""
    buttons = []
    for key, (emoji, title, _) in _HELP_SECTIONS.items():
        buttons.append([
            InlineKeyboardButton(text=f"{emoji} {title}", callback_data=f"help:{key}")
        ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def help_command() -> Callable[[Message], Awaitable[None]]:
    """Factory for /help command handler."""
    async def handler(message: Message) -> None:
        await safe_reply(message, HELP_OVERVIEW, reply_markup=_build_help_keyboard())
    return handler


def help_section_callback() -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for help section button callback."""
    async def handler(callback: CallbackQuery) -> None:
        data = callback.data or ""
        section_key = data.split(":", 1)[1] if ":" in data else ""

        section = _HELP_SECTIONS.get(section_key)
        if not section:
            await callback.answer("Unknown section")
            return

        emoji, title, content = section
        text = f"{emoji} *{title}*\n\n{content}"

        await callback.answer()
        if callback.message:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Back", callback_data="help:back")]
            ])
            await safe_edit(callback.message, text, reply_markup=keyboard)

    return handler


def help_back_callback() -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for help back button callback."""
    async def handler(callback: CallbackQuery) -> None:
        await callback.answer()
        if callback.message:
            await safe_edit(callback.message, HELP_OVERVIEW, reply_markup=_build_help_keyboard())

    return handler


def format_status_summary(state: ContainerStateManager) -> str:
    """Format container status summary."""
    summary = state.get_summary()
    all_containers = state.get_all()

    stopped = [escape_markdown(c.name) for c in all_containers if c.status != "running"]
    unhealthy = [escape_markdown(c.name) for c in all_containers if c.health == "unhealthy"]

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


async def format_container_details(
    container: ContainerInfo,
    resource_monitor: "ResourceMonitor | None" = None,
) -> str:
    """Format detailed container info with optional resource stats."""
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
    ]

    # Show uptime for running containers
    if container.status == "running" and container.uptime_seconds is not None:
        lines.append(f"Uptime: {format_uptime(container.uptime_seconds)}")

    lines.append(f"Image: `{container.image}`")

    if container.started_at:
        lines.append(f"Started: {container.started_at.strftime('%Y-%m-%d %H:%M:%S')}")

    # Add resource stats for running containers
    if container.status == "running" and resource_monitor is not None:
        try:
            stats = await resource_monitor.get_container_stats(container.name)
            if stats is not None:
                cpu_max = stats.cpu_cores * 100
                cpu_bar = format_progress_bar(stats.cpu_percent, max_percent=cpu_max)
                mem_bar = format_progress_bar(stats.memory_percent)

                lines.append("")
                lines.append("📊 *Resources*")
                lines.append(
                    f"CPU:    {stats.cpu_percent:5.1f}% `{cpu_bar}` of {cpu_max}%"
                )
                lines.append(
                    f"Memory: {stats.memory_percent:5.1f}% `{mem_bar}` "
                    f"({stats.memory_display} / {stats.memory_limit_display})"
                )
                lines.append(
                    f"Net I/O: {format_bytes(stats.net_rx_bytes)} ↓ / "
                    f"{format_bytes(stats.net_tx_bytes)} ↑"
                )
                lines.append(
                    f"Block I/O: {format_bytes(stats.block_read_bytes)} read / "
                    f"{format_bytes(stats.block_write_bytes)} write"
                )
                if stats.pids > 0:
                    lines.append(f"PIDs: {stats.pids}")
        except Exception as e:
            logger.warning(f"Failed to get resource stats for {container.name}: {e}")

    return "\n".join(lines)


def status_command(
    state: ContainerStateManager,
    resource_monitor: "ResourceMonitor | None" = None,
) -> Callable[[Message], Awaitable[None]]:
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
                await message.answer(f"❌ No container found matching '{query}'")
                return
            elif len(matches) == 1:
                response = await format_container_details(matches[0], resource_monitor)
                name = matches[0].name
                keyboard = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(text="📋 Logs", callback_data=truncate_callback_data("logs:", f"{name}:50")),
                            InlineKeyboardButton(text="🔍 Diagnose", callback_data=truncate_callback_data("diagnose:", name)),
                            InlineKeyboardButton(text="🔄 Restart", callback_data=truncate_callback_data("restart:", name)),
                        ],
                    ]
                )
                await safe_reply(message, response, reply_markup=keyboard)
                return
            else:
                names = ", ".join(escape_markdown(m.name) for m in matches)
                response = f"Multiple matches found: {names}\n\n_Be more specific_"

        await safe_reply(message, response)

    return handler


def logs_command(
    state: ContainerStateManager,
    docker_client: docker.DockerClient,
    max_lines: int = 100,
    max_chars: int = 4000,
) -> Callable[[Message], Awaitable[None]]:
    """Factory for /logs command handler."""
    async def handler(message: Message) -> None:
        text = message.text or ""
        parts = text.strip().split()

        if len(parts) < 2:
            await safe_reply(
                message,
                "Usage: `/logs <container> [lines]`\n\n"
                "Example: `/logs radarr 50`\n"
                "_Partial names work: /logs rad → radarr_",
            )
            return

        container_name = parts[1]

        # Parse optional line count
        try:
            lines = int(parts[2]) if len(parts) > 2 else 20
        except ValueError:
            lines = 20

        # Cap at reasonable limit
        lines = min(lines, max_lines)

        # Find container
        matches = state.find_by_name(container_name)

        if not matches:
            await message.answer(f"❌ No container found matching '{container_name}'")
            return

        if len(matches) > 1:
            names = ", ".join(escape_markdown(m.name) for m in matches)
            await safe_reply(message, f"Multiple matches found: {names}\n\n_Be more specific_")
            return

        container = matches[0]

        try:
            docker_container = await asyncio.to_thread(docker_client.containers.get, container.name)
            log_bytes = await asyncio.to_thread(docker_container.logs, tail=lines, timestamps=False)
            log_text = log_bytes.decode("utf-8", errors="replace")

            # Account for header/footer when truncating to stay under Telegram 4096 char limit
            safe_name = escape_markdown(container.name)
            header = f"📋 *Logs: {safe_name}* (last {lines} lines)\n\n```\n"
            footer = "\n```"
            available = max_chars - len(header) - len(footer)

            # Truncate if too long for Telegram
            if len(log_text) > available:
                log_text = log_text[-available:]
                log_text = "...(truncated)\n" + log_text

            # Sanitize to remove sensitive data before display
            log_text = sanitize_logs_for_display(log_text)

            response = f"{header}{log_text}{footer}"
            await safe_reply(message, response)

        except docker.errors.NotFound:
            await message.answer(f"❌ Container '{container.name}' not found in Docker")
        except Exception as e:
            logger.error(f"Error getting logs for {container.name}: {e}", exc_info=True)
            await message.answer("❌ Error getting logs. Check bot logs for details.")

    return handler
