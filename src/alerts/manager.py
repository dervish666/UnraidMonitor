import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Protocol, runtime_checkable

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


from src.constants import IMAGE_UPDATE_MAX_SHOWN
from src.utils.formatting import format_bytes, format_uptime, strip_log_timestamps, escape_markdown, truncate_callback_data
from src.utils.telegram_retry import send_with_retry

logger = logging.getLogger(__name__)


@runtime_checkable
class AlertSender(Protocol):
    """Protocol for sending alerts to Telegram."""

    async def send_crash_alert(
        self,
        container_name: str,
        exit_code: int,
        image: str,
        uptime_seconds: int | None = None,
        restart_loop_count: int | None = None,
    ) -> None: ...

    async def send_log_error_alert(
        self,
        container_name: str,
        error_line: str,
        suppressed_count: int = 0,
    ) -> None: ...

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
    ) -> None: ...

    async def send_health_alert(self, container_name: str, health_status: str) -> None: ...

    async def send_recovery_alert(self, container_name: str) -> None: ...

    async def send_update_alert(self, updates: list[tuple[str, str]]) -> None: ...

    async def send_autoheal_alert(self, container_name: str, attempt: int, max_attempts: int, gave_up: bool) -> None: ...


class ChatIdStore:
    """Persistent storage for alert chat IDs (supports multiple users).

    When *json_path* is provided, chat IDs are persisted to disk and
    survive bot restarts so alerts continue flowing immediately.
    """

    def __init__(self, json_path: str | None = None):
        self._json_path = json_path
        self._chat_ids: set[int] = set()
        if json_path:
            self._load()

    # -- persistence helpers ------------------------------------------------

    def _load(self) -> None:
        """Load chat IDs from JSON file if it exists."""
        if not self._json_path:
            return
        try:
            with open(self._json_path) as f:
                data = json.load(f)
            if isinstance(data, list):
                self._chat_ids = {int(cid) for cid in data}
                logger.info(f"Loaded {len(self._chat_ids)} chat IDs from {self._json_path}")
        except FileNotFoundError:
            pass
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Failed to load chat IDs from {self._json_path}: {e}")

    def _save(self) -> None:
        """Persist current chat IDs to JSON file (atomic write)."""
        if not self._json_path:
            return
        try:
            parent = Path(self._json_path).parent
            parent.mkdir(parents=True, exist_ok=True)
            fd, temp_path = tempfile.mkstemp(
                dir=str(parent), prefix=".tmp_chatids_", suffix=".json"
            )
            try:
                os.fchmod(fd, 0o644)
                with os.fdopen(fd, "w") as f:
                    json.dump(sorted(self._chat_ids), f)
                os.replace(temp_path, self._json_path)
            except Exception:
                os.unlink(temp_path)
                raise
        except OSError as e:
            logger.error(f"Failed to save chat IDs to {self._json_path}: {e}")

    # -- public API ---------------------------------------------------------

    def set_chat_id(self, chat_id: int) -> None:
        """Add a chat ID to the set (persists to disk if configured)."""
        if chat_id not in self._chat_ids:
            self._chat_ids.add(chat_id)
            self._save()

    def get_chat_id(self) -> int | None:
        """Get any stored chat ID (backward compat)."""
        return next(iter(self._chat_ids), None)

    def get_all_chat_ids(self) -> set[int]:
        """Get all stored chat IDs."""
        return set(self._chat_ids)


class AlertManager:
    """Manages sending alerts to Telegram."""

    def __init__(self, bot: Bot, chat_id: int, error_display_max_chars: int = 200, cooldown_seconds: int = 900):
        self.bot = bot
        self.chat_id = chat_id
        self.error_display_max_chars = error_display_max_chars
        self.cooldown_seconds = cooldown_seconds

    async def send_crash_alert(
        self,
        container_name: str,
        exit_code: int,
        image: str,
        uptime_seconds: int | None = None,
        restart_loop_count: int | None = None,
    ) -> None:
        """Send a container crash alert with quick action buttons."""
        uptime_str = format_uptime(uptime_seconds) if uptime_seconds else "unknown"

        # Interpret common exit codes
        exit_reason = ""
        if exit_code == 137:
            exit_reason = " (OOM killed)"
        elif exit_code == 143:
            exit_reason = " (SIGTERM)"
        elif exit_code == 139:
            exit_reason = " (segfault)"

        safe_name = escape_markdown(container_name)

        if restart_loop_count:
            text = f"""🔄🔴 *RESTART LOOP:* {safe_name}

Crashed {restart_loop_count} times in the last 10 minutes!
Exit code: {exit_code}{exit_reason}
Image: `{image}`"""
        else:
            text = f"""🔴 *CONTAINER CRASHED:* {safe_name}

Exit code: {exit_code}{exit_reason}
Image: `{image}`
Uptime: {uptime_str}"""

        # Quick action buttons
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="🔄 Restart", callback_data=truncate_callback_data("restart:", container_name)),
                    InlineKeyboardButton(text="📋 Logs", callback_data=truncate_callback_data("logs:", f"{container_name}:50")),
                    InlineKeyboardButton(text="🔍 Diagnose", callback_data=truncate_callback_data("diagnose:", container_name)),
                ],
                [
                    InlineKeyboardButton(text="🔕 Mute 1h", callback_data=truncate_callback_data("mute:", f"{container_name}:60")),
                    InlineKeyboardButton(text="🔕 Mute 24h", callback_data=truncate_callback_data("mute:", f"{container_name}:1440")),
                ],
            ]
        )

        try:
            await send_with_retry(
                self.bot.send_message,
                chat_id=self.chat_id,
                text=text,
                parse_mode="Markdown",
                reply_markup=keyboard,
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
        if len(error_line) > self.error_display_max_chars:
            display_error = error_line[:self.error_display_max_chars] + "..."

        cooldown_str = self._format_duration(self.cooldown_seconds)
        safe_name = escape_markdown(container_name)

        if total_errors > 1:
            count_text = f"Found {total_errors} errors in the last {cooldown_str}"
        else:
            count_text = "New error detected"

        text = f"""⚠️ *ERRORS IN:* {safe_name}

{count_text}

Latest: `{display_error}`

/logs {container_name} 50 - View last 50 lines"""

        # Create inline keyboard with quick action buttons
        # Telegram limits callback_data to 64 bytes (UTF-8 encoded)
        error_preview = strip_log_timestamps(error_line)
        ignore_cb = truncate_callback_data(f"ignore_similar:{container_name}:", error_preview)

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="🔇 Ignore Similar", callback_data=ignore_cb),
                    InlineKeyboardButton(text="🔕 Mute 1h", callback_data=truncate_callback_data("mute:", f"{container_name}:60")),
                ],
                [
                    InlineKeyboardButton(text="📋 Logs", callback_data=truncate_callback_data("logs:", f"{container_name}:50")),
                    InlineKeyboardButton(text="🔍 Diagnose", callback_data=truncate_callback_data("diagnose:", container_name)),
                ],
            ]
        )

        try:
            await send_with_retry(
                self.bot.send_message,
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

        safe_name = escape_markdown(container_name)

        text = f"""⚠️ *{title}:* {safe_name}

{primary}
Exceeded for: {duration_str}

{secondary}"""

        # Quick action buttons
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="📋 Logs", callback_data=truncate_callback_data("logs:", f"{container_name}:50")),
                    InlineKeyboardButton(text="🔍 Diagnose", callback_data=truncate_callback_data("diagnose:", container_name)),
                ],
                [
                    InlineKeyboardButton(text="🔕 Mute 1h", callback_data=truncate_callback_data("mute:", f"{container_name}:60")),
                    InlineKeyboardButton(text="🔕 Mute 24h", callback_data=truncate_callback_data("mute:", f"{container_name}:1440")),
                ],
                [
                    InlineKeyboardButton(
                        text=f"⚙️ Raise {metric.upper()} Limit",
                        callback_data=truncate_callback_data("res_limit:", f"{container_name}:{metric}:{threshold}"),
                    ),
                ],
            ]
        )

        try:
            await send_with_retry(
                self.bot.send_message,
                chat_id=self.chat_id,
                text=text,
                parse_mode="Markdown",
                reply_markup=keyboard,
            )
            logger.info(f"Sent resource alert for {container_name} ({metric})")
        except Exception as e:
            logger.error(f"Failed to send resource alert: {e}")

    async def send_health_alert(self, container_name: str, health_status: str) -> None:
        """Send alert when a container's health check transitions to unhealthy."""
        safe_name = escape_markdown(container_name)
        text = f"🏥 *UNHEALTHY:* {safe_name}\n\nHealth check status: {health_status}"

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="🔄 Restart", callback_data=truncate_callback_data("restart:", container_name)),
                    InlineKeyboardButton(text="📋 Logs", callback_data=truncate_callback_data("logs:", f"{container_name}:50")),
                    InlineKeyboardButton(text="🔍 Diagnose", callback_data=truncate_callback_data("diagnose:", container_name)),
                ],
            ]
        )

        try:
            await send_with_retry(
                self.bot.send_message,
                chat_id=self.chat_id,
                text=text,
                parse_mode="Markdown",
                reply_markup=keyboard,
            )
            logger.info(f"Sent health alert for {container_name}")
        except Exception as e:
            logger.error(f"Failed to send health alert: {e}")

    async def send_autoheal_alert(self, container_name: str, attempt: int, max_attempts: int, gave_up: bool) -> None:
        """Notify that an unhealthy container was auto-restarted (or that we gave up)."""
        safe_name = escape_markdown(container_name)
        if gave_up:
            text = (f"⚠️ *Auto-heal gave up:* {safe_name}\n\n"
                    f"Restarted {attempt} times but it's still unhealthy. Manual attention needed.")
        else:
            text = (f"🔧 *Auto-restarted:* {safe_name}\n\n"
                    f"Container was unhealthy - auto-restarted (attempt {attempt}/{max_attempts}).")
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="📋 Logs", callback_data=truncate_callback_data("logs:", f"{container_name}:50")),
            InlineKeyboardButton(text="🔍 Diagnose", callback_data=truncate_callback_data("diagnose:", container_name)),
        ]])
        try:
            await send_with_retry(self.bot.send_message, chat_id=self.chat_id, text=text,
                                  parse_mode="Markdown", reply_markup=keyboard)
            logger.info(f"Sent autoheal alert for {container_name} (gave_up={gave_up})")
        except Exception as e:
            logger.error(f"Failed to send autoheal alert: {e}")

    async def send_update_alert(self, updates: list[tuple[str, str]]) -> None:
        """Send a single batched digest of containers with newer images available."""
        if not updates:
            return
        lines = [f"⬇️ *Image updates available* ({len(updates)})", ""]
        rows: list[list[InlineKeyboardButton]] = []
        for name, image in updates[:IMAGE_UPDATE_MAX_SHOWN]:
            lines.append(f"• {escape_markdown(name)} - {escape_markdown(image)}")
            rows.append([InlineKeyboardButton(text=f"⬇️ Pull {name}", callback_data=truncate_callback_data("pull:", name))])
        if len(updates) > IMAGE_UPDATE_MAX_SHOWN:
            lines.append(f"...and {len(updates) - IMAGE_UPDATE_MAX_SHOWN} more")
        keyboard = InlineKeyboardMarkup(inline_keyboard=rows)
        try:
            await send_with_retry(self.bot.send_message, chat_id=self.chat_id, text="\n".join(lines),
                                  parse_mode="Markdown", reply_markup=keyboard)
            logger.info(f"Sent image-update digest for {len(updates)} container(s)")
        except Exception as e:
            logger.error(f"Failed to send update alert: {e}")

    async def send_recovery_alert(self, container_name: str) -> None:
        """Send a brief recovery notification when a crashed container starts."""
        text = f"✅ *{escape_markdown(container_name)}* recovered and is running again."

        try:
            await send_with_retry(
                self.bot.send_message,
                chat_id=self.chat_id,
                text=text,
                parse_mode="Markdown",
            )
            logger.info(f"Sent recovery alert for {container_name}")
        except Exception as e:
            logger.error(f"Failed to send recovery alert: {e}")

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
