"""Mute manager for Unraid server alerts."""

import json
import logging
import os
from datetime import datetime, timedelta

from src.alerts.base_mute_manager import BaseMuteManager

logger = logging.getLogger(__name__)


class ServerMuteManager(BaseMuteManager):
    """The one mute store for Unraid system, array and UPS alerts.

    /mute-server mutes all three categories; the per-alert buttons and
    /mute-array mute one. Array mutes used to live in a separate
    ArrayMuteManager that ArrayMonitor checked, so /mute-server said it
    silenced the array and did not.
    """

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
        with self._lock:
            expiry = datetime.now() + duration
            for cat in self.CATEGORIES:
                self._mutes[cat] = expiry
            self._dirty = True
            self._save()
        logger.info(f"Muted all server alerts until {expiry}")
        return expiry

    def mute_system(self, duration: timedelta) -> datetime:
        """Mute just system (CPU/memory/temperature) alerts."""
        expiry = self._add_mute("server", duration)
        logger.info(f"Muted system alerts until {expiry}")
        return expiry

    def unmute_system(self) -> bool:
        """Unmute just system alerts."""
        removed = self._remove_mute("server")
        if removed:
            logger.info("Unmuted system alerts")
        return removed

    def get_category_expiry(self, category: str) -> datetime | None:
        """Expiry of an active mute on one category, else None."""
        for cat, expiry in self._get_active_mutes():
            if cat == category:
                return expiry
        return None

    def import_legacy_array_mutes(self, legacy_path: str) -> bool:
        """One-time import of the retired ArrayMuteManager file.

        Carries an unexpired array mute across, then renames the old file so
        the import never runs twice, but only once the mute is on disk in the
        new store. Returns True when a mute was imported.
        """
        if not os.path.exists(legacy_path):
            return False
        imported = False
        try:
            with open(legacy_path, encoding="utf-8") as f:
                data = json.load(f)
            raw = data.get("array") if isinstance(data, dict) else None
            if isinstance(raw, str):
                expiry = datetime.fromisoformat(raw)
                if expiry.tzinfo is not None:
                    expiry = expiry.replace(tzinfo=None)
                current = self.get_category_expiry("array")
                if expiry > datetime.now() and (current is None or expiry > current):
                    self._add_mute("array", expiry - datetime.now())
                    imported = True
        except (OSError, ValueError, json.JSONDecodeError) as e:
            logger.warning(f"Could not import legacy array mutes from {legacy_path}: {e}")
        if imported and self._dirty:
            # The mute is only in memory. Keep the source so the next start
            # imports it again instead of losing it.
            logger.warning(f"Imported array mute not saved yet; keeping {legacy_path}")
            return imported
        try:
            os.replace(legacy_path, legacy_path + ".migrated")
        except OSError as e:
            logger.warning(f"Could not retire {legacy_path}: {e}")
        if imported:
            logger.info("Imported array mute from the retired array_mutes.json")
        return imported

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

    def unmute_ups(self) -> bool:
        """Unmute UPS alerts."""
        removed = self._remove_mute("ups")
        if removed:
            logger.info("Unmuted UPS alerts")
        return removed

    def unmute_server(self) -> bool:
        """Unmute all server alerts."""
        with self._lock:
            removed = False
            for cat in self.CATEGORIES:
                if cat in self._mutes:
                    del self._mutes[cat]
                    removed = True
            if removed:
                self._dirty = True
                self._save()
        if removed:
            logger.info("Unmuted all server alerts")
        return removed

    def unmute_array(self) -> bool:
        """Unmute array alerts."""
        removed = self._remove_mute("array")
        if removed:
            logger.info("Unmuted array alerts")
        return removed

    def get_active_mutes(self) -> list[tuple[str, datetime]]:
        """Get list of active mutes."""
        return self._get_active_mutes()
