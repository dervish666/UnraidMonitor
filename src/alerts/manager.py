import logging
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from src.utils.formatting import format_bytes

logger = logging.getLogger(__name__)


class ChatIdStore:
    """Simple in-memory storage for the alert chat ID."""

    def __init__(self):
        self._chat_id: int | None = None

    def set_chat_id(self, chat_id: int) -> None:
        """Store the chat ID."""
        self._chat_id = chat_id

    def get_chat_id(self) -> int | None:
        """Get the stored chat ID."""
        return self._chat_id


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
        """Send a log error alert with ignore button."""
        total_errors = suppressed_count + 1

        # Truncate long error lines for display
        display_error = error_line
        if len(error_line) > 200:
            display_error = error_line[:200] + "..."

        if total_errors > 1:
            count_text = f"Found {total_errors} errors in the last 15 minutes"
        else:
            count_text = "New error detected"

        text = f"""⚠️ *ERRORS IN:* {container_name}

{count_text}

Latest: `{display_error}`

/logs {container_name} 50 - View last 50 lines"""

        # Create inline keyboard with ignore button
        # Truncate error in callback data (max 64 bytes for callback_data)
        # Format: ignore_similar:container:error_preview
        # Reserve space for prefix and container name
        prefix = f"ignore_similar:{container_name}:"
        max_error_len = 64 - len(prefix)
        error_preview = error_line[:max_error_len] if len(error_line) > max_error_len else error_line

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔇 Ignore Similar",
                        callback_data=f"{prefix}{error_preview}",
                    )
                ]
            ]
        )

        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=text,
                parse_mode="Markdown",
                reply_markup=keyboard,
            )
            logger.info(f"Sent log error alert for {container_name}")
        except Exception as e:
            logger.error(f"Failed to send log error alert: {e}")

    async def send_resource_alert(
        self,
        container_name: str,
        metric: str,
        current_value: float,
        threshold: int,
        duration_seconds: int,
        memory_bytes: int,
        memory_limit: int,
        memory_percent: float,
        cpu_percent: float,
    ) -> None:
        """Send a resource threshold alert.

        Args:
            container_name: Container name.
            metric: "cpu" or "memory".
            current_value: Current metric value.
            threshold: Threshold that was exceeded.
            duration_seconds: How long threshold has been exceeded.
            memory_bytes: Current memory usage in bytes.
            memory_limit: Memory limit in bytes.
            memory_percent: Memory usage percentage.
            cpu_percent: CPU usage percentage.
        """
        duration_str = self._format_duration(duration_seconds)
        memory_display = format_bytes(memory_bytes)
        memory_limit_display = format_bytes(memory_limit)

        if metric == "cpu":
            title = "HIGH RESOURCE USAGE"
            primary = f"CPU: {current_value}% (threshold: {threshold}%)"
            secondary = f"Memory: {memory_display} / {memory_limit_display} ({memory_percent}%)"
        else:
            title = "HIGH MEMORY USAGE"
            primary = f"Memory: {current_value}% (threshold: {threshold}%)"
            primary += f"\n        {memory_display} / {memory_limit_display} limit"
            secondary = f"CPU: {cpu_percent}% (normal)"

        text = f"""⚠️ *{title}:* {container_name}

{primary}
Exceeded for: {duration_str}

{secondary}

_Use /resources {container_name} or /diagnose {container_name} for details_"""

        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=text,
                parse_mode="Markdown",
            )
            logger.info(f"Sent resource alert for {container_name} ({metric})")
        except Exception as e:
            logger.error(f"Failed to send resource alert: {e}")

    @staticmethod
    def _format_duration(seconds: int) -> str:
        """Format duration in human-readable form."""
        if seconds >= 3600:
            hours = seconds // 3600
            minutes = (seconds % 3600) // 60
            return f"{hours}h {minutes}m"
        minutes = seconds // 60
        if minutes > 0:
            return f"{minutes} minutes" if minutes > 1 else "1 minute"
        return f"{seconds} seconds"
