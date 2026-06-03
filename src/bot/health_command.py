"""Bot health and status command."""

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable, TYPE_CHECKING

from aiogram.types import Message

from src import __version__ as _FALLBACK_VERSION
from src.utils.formatting import safe_reply

if TYPE_CHECKING:
    from src.monitors.docker_events import DockerEventMonitor
    from src.monitors.log_watcher import LogWatcher
    from src.monitors.resource_monitor import ResourceMonitor
    from src.monitors.memory_monitor import MemoryMonitor
    from src.unraid.monitors.system_monitor import UnraidSystemMonitor
    from src.unraid.monitors.array_monitor import ArrayMonitor
    from src.unraid.client import UnraidClientWrapper

logger = logging.getLogger(__name__)

try:
    from importlib.metadata import version as _pkg_version
    BOT_VERSION = _pkg_version("unraid-monitor-bot")
except Exception:
    BOT_VERSION = _FALLBACK_VERSION


def _format_health_uptime(start_time: datetime) -> str:
    """Format bot uptime from start time to now."""
    delta = datetime.now(timezone.utc) - start_time
    days = delta.days
    hours = delta.seconds // 3600
    minutes = (delta.seconds % 3600) // 60

    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0 or not parts:
        parts.append(f"{minutes}m")
    return " ".join(parts)


def build_status_lines(
    monitor: "DockerEventMonitor | None" = None,
    log_watcher: "LogWatcher | None" = None,
    resource_monitor: "ResourceMonitor | None" = None,
    memory_monitor: "MemoryMonitor | None" = None,
    unraid_client: Any = None,
    unraid_system_monitor: "UnraidSystemMonitor | None" = None,
    unraid_array_monitor: "ArrayMonitor | None" = None,
    image_update_monitor: Any = None,
    auto_heal_config: Any = None,
) -> list[str]:
    lines: list[str] = ["*Monitors:*"]
    if monitor:
        status = "✅ Running" if monitor.is_running else "🔴 Stopped"
        lines.append(f"  Docker Events: {status} ({len(monitor.state_manager.get_all())} containers)")
    else:
        lines.append("  Docker Events: ⚪ Not configured")
    if log_watcher:
        status = "✅ Running" if log_watcher.is_running else "🔴 Stopped"
        drop_info = f", {log_watcher.total_drops} dropped" if log_watcher.total_drops else ""
        lines.append(f"  Log Watcher: {status} ({len(log_watcher.containers)} containers{drop_info})")
    else:
        lines.append("  Log Watcher: ⚪ Not configured")
    if resource_monitor:
        lines.append(f"  Resources: {'✅ Running' if resource_monitor.is_running else '🔴 Stopped'}")
    else:
        lines.append("  Resources: ⚪ Disabled")
    if memory_monitor:
        lines.append(f"  Memory: {'✅ Running' if memory_monitor.is_running else '🔴 Stopped'}")
    else:
        lines.append("  Memory: ⚪ Disabled")
    if image_update_monitor:
        lines.append(f"  Image updates: {'✅ Running' if image_update_monitor.is_running else '🔴 Stopped'}")
    else:
        lines.append("  Image updates: ⚪ Disabled")
    if auto_heal_config is not None and auto_heal_config.enabled and auto_heal_config.containers:
        lines.append(f"  Auto-heal: ✅ {len(auto_heal_config.containers)} container(s)")
    else:
        lines.append("  Auto-heal: ⚪ Disabled")
    if unraid_client:
        lines.append(f"  Unraid: {'✅ Connected' if unraid_client.is_connected else '🔴 Disconnected'}")
        if unraid_system_monitor:
            lines.append(f"    System: {'✅' if unraid_system_monitor.is_running else '🔴'}")
        if unraid_array_monitor:
            lines.append(f"    Array: {'✅' if unraid_array_monitor.is_running else '🔴'}")
    else:
        lines.append("  Unraid: ⚪ Not configured")
    return lines


def health_command(
    start_time: datetime,
    monitor: "DockerEventMonitor | None" = None,
    log_watcher: "LogWatcher | None" = None,
    resource_monitor: "ResourceMonitor | None" = None,
    memory_monitor: "MemoryMonitor | None" = None,
    unraid_client: "UnraidClientWrapper | None" = None,
    unraid_system_monitor: "UnraidSystemMonitor | None" = None,
    unraid_array_monitor: "ArrayMonitor | None" = None,
    alert_manager: object | None = None,
    image_update_monitor: Any = None,
    auto_heal_config: Any = None,
) -> Callable[[Message], Awaitable[None]]:
    """Factory for /health command handler."""

    async def handler(message: Message) -> None:
        uptime = _format_health_uptime(start_time)

        lines = [
            "🏥 *Bot Health*",
            "",
            f"*Version:* {BOT_VERSION}",
            f"*Uptime:* {uptime}",
            "",
        ]

        lines.extend(build_status_lines(
            monitor=monitor,
            log_watcher=log_watcher,
            resource_monitor=resource_monitor,
            memory_monitor=memory_monitor,
            unraid_client=unraid_client,
            unraid_system_monitor=unraid_system_monitor,
            unraid_array_monitor=unraid_array_monitor,
            image_update_monitor=image_update_monitor,
            auto_heal_config=auto_heal_config,
        ))

        # Alert queue depth
        if alert_manager and hasattr(alert_manager, "queued_count"):
            queued = alert_manager.queued_count
            if queued > 0:
                lines.append(f"  Alert Queue: {queued} pending")

        # Crash tracker stats
        if monitor:
            active_loops = monitor.crash_tracker.get_active_crash_loops()
            if active_loops:
                lines.append("")
                lines.append("*Recent Crashes:*")
                for name, count in active_loops:
                    lines.append(f"  ⚠️ {name} ({count}x)")

        await safe_reply(message, "\n".join(lines))

    return handler
