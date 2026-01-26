import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


class ServerMuteManager:
    """Manages mutes for Unraid server alerts (separate from container mutes)."""

    CATEGORIES = ("server", "array", "ups")

    def __init__(self, json_path: str):
        """Initialize ServerMuteManager.

        Args:
            json_path: Path to JSON file for persistence.
        """
        self._json_path = Path(json_path)
        self._mutes: dict[str, datetime] = {}
        self._load()

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
        expiry = datetime.now() + duration
        self._mutes["array"] = expiry
        self._save()
        logger.info(f"Muted array alerts until {expiry}")
        return expiry

    def mute_ups(self, duration: timedelta) -> datetime:
        """Mute just UPS alerts."""
        expiry = datetime.now() + duration
        self._mutes["ups"] = expiry
        self._save()
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
        return self._unmute("array")

    def unmute_ups(self) -> bool:
        """Unmute UPS alerts."""
        return self._unmute("ups")

    def get_active_mutes(self) -> list[tuple[str, datetime]]:
        """Get list of active mutes.

        Returns:
            List of (category, expiry) tuples.
        """
        self._clean_expired()
        return [(cat, exp) for cat, exp in self._mutes.items()]

    def _is_muted(self, category: str) -> bool:
        """Check if a category is currently muted."""
        if category not in self._mutes:
            return False

        expiry = self._mutes[category]
        if datetime.now() >= expiry:
            del self._mutes[category]
            self._save()
            return False

        return True

    def _unmute(self, category: str) -> bool:
        """Unmute a specific category."""
        if category not in self._mutes:
            return False

        del self._mutes[category]
        self._save()
        logger.info(f"Unmuted {category} alerts")
        return True

    def _clean_expired(self) -> None:
        """Remove expired mutes."""
        now = datetime.now()
        expired = [cat for cat, exp in self._mutes.items() if now >= exp]
        for cat in expired:
            del self._mutes[cat]
        if expired:
            self._save()

    def _load(self) -> None:
        """Load mutes from JSON file."""
        if not self._json_path.exists():
            self._mutes = {}
            return

        try:
            with open(self._json_path, encoding="utf-8") as f:
                data = json.load(f)
                self._mutes = {
                    cat: datetime.fromisoformat(exp)
                    for cat, exp in data.items()
                }
        except (json.JSONDecodeError, IOError, ValueError) as e:
            logger.warning(f"Failed to load server mutes: {e}")
            self._mutes = {}

    def _save(self) -> None:
        """Save mutes to JSON file."""
        self._json_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            data = {
                cat: exp.isoformat()
                for cat, exp in self._mutes.items()
            }
            with open(self._json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except IOError as e:
            logger.error(f"Failed to save server mutes: {e}")
