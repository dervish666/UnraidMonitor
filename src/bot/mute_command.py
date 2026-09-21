import logging
from typing import Callable, Awaitable, TYPE_CHECKING
from datetime import timedelta

from aiogram.types import Message

from src.alerts.mute_manager import parse_duration
from src.utils.formatting import extract_container_from_alert, truncate_message, safe_reply, format_mute_expiry, escape_markdown

if TYPE_CHECKING:
    from src.alerts.mute_manager import MuteManager
    from src.alerts.server_mute_manager import ServerMuteManager
    from src.alerts.array_mute_manager import ArrayMuteManager
    from src.state import ContainerStateManager

logger = logging.getLogger(__name__)


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
                        "• `/mute handbrake 1w` (max 7 days)\n"
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
                    "• `/mute handbrake 1w` (max 7 days)\n"
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
                    "Examples: `2h`, `30m`, `24h`, `1w`",
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
                # Accept but warn — may be a typo or a container that starts later
                container = container_query
                await message.answer(
                    f"No running container matches '{container_query}'. "
                    f"Muting the name anyway (will apply if it starts later)."
                )

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
                "Use format like `15m`, `2h`, `24h`, `1w` (max 7 days)",
                parse_mode="Markdown",
            )
            return

        # Add mute
        expiry = mute_manager.add_mute(container, duration)

        mute_msg = (
            f"🔇 *Muted {escape_markdown(container)}* {format_mute_expiry(expiry)}\n\n"
            f"All alerts suppressed for {format_duration(duration)}.\n"
            f"Use `/unmute {container}` to unmute early."
        )
        await safe_reply(message, mute_msg)

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
    server_mute_manager: "ServerMuteManager | None" = None,
    array_mute_manager: "ArrayMuteManager | None" = None,
) -> Callable[[Message], Awaitable[None]]:
    """Factory for /mutes command handler."""

    async def handler(message: Message) -> None:
        # Collect container mutes
        container_mutes = mute_manager.get_active_mutes()

        # Check server mutes - look for "server" category in active mutes
        server_expiry = None
        if server_mute_manager:
            server_mutes = server_mute_manager.get_active_mutes()
            for category, expiry in server_mutes:
                if category == "server":
                    server_expiry = expiry
                    break

        # Check array mutes
        array_expiry = None
        if array_mute_manager:
            array_expiry = array_mute_manager.get_mute_expiry()

        # Check if anything is muted
        if not container_mutes and not server_expiry and not array_expiry:
            await message.answer(
                "No active mutes.",
                parse_mode="Markdown",
            )
            return

        lines = ["🔇 *Active Mutes*"]

        # Container mutes section
        if container_mutes:
            lines.append("\nContainer mutes:")
            for container, expiry in sorted(container_mutes, key=lambda x: x[1]):
                lines.append(f"  {container}: {format_mute_expiry(expiry)}")

        # Server mutes section
        if server_expiry:
            lines.append(f"\n🔇 *Server alerts muted* {format_mute_expiry(server_expiry)}")

        # Array mutes section
        if array_expiry:
            lines.append(f"🔇 *Array alerts muted* {format_mute_expiry(array_expiry)}")

        mutes_text = truncate_message("\n".join(lines))
        await safe_reply(message, mutes_text)

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
            await safe_reply(message, f"🔔 *Unmuted {escape_markdown(container)}*\n\nAlerts are now enabled.")
        else:
            await message.answer(f"{container} is not muted.")

    return handler
