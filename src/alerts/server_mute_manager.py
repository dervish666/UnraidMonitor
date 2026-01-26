"""Mute manager for Unraid server alerts."""

import logging
from datetime import datetime, timedelta

from src.alerts.base_mute_manager import BaseMuteManager

logger = logging.getLogger(__name__)


class ServerMuteManager(BaseMuteManager):
    """Manages mutes for Unraid server alerts (separate from container mutes)."""

    CATEGORIES = ("server", "array", "ups")

    def is_server_muted(self) -> bool:
        """Check if server (system) alerts are muted."""
        return self._is_muted("server")

    def is_array_muted(self) -> bool:
        """Check if array/disk alerts are muted."""
        return self._is_muted("array")

    def is_ups_muted(self) -> bool:
        """Check if UPS alerts are muted."""
        return self._is_muted("ups")

    def mute_server(self, duration: timedelta) -> datetime:
        """Mute all server alerts (system, array, UPS)."""
        expiry = datetime.now() + duration
        for cat in self.CATEGORIES:
            self._mutes[cat] = expiry
        self._save()
        logger.info(f"Muted all server alerts until {expiry}")
        return expiry

    def mute_array(self, duration: timedelta) -> datetime:
        """Mute just array/disk alerts."""
        expiry = self._add_mute("array", duration)
        logger.info(f"Muted array alerts until {expiry}")
        return expiry

    def mute_ups(self, duration: timedelta) -> datetime:
        """Mute just UPS alerts."""
        expiry = self._add_mute("ups", duration)
        logger.info(f"Muted UPS alerts until {expiry}")
        return expiry

    def unmute_server(self) -> bool:
        """Unmute all server alerts."""
        removed = False
        for cat in self.CATEGORIES:
            if cat in self._mutes:
                del self._mutes[cat]
                removed = True
        if removed:
            self._save()
            logger.info("Unmuted all server alerts")
        return removed

    def unmute_array(self) -> bool:
        """Unmute array alerts."""
        removed = self._remove_mute("array")
        if removed:
            logger.info("Unmuted array alerts")
        return removed

    def unmute_ups(self) -> bool:
        """Unmute UPS alerts."""
        removed = self._remove_mute("ups")
        if removed:
            logger.info("Unmuted UPS alerts")
        return removed

    def get_active_mutes(self) -> list[tuple[str, datetime]]:
        """Get list of active mutes."""
        return self._get_active_mutes()
