from typing import Callable, Awaitable, TYPE_CHECKING

from aiogram.types import Message

if TYPE_CHECKING:
    from src.monitors.resource_monitor import ResourceMonitor


def format_progress_bar(percent: float, width: int = 16) -> str:
    """Format a progress bar for resource usage."""
    filled = int(percent / 100 * width)
    empty = width - filled
    return "█" * filled + "░" * empty


def format_summary_line(name: str, cpu: float, mem: float, mem_display: str) -> str:
    """Format a single container line for summary view."""
    # Pad name to 12 chars
    name_padded = name[:12].ljust(12)
    warning = " ⚠️" if cpu > 70 or mem > 70 else ""
    return f"{name_padded} CPU: {cpu:4.0f}%  MEM: {mem:4.0f}% ({mem_display}){warning}"


async def format_resources_summary(resource_monitor: "ResourceMonitor") -> str | None:
    """Format resource summary for all containers.

    Returns:
        Formatted summary string, or None if no containers found.
    """
    stats_list = await resource_monitor.get_all_stats()

    if not stats_list:
        return None

    lines = ["📊 *Container Resources*", ""]

    for stats in sorted(stats_list, key=lambda s: s.memory_percent, reverse=True):
        line = format_summary_line(
            stats.name,
            stats.cpu_percent,
            stats.memory_percent,
            stats.memory_display,
        )
        lines.append(f"`{line}`")

    lines.append("")
    lines.append("_⚠️ = approaching threshold_")

    return "\n".join(lines)


def resources_command(
    resource_monitor: "ResourceMonitor",
) -> Callable[[Message], Awaitable[None]]:
    """Factory for /resources command handler."""

    async def handler(message: Message) -> None:
        text = message.text or ""
        parts = text.strip().split(maxsplit=1)

        if len(parts) == 1:
            # Summary view
            summary = await format_resources_summary(resource_monitor)

            if not summary:
                await message.answer("📊 No running containers found")
                return

            await message.answer(summary, parse_mode="Markdown")
        else:
            # Detailed view for specific container
            container_name = parts[1].strip()
            stats = await resource_monitor.get_container_stats(container_name)

            if stats is None:
                await message.answer(
                    f"❌ Container '{container_name}' not found or not running"
                )
                return

            cpu_threshold, mem_threshold = resource_monitor._config.get_thresholds(
                container_name
            )

            cpu_bar = format_progress_bar(stats.cpu_percent)
            mem_bar = format_progress_bar(stats.memory_percent)

            response = f"""📊 *Resources: {stats.name}*

CPU:    {stats.cpu_percent:5.1f}% `{cpu_bar}` (threshold: {cpu_threshold}%)
Memory: {stats.memory_percent:5.1f}% `{mem_bar}` (threshold: {mem_threshold}%)
        {stats.memory_display} / {stats.memory_limit_display} limit"""

            await message.answer(response, parse_mode="Markdown")

    return handler
