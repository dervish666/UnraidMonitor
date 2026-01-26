"""Base class for mute managers with shared persistence logic."""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


class BaseMuteManager:
    """Base class providing JSON persistence for mute state.

    Subclasses use a dict[str, datetime] mapping keys to expiry times.
    """

    def __init__(self, json_path: str):
        """Initialize BaseMuteManager.

        Args:
            json_path: Path to JSON file for persistence.
        """
        self._json_path = Path(json_path)
        self._mutes: dict[str, datetime] = {}
        self._load()

    def _is_muted(self, key: str) -> bool:
        """Check if a key is currently muted.

        Returns False if mute has expired, cleaning up the expired entry.
        """
        if key not in self._mutes:
            return False

        expiry = self._mutes[key]
        if datetime.now() >= expiry:
            del self._mutes[key]
            self._save()
            return False

        return True

    def _add_mute(self, key: str, duration: timedelta) -> datetime:
        """Add a mute for a key.

        Args:
            key: The key to mute.
            duration: How long to mute.

        Returns:
            Expiry datetime.
        """
        expiry = datetime.now() + duration
        self._mutes[key] = expiry
        self._save()
        return expiry

    def _remove_mute(self, key: str) -> bool:
        """Remove a mute early.

        Returns:
            True if mute was removed, False if not found.
        """
        if key not in self._mutes:
            return False

        del self._mutes[key]
        self._save()
        return True

    def _get_active_mutes(self) -> list[tuple[str, datetime]]:
        """Get list of active mutes.

        Returns:
            List of (key, expiry) tuples.
        """
        self._clean_expired()
        return [(key, exp) for key, exp in self._mutes.items()]

    def _clean_expired(self) -> None:
        """Remove expired mutes."""
        now = datetime.now()
        expired = [key for key, exp in self._mutes.items() if now >= exp]
        for key in expired:
            del self._mutes[key]
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
                    key: datetime.fromisoformat(exp)
                    for key, exp in data.items()
                }
        except (json.JSONDecodeError, IOError, ValueError) as e:
            logger.warning(f"Failed to load mutes from {self._json_path}: {e}")
            self._mutes = {}

    def _save(self) -> None:
        """Save mutes to JSON file."""
        self._json_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            data = {
                key: exp.isoformat()
                for key, exp in self._mutes.items()
            }
            with open(self._json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except IOError as e:
            logger.error(f"Failed to save mutes to {self._json_path}: {e}")
