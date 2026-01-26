import re
import logging
from typing import Callable, Awaitable, TYPE_CHECKING
from datetime import timedelta

from aiogram.types import Message

from src.alerts.mute_manager import parse_duration

if TYPE_CHECKING:
    from src.alerts.mute_manager import MuteManager
    from src.state import ContainerStateManager

logger = logging.getLogger(__name__)

# Pattern to extract container name from various alert types
ALERT_PATTERNS = [
    re.compile(r"ERRORS IN[:\s]+(\w+)", re.IGNORECASE),
    re.compile(r"CRASHED[:\s]+(\w+)", re.IGNORECASE),
    re.compile(r"HIGH .+ USAGE[:\s]+(\w+)", re.IGNORECASE),
    re.compile(r"Container[:\s]+(\w+)", re.IGNORECASE),
]


def extract_container_from_alert(text: str) -> str | None:
    """Extract container name from any alert type."""
    for pattern in ALERT_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(1)
    return None


def mute_command(
    state: "ContainerStateManager",
    mute_manager: "MuteManager",
) -> Callable[[Message], Awaitable[None]]:
    """Factory for /mute command handler."""

    async def handler(message: Message) -> None:
        text = (message.text or "").strip()
        parts = text.split()

        # Parse command arguments
        container: str | None = None
        duration_str: str | None = None

        if len(parts) == 1:
            # Just /mute - need reply or show usage
            if message.reply_to_message and message.reply_to_message.text:
                container = extract_container_from_alert(message.reply_to_message.text)
                if not container:
                    await message.answer(
                        "Usage: `/mute <container> <duration>`\n"
                        "Or reply to an alert with `/mute <duration>`\n\n"
                        "Examples:\n"
                        "• `/mute plex 2h`\n"
                        "• `/mute radarr 30m`\n"
                        "• Reply to alert + `/mute 1h`",
                        parse_mode="Markdown",
                    )
                    return
            else:
                await message.answer(
                    "Usage: `/mute <container> <duration>`\n"
                    "Or reply to an alert with `/mute <duration>`\n\n"
                    "Examples:\n"
                    "• `/mute plex 2h`\n"
                    "• `/mute radarr 30m`\n"
                    "• Reply to alert + `/mute 1h`",
                    parse_mode="Markdown",
                )
                return

        elif len(parts) == 2:
            # /mute <duration> (replying) or /mute <container> (missing duration)
            if message.reply_to_message and message.reply_to_message.text:
                container = extract_container_from_alert(message.reply_to_message.text)
                duration_str = parts[1]
            else:
                await message.answer(
                    "Missing duration. Use `/mute <container> <duration>`\n"
                    "Examples: `2h`, `30m`, `24h`",
                    parse_mode="Markdown",
                )
                return

        elif len(parts) >= 3:
            # /mute <container> <duration>
            container_query = parts[1]
            duration_str = parts[2]

            # Find container by partial match
            containers = [c.name for c in state.get_all()]
            matches = [c for c in containers if container_query.lower() in c.lower()]

            if len(matches) == 1:
                container = matches[0]
            elif len(matches) > 1:
                await message.answer(
                    f"Ambiguous: `{container_query}` matches {', '.join(matches)}",
                    parse_mode="Markdown",
                )
                return
            else:
                # Accept anyway for flexibility
                container = container_query

        if not container:
            await message.answer("Could not determine container.")
            return

        if not duration_str:
            await message.answer("Missing duration.")
            return

        # Parse duration
        duration = parse_duration(duration_str)
        if not duration:
            await message.answer(
                f"Invalid duration: `{duration_str}`\n"
                "Use format like `15m`, `2h`, `24h`",
                parse_mode="Markdown",
            )
            return

        # Add mute
        expiry = mute_manager.add_mute(container, duration)

        # Format expiry time
        time_str = expiry.strftime("%H:%M")
        await message.answer(
            f"🔇 *Muted {container}* until {time_str}\n\n"
            f"All alerts suppressed for {format_duration(duration)}.\n"
            f"Use `/unmute {container}` to unmute early.",
            parse_mode="Markdown",
        )

    return handler


def format_duration(delta: timedelta) -> str:
    """Format timedelta for display."""
    total_minutes = int(delta.total_seconds() / 60)
    if total_minutes >= 60:
        hours = total_minutes // 60
        mins = total_minutes % 60
        if mins:
            return f"{hours}h {mins}m"
        return f"{hours}h"
    return f"{total_minutes}m"


def mutes_command(
    mute_manager: "MuteManager",
) -> Callable[[Message], Awaitable[None]]:
    """Factory for /mutes command handler."""

    async def handler(message: Message) -> None:
        mutes = mute_manager.get_active_mutes()

        if not mutes:
            await message.answer(
                "🔇 No active mutes.\n\n_Use `/mute <container> <duration>` to mute._",
                parse_mode="Markdown",
            )
            return

        lines = ["🔇 *Active Mutes*\n"]
        for container, expiry in sorted(mutes, key=lambda x: x[1]):
            time_str = expiry.strftime("%H:%M")
            lines.append(f"• *{container}* until {time_str}")

        lines.append("\n_Use `/unmute <container>` to unmute early._")
        await message.answer("\n".join(lines), parse_mode="Markdown")

    return handler


def unmute_command(
    state: "ContainerStateManager",
    mute_manager: "MuteManager",
) -> Callable[[Message], Awaitable[None]]:
    """Factory for /unmute command handler."""

    async def handler(message: Message) -> None:
        text = (message.text or "").strip()
        parts = text.split()

        if len(parts) < 2:
            await message.answer(
                "Usage: `/unmute <container>`",
                parse_mode="Markdown",
            )
            return

        container_query = parts[1]

        # Find container by partial match in active mutes first
        mutes = mute_manager.get_active_mutes()
        muted_containers = [c for c, _ in mutes]
        matches = [c for c in muted_containers if container_query.lower() in c.lower()]

        if len(matches) == 1:
            container = matches[0]
        elif len(matches) > 1:
            await message.answer(
                f"Ambiguous: `{container_query}` matches {', '.join(matches)}",
                parse_mode="Markdown",
            )
            return
        else:
            container = container_query

        # Try to unmute
        if mute_manager.remove_mute(container):
            await message.answer(
                f"🔔 *Unmuted {container}*\n\nAlerts are now enabled.",
                parse_mode="Markdown",
            )
        else:
            await message.answer(
                f"`{container}` is not muted.",
                parse_mode="Markdown",
            )

    return handler
