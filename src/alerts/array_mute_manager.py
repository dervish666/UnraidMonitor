"""Mute manager for array/disk alerts."""

import logging
from datetime import datetime, timedelta

from src.alerts.base_mute_manager import BaseMuteManager

logger = logging.getLogger(__name__)


class ArrayMuteManager(BaseMuteManager):
    """Manages mutes for array/disk alerts independently from server alerts."""

    # Use a single key for the array mute
    _KEY = "array"

    def is_array_muted(self) -> bool:
        """Check if array/disk alerts are currently muted."""
        return self._is_muted(self._KEY)

    def mute_array(self, duration: timedelta) -> datetime:
        """Mute array/disk alerts for the specified duration."""
        expiry = self._add_mute(self._KEY, duration)
        logger.info(f"Muted array alerts until {expiry}")
        return expiry

    def unmute_array(self) -> bool:
        """Unmute array/disk alerts."""
        removed = self._remove_mute(self._KEY)
        if removed:
            logger.info("Unmuted array alerts")
        return removed

    def get_mute_expiry(self) -> datetime | None:
        """Get the mute expiry time."""
        if self._KEY not in self._mutes:
            return None

        if datetime.now() >= self._mutes[self._KEY]:
            del self._mutes[self._KEY]
            self._save()
            return None

        return self._mutes[self._KEY]
