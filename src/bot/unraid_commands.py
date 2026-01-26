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

            # Get array status
            array = await system_monitor.get_array_status()
            if array:
                state = array.get("state", "Unknown")
                # Use kilobytes for actual storage values (not disk counts)
                capacity_kb = array.get("capacity", {}).get("kilobytes", {})
                # Convert kilobytes to TB (divide by 1024^3)
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
                    # fsUsed and fsSize are in kilobytes
                    fs_used_kb = cache.get("fsUsed", 0) or 0
                    fs_size_kb = cache.get("fsSize", 0) or 0
                    if fs_size_kb:
                        # Convert KB to GB
                        used_gb = fs_used_kb / (1024 * 1024)
                        size_gb = fs_size_kb / (1024 * 1024)
                        pct = (fs_used_kb / fs_size_kb * 100) if fs_size_kb else 0
                        lines.append(f"*{name.title()}:* {pct:.0f}% ({used_gb:.0f}/{size_gb:.0f} GB) • {cache_temp}°C • {status}")
                    else:
                        lines.append(f"*{name.title()}:* {cache_temp}°C • {status}")

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


def _format_disk_line(disk: dict) -> str:
    """Format a single disk for display."""
    name = disk.get("name", "unknown")
    temp = disk.get("temp", 0)
    status = disk.get("status", "").replace("DISK_", "")
    size = disk.get("size", 0)

    # Convert size to TB (size is in bytes from API)
    size_tb = size / (1000 * 1000 * 1000 * 1000) if size else 0

    status_icon = "✅" if status == "OK" else "⚠️"

    if size_tb > 0:
        return f"  {status_icon} {name}: {size_tb:.1f}TB • {temp}°C • {status}"
    return f"  {status_icon} {name}: {temp}°C • {status}"


def disks_command(
    system_monitor: "UnraidSystemMonitor",
) -> Callable[[Message], Awaitable[None]]:
    """Factory for /disks command handler."""

    async def handler(message: Message) -> None:
        array = await system_monitor.get_array_status()

        if not array:
            await message.answer("💾 Disk status unavailable.")
            return

        # Get disk lists
        parities = array.get("parities", [])
        disks = array.get("disks", [])
        caches = array.get("caches", [])

        lines = ["💾 *Disk Status*\n"]

        # Parity disks
        if parities:
            lines.append("*Parity:*")
            for parity in parities:
                lines.append(_format_disk_line(parity))
            lines.append("")

        # Data disks
        if disks:
            lines.append("*Data Disks:*")
            for disk in disks:
                lines.append(_format_disk_line(disk))
            lines.append("")

        # Cache disks
        if caches:
            lines.append("*Cache:*")
            for cache in caches:
                lines.append(_format_disk_line(cache))

        await message.answer("\n".join(lines).rstrip(), parse_mode="Markdown")

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

        lines.append(f"\n*Devices:*")
        lines.append(f"  Data disks: {data_disk_count}")
        lines.append(f"  Parity: {parity_count}")
        lines.append(f"  Cache: {cache_count}")

        if issues:
            lines.append(f"\n*Issues:*")
            lines.extend(issues)

        await message.answer("\n".join(lines), parse_mode="Markdown")

    return handler
