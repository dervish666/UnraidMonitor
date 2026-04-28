"""Unraid server monitoring commands."""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable, TYPE_CHECKING

from aiogram.types import Message
from aiogram.enums import ChatAction

from src.alerts.mute_manager import parse_duration
from src.utils.formatting import truncate_message, safe_reply, format_mute_expiry

if TYPE_CHECKING:
    from src.unraid.monitors.system_monitor import UnraidSystemMonitor
    from src.unraid.monitors.array_monitor import ArrayMonitor
    from src.alerts.server_mute_manager import ServerMuteManager
    from src.alerts.array_mute_manager import ArrayMuteManager

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


async def format_server_brief(system_monitor: "UnraidSystemMonitor") -> str | None:
    """Format brief server status.

    Returns:
        Formatted string or None if unavailable.
    """
    metrics = await system_monitor.get_current_metrics()

    if not metrics:
        return None

    cpu = metrics.get("cpu_percent") or 0
    temp = metrics.get("cpu_temperature")
    memory = metrics.get("memory_percent") or 0
    uptime = format_uptime(metrics.get("uptime", ""))

    temp_str = f" ({temp:.1f}°C)" if temp is not None else ""
    return (
        f"🖥️ *Unraid Server*\n"
        f"CPU: {cpu:.1f}%{temp_str} • RAM: {memory:.1f}%\n"
        f"Uptime: {uptime}"
    )


async def format_server_detailed(system_monitor: "UnraidSystemMonitor") -> str | None:
    """Format detailed server status.

    Returns:
        Formatted string or None if unavailable.
    """
    # Fetch metrics and array status in parallel
    metrics, array = await asyncio.gather(
        system_monitor.get_current_metrics(),
        system_monitor.get_array_status(),
    )

    if not metrics:
        return None

    cpu = metrics.get("cpu_percent") or 0
    temp = metrics.get("cpu_temperature")
    memory = metrics.get("memory_percent") or 0
    memory_gb = (metrics.get("memory_used") or 0) / (1024**3)
    uptime = format_uptime(metrics.get("uptime", ""))

    lines = [
        "🖥️ *Unraid Server Status*\n",
        f"*CPU:* {cpu:.1f}%",
    ]

    if temp is not None:
        lines.append(f"*CPU Temp:* {temp:.1f}°C")

    lines.extend([
        f"\n*Memory:* {memory:.1f}% ({memory_gb:.1f} GB)",
        f"\n*Uptime:* {uptime}",
    ])
    if array:
        state = array.get("state", "Unknown")
        capacity_kb = array.get("capacity", {}).get("kilobytes", {})
        kb_to_tb = 1024 * 1024 * 1024
        used_tb = float(capacity_kb.get("used", 0)) / kb_to_tb
        total_tb = float(capacity_kb.get("total", 0)) / kb_to_tb
        free_tb = float(capacity_kb.get("free", 0)) / kb_to_tb

        lines.append(f"\n*Array:* {state}")
        if total_tb > 0:
            lines.append(f"*Storage:* {used_tb:.1f} / {total_tb:.1f} TB ({free_tb:.1f} TB free)")

        # Cache info
        caches = array.get("caches", [])
        for cache in caches:
            name = cache.get("name", "cache")
            cache_temp = cache.get("temp", 0)
            status = cache.get("status", "").replace("DISK_", "")
            fs_used_kb = cache.get("fsUsed", 0) or 0
            fs_size_kb = cache.get("fsSize", 0) or 0
            if fs_size_kb:
                used_gb = fs_used_kb / (1024 * 1024)
                size_gb = fs_size_kb / (1024 * 1024)
                pct = (fs_used_kb / fs_size_kb * 100) if fs_size_kb else 0
                lines.append(f"*{name.title()}:* {pct:.0f}% ({used_gb:.0f}/{size_gb:.0f} GB) • {cache_temp}°C • {status}")
            else:
                lines.append(f"*{name.title()}:* {cache_temp}°C • {status}")

    return "\n".join(lines)


async def format_disks(system_monitor: "UnraidSystemMonitor") -> str | None:
    """Format disk status.

    Returns:
        Formatted string or None if unavailable.
    """
    array = await system_monitor.get_array_status()

    if not array:
        return None

    parities = array.get("parities", [])
    disks = array.get("disks", [])
    caches = array.get("caches", [])

    lines = ["💾 *Disk Status*\n"]

    if parities:
        lines.append("*Parity:*")
        for parity in parities:
            lines.append(_format_disk_line(parity))
        lines.append("")

    if disks:
        lines.append("*Data Disks:*")
        for disk in disks:
            lines.append(_format_disk_line(disk))
        lines.append("")

    if caches:
        lines.append("*Cache:*")
        for cache in caches:
            lines.append(_format_disk_line(cache))

    return "\n".join(lines).rstrip()


def server_command(
    system_monitor: "UnraidSystemMonitor",
) -> Callable[[Message], Awaitable[None]]:
    """Factory for /server command handler."""

    async def handler(message: Message) -> None:
        text = (message.text or "").strip()
        detailed = "detailed" in text.lower()

        if message.bot:
            await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

        if detailed:
            response = await format_server_detailed(system_monitor)
        else:
            response = await format_server_brief(system_monitor)

        if response:
            await safe_reply(message, response)
        else:
            await message.answer("🖥️ Unraid server unavailable or not configured.")

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

        await safe_reply(
            message,
            f"🔇 *Muted all server alerts* {format_mute_expiry(expiry)}\n\n"
            f"System, array, and UPS alerts suppressed.\n"
            f"Use `/unmute-server` to unmute early.",
        )

    return handler


def unmute_server_command(
    mute_manager: "ServerMuteManager",
) -> Callable[[Message], Awaitable[None]]:
    """Factory for /unmute-server command handler."""

    async def handler(message: Message) -> None:
        if mute_manager.unmute_server():
            await safe_reply(
                message,
                "🔔 *Unmuted all server alerts*\n\n"
                "System, array, and UPS alerts are now enabled.",
            )
        else:
            await message.answer("Server alerts are not currently muted.")

    return handler


def mute_array_command(
    mute_manager: "ArrayMuteManager",
) -> Callable[[Message], Awaitable[None]]:
    """Factory for /mute-array command handler."""

    async def handler(message: Message) -> None:
        text = (message.text or "").strip()
        parts = text.split()

        if len(parts) < 2:
            await message.answer(
                "Usage: `/mute-array <duration>`\n\n"
                "Examples: `2h`, `30m`, `24h`\n\n"
                "This mutes array alerts (disk/parity warnings).",
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

        expiry = mute_manager.mute_array(duration)

        await safe_reply(
            message,
            f"🔇 *Muted array alerts* {format_mute_expiry(expiry)}\n\n"
            f"Disk and parity alerts suppressed.\n"
            f"Use `/unmute-array` to unmute early.",
        )

    return handler


def unmute_array_command(
    mute_manager: "ArrayMuteManager",
    array_monitor: "ArrayMonitor | None" = None,
) -> Callable[[Message], Awaitable[None]]:
    """Factory for /unmute-array command handler."""

    async def handler(message: Message) -> None:
        if mute_manager.unmute_array():
            if array_monitor is not None:
                array_monitor.clear_alert_state()
            await safe_reply(
                message,
                "🔔 *Unmuted array alerts*\n\n"
                "Disk and parity alerts are now enabled.",
            )
        else:
            await message.answer("Array alerts are not currently muted.")

    return handler


def _format_disk_line(disk: dict[str, Any]) -> str:
    """Format a single disk for display."""
    name = disk.get("name", "unknown")
    temp = disk.get("temp", 0)
    status = disk.get("status", "").replace("DISK_", "")
    try:
        size_kb = int(disk.get("size", 0))
    except (ValueError, TypeError):
        size_kb = 0

    # Convert size from kilobytes to TB (binary: 1 TiB = 1024^3 KB)
    size_tb = size_kb / (1024**3) if size_kb else 0

    status_icon = "✅" if status == "OK" else "⚠️"

    if size_tb > 0:
        return f"  {status_icon} {name}: {size_tb:.1f}TB • {temp}°C • {status}"
    return f"  {status_icon} {name}: {temp}°C • {status}"


def disks_command(
    system_monitor: "UnraidSystemMonitor",
) -> Callable[[Message], Awaitable[None]]:
    """Factory for /disks command handler."""

    async def handler(message: Message) -> None:
        if message.bot:
            await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
        response = await format_disks(system_monitor)

        if response:
            await safe_reply(message, truncate_message(response))
        else:
            await message.answer("💾 Disk status unavailable.")

    return handler


def array_command(
    system_monitor: "UnraidSystemMonitor",
) -> Callable[[Message], Awaitable[None]]:
    """Factory for /array command handler."""

    async def handler(message: Message) -> None:
        array = await system_monitor.get_array_status()

        if not array:
            await message.answer("💾 Array status unavailable.")
            return

        state = array.get("state", "Unknown")
        capacity_kb = array.get("capacity", {}).get("kilobytes", {})

        # Convert kilobytes to TB
        kb_to_tb = 1024 * 1024 * 1024
        used_tb = float(capacity_kb.get("used", 0)) / kb_to_tb
        total_tb = float(capacity_kb.get("total", 0)) / kb_to_tb
        free_tb = float(capacity_kb.get("free", 0)) / kb_to_tb

        # Calculate percentage
        percent_used = (used_tb / total_tb * 100) if total_tb > 0 else 0

        # Count devices
        disks = array.get("disks", [])
        parities = array.get("parities", [])
        caches = array.get("caches", [])

        data_disk_count = len(disks)
        parity_count = len(parities)
        cache_count = len(caches)

        # Check for issues
        issues = []
        for disk in disks:
            if disk.get("status") != "DISK_OK":
                issues.append(f"  ⚠️ {disk.get('name', 'unknown')}: {disk.get('status', 'UNKNOWN').replace('DISK_', '')}")

        for parity in parities:
            if parity.get("status") != "DISK_OK":
                issues.append(f"  ⚠️ {parity.get('name', 'unknown')}: {parity.get('status', 'UNKNOWN').replace('DISK_', '')}")

        for cache in caches:
            if cache.get("status") != "DISK_OK":
                issues.append(f"  ⚠️ {cache.get('name', 'unknown')}: {cache.get('status', 'UNKNOWN').replace('DISK_', '')}")

        # Build response
        lines = [
            "💾 *Array Status*\n",
            f"*State:* {state}",
        ]

        if total_tb > 0:
            lines.append(f"*Storage:* {used_tb:.1f} / {total_tb:.1f} TB ({percent_used:.0f}% used)")
            lines.append(f"*Free:* {free_tb:.1f} TB")

        lines.append("\n*Devices:*")
        lines.append(f"  Data disks: {data_disk_count}")
        lines.append(f"  Parity: {parity_count}")
        lines.append(f"  Cache: {cache_count}")

        if issues:
            lines.append("\n*Issues:*")
            lines.extend(issues)

        await safe_reply(message, "\n".join(lines))

    return handler
