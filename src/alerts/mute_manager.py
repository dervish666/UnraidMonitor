"""Mute manager for container alerts."""

import re
import logging
from datetime import datetime, timedelta

from src.alerts.base_mute_manager import BaseMuteManager

logger = logging.getLogger(__name__)

DURATION_PATTERN = re.compile(r"^(\d+)(m|h)$")


def parse_duration(text: str) -> timedelta | None:
    """Parse duration string like '15m' or '2h'.

    Args:
        text: Duration string (e.g., '15m', '2h', '24h').

    Returns:
        timedelta if valid, None if invalid.
    """
    if not text:
        return None

    match = DURATION_PATTERN.match(text.strip().lower())
    if not match:
        return None

    value = int(match.group(1))
    unit = match.group(2)

    if value <= 0:
        return None

    if unit == "m":
        return timedelta(minutes=value)
    elif unit == "h":
        return timedelta(hours=value)

    return None


class MuteManager(BaseMuteManager):
    """Manages temporary mutes for containers."""

    def is_muted(self, container: str) -> bool:
        """Check if container is currently muted."""
        return self._is_muted(container)

    def add_mute(self, container: str, duration: timedelta) -> datetime:
        """Add a mute for container."""
        expiry = self._add_mute(container, duration)
        logger.info(f"Muted {container} until {expiry}")
        return expiry

    def remove_mute(self, container: str) -> bool:
        """Remove a mute early."""
        removed = self._remove_mute(container)
        if removed:
            logger.info(f"Unmuted {container}")
        return removed

    def get_active_mutes(self) -> list[tuple[str, datetime]]:
        """Get list of active mutes."""
        return self._get_active_mutes()
