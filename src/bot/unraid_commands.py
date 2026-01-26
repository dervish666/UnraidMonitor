"""Unraid server monitoring commands."""

import logging
from datetime import datetime, timezone
from typing import Callable, Awaitable, TYPE_CHECKING

from aiogram.types import Message

from src.alerts.mute_manager import parse_duration

if TYPE_CHECKING:
    from src.unraid.monitors.system_monitor import UnraidSystemMonitor
    from src.alerts.server_mute_manager import ServerMuteManager

logger = logging.getLogger(__name__)


def format_uptime(uptime_str: str) -> str:
    """Format ISO timestamp uptime to human-readable format.

    Args:
        uptime_str: Either an ISO timestamp (boot time) or already formatted string.

    Returns:
        Human-readable uptime like "24 days, 19 hours".
    """
    if not uptime_str:
        return "Unknown"

    # Try to parse as ISO timestamp
    try:
        # Handle ISO format like "2026-01-02T18:14:24.693Z"
        boot_time = datetime.fromisoformat(uptime_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta = now - boot_time

        days = delta.days
        hours = delta.seconds // 3600
        minutes = (delta.seconds % 3600) // 60

        parts = []
        if days > 0:
            parts.append(f"{days} day{'s' if days != 1 else ''}")
        if hours > 0:
            parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
        if not parts and minutes > 0:
            parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
        if not parts:
            return "Just started"

        return ", ".join(parts)
    except (ValueError, TypeError):
        # Already formatted or unknown format
        return uptime_str


def server_command(
    system_monitor: "UnraidSystemMonitor",
) -> Callable[[Message], Awaitable[None]]:
    """Factory for /server command handler."""

    async def handler(message: Message) -> None:
        text = (message.text or "").strip()
        detailed = "detailed" in text.lower()

        metrics = await system_monitor.get_current_metrics()

        if not metrics:
            await message.answer("🖥️ Unraid server unavailable or not configured.")
            return

        cpu = metrics.get("cpu_percent", 0)
        temp = metrics.get("cpu_temperature", 0)
        memory = metrics.get("memory_percent", 0)
        memory_gb = metrics.get("memory_used", 0) / (1024**3)
        uptime = format_uptime(metrics.get("uptime", ""))

        if detailed:
            swap = metrics.get("swap_percent", 0)
            power = metrics.get("cpu_power", 0)

            lines = [
                "🖥️ *Unraid Server Status*\n",
                f"*CPU:* {cpu:.1f}%",
                f"*CPU Temp:* {temp:.1f}°C",
            ]

            if power:
                lines.append(f"*CPU Power:* {power:.1f}W")

            lines.extend([
                f"\n*Memory:* {memory:.1f}% ({memory_gb:.1f} GB)",
                f"*Swap:* {swap:.1f}%",
                f"\n*Uptime:* {uptime}",
            ])

            await message.answer("\n".join(lines), parse_mode="Markdown")
        else:
            # Compact summary
            response = (
                f"🖥️ *Unraid Server*\n\n"
                f"CPU: {cpu:.1f}% ({temp:.1f}°C) • "
                f"RAM: {memory:.1f}%\n"
                f"Uptime: {uptime}"
            )
            await message.answer(response, parse_mode="Markdown")

    return handler


def mute_server_command(
    mute_manager: "ServerMuteManager",
) -> Callable[[Message], Awaitable[None]]:
    """Factory for /mute-server command handler."""

    async def handler(message: Message) -> None:
        text = (message.text or "").strip()
        parts = text.split()

        if len(parts) < 2:
            await message.answer(
                "Usage: `/mute-server <duration>`\n\n"
                "Examples: `2h`, `30m`, `24h`\n\n"
                "This mutes ALL server alerts (system, array, UPS).",
                parse_mode="Markdown",
            )
            return

        duration_str = parts[1]
        duration = parse_duration(duration_str)

        if not duration:
            await message.answer(
                f"Invalid duration: `{duration_str}`\n"
                "Use format like `15m`, `2h`, `24h`",
                parse_mode="Markdown",
            )
            return

        expiry = mute_manager.mute_server(duration)
        time_str = expiry.strftime("%H:%M")

        await message.answer(
            f"🔇 *Muted all server alerts* until {time_str}\n\n"
            f"System, array, and UPS alerts suppressed.\n"
            f"Use `/unmute-server` to unmute early.",
            parse_mode="Markdown",
        )

    return handler


def unmute_server_command(
    mute_manager: "ServerMuteManager",
) -> Callable[[Message], Awaitable[None]]:
    """Factory for /unmute-server command handler."""

    async def handler(message: Message) -> None:
        if mute_manager.unmute_server():
            await message.answer(
                "🔔 *Unmuted all server alerts*\n\n"
                "System, array, and UPS alerts are now enabled.",
                parse_mode="Markdown",
            )
        else:
            await message.answer("Server alerts are not currently muted.")

    return handler
