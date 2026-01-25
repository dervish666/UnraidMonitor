import logging
from aiogram import Bot

logger = logging.getLogger(__name__)


def format_uptime(seconds: int) -> str:
    """Format uptime in human-readable form."""
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60

    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


class AlertManager:
    """Manages sending alerts to Telegram."""

    def __init__(self, bot: Bot, chat_id: int):
        self.bot = bot
        self.chat_id = chat_id

    async def send_crash_alert(
        self,
        container_name: str,
        exit_code: int,
        image: str,
        uptime_seconds: int | None = None,
    ) -> None:
        """Send a container crash alert."""
        uptime_str = format_uptime(uptime_seconds) if uptime_seconds else "unknown"

        # Interpret common exit codes
        exit_reason = ""
        if exit_code == 137:
            exit_reason = " (OOM killed)"
        elif exit_code == 143:
            exit_reason = " (SIGTERM)"
        elif exit_code == 139:
            exit_reason = " (segfault)"

        text = f"""🔴 *CONTAINER CRASHED:* {container_name}

Exit code: {exit_code}{exit_reason}
Image: `{image}`
Uptime: {uptime_str}

/status {container_name} - View details
/logs {container_name} - View recent logs"""

        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=text,
                parse_mode="Markdown",
            )
            logger.info(f"Sent crash alert for {container_name}")
        except Exception as e:
            logger.error(f"Failed to send crash alert: {e}")

    async def send_log_error_alert(
        self,
        container_name: str,
        error_line: str,
        suppressed_count: int = 0,
    ) -> None:
        """Send a log error alert."""
        total_errors = suppressed_count + 1

        # Truncate long error lines
        if len(error_line) > 200:
            error_line = error_line[:200] + "..."

        if total_errors > 1:
            count_text = f"Found {total_errors} errors in the last 15 minutes"
        else:
            count_text = "New error detected"

        text = f"""⚠️ *ERRORS IN:* {container_name}

{count_text}

Latest: `{error_line}`

/logs {container_name} 50 - View last 50 lines"""

        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=text,
                parse_mode="Markdown",
            )
            logger.info(f"Sent log error alert for {container_name}")
        except Exception as e:
            logger.error(f"Failed to send log error alert: {e}")
