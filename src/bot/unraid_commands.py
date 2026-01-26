"""Unraid server monitoring commands."""

import logging
from typing import Callable, Awaitable, TYPE_CHECKING

from aiogram.types import Message

if TYPE_CHECKING:
    from src.unraid.monitors.system_monitor import UnraidSystemMonitor

logger = logging.getLogger(__name__)


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
        uptime = metrics.get("uptime", "Unknown")

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
