"""Shared formatting utility functions."""

import logging
import re
from datetime import datetime, timedelta
from typing import Any

from aiogram.types import Message, MaybeInaccessibleMessage
from aiogram.exceptions import TelegramBadRequest

from src.constants import MAX_CONTAINER_NAME_LENGTH
from src.utils.telegram_format import strip_html_tags

logger = logging.getLogger(__name__)

# Valid Docker container name pattern (alphanumeric, dash, underscore, dot, colon)
# Docker allows: [a-zA-Z0-9][a-zA-Z0-9_.-]* but we also allow colons for compose names
_VALID_CONTAINER_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.:-]*$")


def validate_container_name(name: str) -> bool:
    """Validate that a string looks like a valid container name."""
    if not name or len(name) > MAX_CONTAINER_NAME_LENGTH:
        return False
    return bool(_VALID_CONTAINER_NAME.match(name))


def _strip_markdown(text: str) -> str:
    """Strip Markdown V1 formatting characters for plain-text fallback."""
    return text.replace("*", "").replace("`", "").replace("_", "").replace("[", "").replace("]", "")


def _plain_fallback(text: str, parse_mode: str | None) -> str:
    """Reduce formatted text to plain text for the parse-failure fallback."""
    if parse_mode == "HTML":
        return strip_html_tags(text)
    return _strip_markdown(text)


async def safe_reply(
    message: Message,
    text: str,
    parse_mode: str = "Markdown",
    **kwargs: Any,
) -> Message:
    """Send a formatted message, falling back to plain text on parse failure."""
    try:
        return await message.answer(text, parse_mode=parse_mode, **kwargs)
    except TelegramBadRequest as e:
        if "can't parse entities" in str(e):
            return await message.answer(_plain_fallback(text, parse_mode), **kwargs)
        raise


async def safe_edit(
    message: "Message | MaybeInaccessibleMessage",
    text: str,
    parse_mode: str = "Markdown",
    **kwargs: Any,
) -> Message | bool:
    """Edit a message with formatting, falling back to plain text on parse failure."""
    try:
        return await message.edit_text(text, parse_mode=parse_mode, **kwargs)  # type: ignore[union-attr]
    except TelegramBadRequest as e:
        if "can't parse entities" in str(e):
            return await message.edit_text(_plain_fallback(text, parse_mode), **kwargs)  # type: ignore[union-attr]
        if "message is not modified" in str(e):
            # Benign: a Refresh that found nothing changed. The message already
            # shows what we wanted it to.
            logger.debug("Edit skipped: message content unchanged")
            return False
        raise


def format_mute_expiry(expiry: datetime) -> str:
    """Format mute expiry in a human-readable way.

    - Same day: "until 14:30"
    - Tomorrow: "until tomorrow 14:30"
    - Further: "until Feb 26 14:30"
    """
    import os
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(os.environ.get("TZ", "Europe/London"))
    now = datetime.now(tz)

    # Make expiry timezone-aware if naive
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=tz)
    else:
        expiry = expiry.astimezone(tz)

    time_str = expiry.strftime("%H:%M")

    if expiry.date() == now.date():
        return f"until {time_str}"
    elif expiry.date() == (now + timedelta(days=1)).date():
        return f"until tomorrow {time_str}"
    else:
        return f"until {expiry.strftime('%b %d')} {time_str}"

# Common log timestamp patterns to strip for pattern matching
# Matches: 2026-02-25T11:55:11.548437Z, 2026-02-25 11:55:11,548, [2026-02-25T11:55:11]
_LOG_TIMESTAMP_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}[.,]?\d*Z?\s*"
)


def strip_log_timestamps(line: str) -> str:
    """Strip common timestamp patterns from a log line.

    Removes ISO8601, Python logging, and similar timestamp formats so that
    patterns match future errors regardless of when they occurred.
    """
    return _LOG_TIMESTAMP_RE.sub("", line).strip()


# Patterns to extract a container name from an alert the user replied to.
#
# Telegram hands back `Message.text` with formatting *stripped* -- the markup
# lives in `entities`. So an alert sent as "⚠️ *ERRORS IN:* plex" arrives here
# as "⚠️ ERRORS IN: plex". Patterns therefore match the rendered text, and
# tolerate stray asterisks only so raw-source strings still work in tests.
#
# Two rules earn their keep, both learned the hard way:
#   1. Anchor to the start of a line. Unanchored + case-insensitive "CRASHED"
#      matched "Crashed 4 times in the last 10 minutes" in the body of a
#      restart-loop alert and extracted the container name "4".
#   2. Require the colon, and match the label's real case. Same reason.
# Order matters: RESTART LOOP is checked before CONTAINER CRASHED because both
# alerts come from the same sender and share vocabulary.
_NAME = r"([\w.\\-]+)"
_ALERT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(rf"^[^\w\n]*\**RESTART LOOP:\**\s+{_NAME}", re.M), "Restart loop alert"),
    (re.compile(rf"^[^\w\n]*\**(?:CONTAINER )?CRASHED:\**\s+{_NAME}", re.M), "Container crash alert"),
    (re.compile(rf"^[^\w\n]*\**ERRORS IN:\**\s+{_NAME}", re.M), "Error alert (container still running with errors)"),
    (re.compile(rf"^[^\w\n]*\**HIGH \w+ USAGE:\**\s+{_NAME}", re.M), "High resource usage alert"),
    (re.compile(rf"^[^\w\n]*\**UNHEALTHY:\**\s+{_NAME}", re.M), "Failing health check alert"),
    (re.compile(rf"^[^\w\n]*\**Auto-heal gave up:\**\s+{_NAME}", re.M), "Auto-heal gave up alert"),
    (re.compile(rf"^[^\w\n]*\**Auto-heal failed:\**\s+{_NAME}", re.M), "Auto-heal failed alert"),
    (re.compile(rf"^[^\w\n]*\**Auto-restarted:\**\s+{_NAME}", re.M), "Auto-heal restart alert"),
    (re.compile(rf"^[^\w\n]*\**Container:\**\s+{_NAME}", re.M), "Container status message"),
]


def extract_alert_container(text: str) -> tuple[str | None, str]:
    """Extract the container name and a description of the alert it came from.

    Args:
        text: The rendered text of the alert being replied to.

    Returns:
        (container_name, alert_description), or (None, "") if nothing matched.
    """
    if not text:
        return None, ""
    for pattern, description in _ALERT_PATTERNS:
        match = pattern.search(text)
        if match:
            # Markdown escapes survive if a caller passes raw source text.
            name = match.group(1).replace("\\", "")
            if name:
                return name, description
    return None, ""


def extract_container_from_alert(text: str) -> str | None:
    """Extract container name from any alert type message.

    Args:
        text: Alert message text.

    Returns:
        Container name if found, None otherwise.
    """
    return extract_alert_container(text)[0]


def format_bytes(bytes_val: int) -> str:
    """Format bytes as human-readable string.

    Args:
        bytes_val: Number of bytes.

    Returns:
        Human-readable string like "1.5GB" or "500MB".
    """
    gb = bytes_val / (1024**3)
    if gb >= 1.0:
        return f"{gb:.1f}GB"
    mb = bytes_val / (1024**2)
    return f"{mb:.0f}MB"


def format_duration_minutes(minutes: int) -> str:
    """Format a duration in minutes to human-readable string."""
    if minutes >= 1440:
        return f"{minutes // 1440} day(s)"
    elif minutes >= 60:
        return f"{minutes // 60} hour(s)"
    return f"{minutes} minute(s)"


def format_uptime(seconds: int) -> str:
    """Format seconds into human-readable uptime.

    Args:
        seconds: Uptime in seconds.

    Returns:
        Human-readable string like "3d 14h 22m" or "2h 15m" or "45m".
    """
    if seconds < 0:
        return "0m"
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60

    parts: list[str] = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0 or not parts:
        parts.append(f"{minutes}m")
    return " ".join(parts)


# Telegram message length limit
TELEGRAM_MAX_LENGTH = 4096


def truncate_message(text: str, max_length: int = TELEGRAM_MAX_LENGTH, suffix: str = "\n\n_(truncated)_") -> str:
    """Truncate a message to fit within Telegram's character limit.

    Args:
        text: The message text.
        max_length: Maximum allowed characters (default: 4096).
        suffix: Text appended when truncated.

    Returns:
        The original text if within limit, or truncated text with suffix.
    """
    if len(text) <= max_length:
        return text
    return text[:max_length - len(suffix)] + suffix


def escape_markdown(text: str) -> str:
    """Escape Telegram Markdown V1 special characters.

    Args:
        text: Raw text that may contain *, _, `, [ characters.

    Returns:
        Text with special characters escaped.
    """
    for ch in ("\\", "`", "*", "_", "["):
        text = text.replace(ch, f"\\{ch}")
    return text


def truncate_callback_data(prefix: str, data: str) -> str:
    """Build callback_data that fits within Telegram's 64-byte UTF-8 limit.

    Args:
        prefix: The callback prefix including trailing separator (e.g. "restart:").
        data: The data portion (e.g. container name).

    Returns:
        Combined string truncated to fit 64 UTF-8 bytes.
    """
    combined = f"{prefix}{data}"
    encoded = combined.encode("utf-8")
    if len(encoded) <= 64:
        return combined
    # Truncate data to fit, leaving room for ellipsis
    suffix = "…"
    max_data_bytes = 64 - len(prefix.encode("utf-8")) - len(suffix.encode("utf-8"))
    truncated = data.encode("utf-8")[:max_data_bytes].decode("utf-8", errors="ignore")
    return f"{prefix}{truncated}{suffix}"
