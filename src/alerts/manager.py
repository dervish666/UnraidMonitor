import logging
from typing import Protocol, runtime_checkable

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


from src.constants import IMAGE_UPDATE_MAX_SHOWN
from src.utils.formatting import (
    escape_markdown, format_bytes, format_uptime, parse_int, strip_log_timestamps,
    truncate_callback_data,
)
from src.utils.telegram_retry import send_with_retry
from src.utils.sanitize import redact_secrets
from src.utils.json_store import atomic_write_json, load_json, log_rejected

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
        oom_killed: bool = False,
    ) -> bool: ...

    async def send_log_error_alert(
        self,
        container_name: str,
        error_line: str,
        suppressed_count: int = 0,
    ) -> bool: ...

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
    ) -> bool: ...

    async def send_resource_recovery(
        self, container_name: str, metric: str, current_value: float, threshold: int,
    ) -> bool: ...

    async def send_health_alert(self, container_name: str, health_status: str) -> bool: ...

    async def send_recovery_alert(self, container_name: str) -> bool: ...

    async def send_update_alert(self, updates: list[tuple[str, str]]) -> bool: ...

    async def send_autoheal_alert(
        self, container_name: str, attempt: int, max_attempts: int, gave_up: bool, failed: bool = False
    ) -> bool: ...


class ChatIdStore:
    """Persistent storage for alert chat IDs (supports multiple users).

    When *json_path* is provided, chat IDs are persisted to disk and
    survive bot restarts so alerts continue flowing immediately.

    Only private chats are stored, and a private chat's ID is the user's ID,
    so *allowed_users* also prunes chats of users removed from
    TELEGRAM_ALLOWED_USERS since the file was written.
    """

    def __init__(self, json_path: str | None = None, allowed_users: list[int] | None = None):
        self._json_path = json_path
        self._allowed = set(allowed_users) if allowed_users is not None else None
        self._chat_ids: set[int] = set()
        if json_path:
            self._load()

    # -- persistence helpers ------------------------------------------------

    def _load(self) -> None:
        """Load chat IDs, keeping every integer entry; a non-list file is set aside."""
        if not self._json_path:
            return
        data = load_json(self._json_path, list, "chat IDs")
        if data is None:
            return
        loaded: set[int] = set()
        rejected: list[str] = []
        for cid in data:
            if isinstance(cid, int) and not isinstance(cid, bool):
                loaded.add(cid)
            elif isinstance(cid, str) and (parsed := parse_int(cid)) is not None:
                loaded.add(parsed)
            else:
                rejected.append(repr(cid)[:20])
        log_rejected(self._json_path, rejected)
        if self._allowed is not None:
            dropped = loaded - self._allowed
            if dropped:
                logger.warning(
                    f"Dropping {len(dropped)} stored alert chat(s) that are not in "
                    "TELEGRAM_ALLOWED_USERS"
                )
            loaded &= self._allowed
        self._chat_ids = loaded
        logger.info(f"Loaded {len(self._chat_ids)} chat IDs from {self._json_path}")
        if rejected or len(loaded) != len(data):
            self._save()

    def _save(self) -> None:
        """Persist current chat IDs (atomic write)."""
        if not self._json_path:
            return
        try:
            atomic_write_json(self._json_path, sorted(self._chat_ids))
        except OSError as e:
            logger.error(f"Failed to save chat IDs to {self._json_path}: {e}")

    # -- public API ---------------------------------------------------------

    def set_chat_id(self, chat_id: int) -> None:
        """Add a chat ID to the set (persists to disk if configured)."""
        if self._allowed is not None and chat_id not in self._allowed:
            return
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

    async def _deliver(
        self, text: str, keyboard: InlineKeyboardMarkup | None, sent_msg: str, fail_msg: str
    ) -> bool:
        """Send one alert. True only when Telegram accepted it.

        Callers commit dedup and cooldown state on True, so a swallowed failure
        here would lose the alert for good.
        """
        try:
            await send_with_retry(
                self.bot.send_message,
                chat_id=self.chat_id,
                text=text,
                parse_mode="Markdown",
                reply_markup=keyboard,
            )
        except Exception as e:
            logger.error(f"{fail_msg} to {self.chat_id}: {e}")
            return False
        logger.info(sent_msg)
        return True

    async def send_crash_alert(
        self,
        container_name: str,
        exit_code: int,
        image: str,
        uptime_seconds: int | None = None,
        restart_loop_count: int | None = None,
        oom_killed: bool = False,
    ) -> bool:
        """Send a container crash alert with quick action buttons."""
        uptime_str = format_uptime(uptime_seconds) if uptime_seconds else "unknown"

        # Interpret common exit codes
        # 137 is SIGKILL from anyone; only Docker's oom event or
        # State.OOMKilled says the kernel did it for memory.
        exit_reason = ""
        if oom_killed:
            exit_reason = " (OOM killed)"
        elif exit_code == 137:
            exit_reason = " (SIGKILL)"
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

        return await self._deliver(text, keyboard, f"Sent crash alert for {container_name}", "Failed to send crash alert")

    async def send_log_error_alert(
        self,
        container_name: str,
        error_line: str,
        suppressed_count: int = 0,
    ) -> bool:
        """Send a log error alert with ignore button."""
        total_errors = suppressed_count + 1

        # A log line can carry a credential; the alert sits in chat history.
        error_line = redact_secrets(error_line)

        # Truncate long error lines for display
        display_error = error_line
        if len(error_line) > self.error_display_max_chars:
            display_error = error_line[:self.error_display_max_chars] + "..."

        cooldown_str = format_uptime(self.cooldown_seconds)
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

        return await self._deliver(text, keyboard, f"Sent log error alert for {container_name}", "Failed to send log error alert")

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
    ) -> bool:
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
        duration_str = format_uptime(duration_seconds)
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
                    InlineKeyboardButton(text="🔕 Mute 1w", callback_data=truncate_callback_data("mute:", f"{container_name}:10080")),
                ],
                [
                    InlineKeyboardButton(
                        text=f"⚙️ Raise {metric.upper()} Limit",
                        callback_data=truncate_callback_data("res_limit:", f"{container_name}:{metric}:{threshold}"),
                    ),
                ],
            ]
        )

        return await self._deliver(text, keyboard, f"Sent resource alert for {container_name} ({metric})", "Failed to send resource alert")

    async def send_resource_recovery(
        self, container_name: str, metric: str, current_value: float, threshold: int,
    ) -> bool:
        """Close a resource episode: the container is back under its threshold."""
        label = "CPU" if metric == "cpu" else "Memory"
        text = (
            f"✅ *{escape_markdown(container_name)}* {label} back to normal: "
            f"{current_value:.0f}% (threshold {threshold}%)."
        )
        return await self._deliver(
            text, None, f"Sent {metric} recovery for {container_name}", "Failed to send resource recovery",
        )

    async def send_health_alert(self, container_name: str, health_status: str) -> bool:
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

        return await self._deliver(text, keyboard, f"Sent health alert for {container_name}", "Failed to send health alert")

    async def send_autoheal_alert(
        self, container_name: str, attempt: int, max_attempts: int, gave_up: bool, failed: bool = False
    ) -> bool:
        """Notify that an unhealthy container was auto-restarted, the restart failed, or we gave up."""
        safe_name = escape_markdown(container_name)
        if gave_up:
            text = (f"⚠️ *Auto-heal gave up:* {safe_name}\n\n"
                    f"Restarted {attempt} times but it's still unhealthy. Manual attention needed.")
        elif failed:
            text = (f"❌ *Auto-heal failed:* {safe_name}\n\n"
                    f"Restart attempt {attempt}/{max_attempts} didn't go through. Worth a look.")
        else:
            text = (f"🔧 *Auto-restarted:* {safe_name}\n\n"
                    f"Container was unhealthy - auto-restarted (attempt {attempt}/{max_attempts}).")
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="📋 Logs", callback_data=truncate_callback_data("logs:", f"{container_name}:50")),
            InlineKeyboardButton(text="🔍 Diagnose", callback_data=truncate_callback_data("diagnose:", container_name)),
        ]])
        return await self._deliver(text, keyboard, f"Sent autoheal alert for {container_name} (gave_up={gave_up}, failed={failed})", "Failed to send autoheal alert")

    async def send_update_alert(self, updates: list[tuple[str, str]]) -> bool:
        """Send a single batched digest of containers with newer images available."""
        if not updates:
            return True
        lines = [f"⬇️ *Image updates available* ({len(updates)})", ""]
        rows: list[list[InlineKeyboardButton]] = []
        for name, image in updates[:IMAGE_UPDATE_MAX_SHOWN]:
            lines.append(f"• {escape_markdown(name)} - {escape_markdown(image)}")
            rows.append([InlineKeyboardButton(text=f"⬇️ Pull {name}", callback_data=truncate_callback_data("pull:", name))])
        if len(updates) > IMAGE_UPDATE_MAX_SHOWN:
            lines.append(f"...and {len(updates) - IMAGE_UPDATE_MAX_SHOWN} more")
        keyboard = InlineKeyboardMarkup(inline_keyboard=rows)
        return await self._deliver("\n".join(lines), keyboard, f"Sent image-update digest for {len(updates)} container(s)", "Failed to send update alert")

    async def send_recovery_alert(self, container_name: str) -> bool:
        """Send a brief recovery notification when a crashed container starts."""
        text = f"✅ *{escape_markdown(container_name)}* recovered and is running again."
        return await self._deliver(text, None, f"Sent recovery alert for {container_name}", "Failed to send recovery alert")
