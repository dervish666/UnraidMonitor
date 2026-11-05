"""Base class for mute managers with shared persistence logic."""

import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from src.utils.json_store import atomic_write_json, load_json, log_rejected

logger = logging.getLogger(__name__)


def save_note_of(manager: Any) -> str:
    """``manager.save_note()`` for anything that may or may not be a mute manager."""
    note = getattr(manager, "save_note", None)
    text = note() if callable(note) else ""
    return text if isinstance(text, str) else ""


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
        self._lock = threading.RLock()
        self._dirty = False
        # Why the last save failed, until one succeeds. A mute that is not on
        # disk silently ends at the next restart, so callers tell the user.
        self.save_error: str | None = None
        self._recently_expired: list[str] = []
        self._load()

    def _is_muted(self, key: str) -> bool:
        """Check if a key is currently muted.

        Returns False if mute has expired, cleaning up the expired entry.
        Expired entries are removed from memory but file save is deferred
        to avoid disk I/O on every check.
        """
        with self._lock:
            if key not in self._mutes:
                return False

            expiry = self._mutes[key]
            if datetime.now() >= expiry:
                del self._mutes[key]
                self._dirty = True
                self._recently_expired.append(key)
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
        with self._lock:
            expiry = datetime.now() + duration
            self._mutes[key] = expiry
            self._dirty = True
            self._save()
            return expiry

    def _remove_mute(self, key: str) -> bool:
        """Remove a mute early.

        Returns:
            True if mute was removed, False if not found.
        """
        with self._lock:
            if key not in self._mutes:
                return False

            del self._mutes[key]
            self._dirty = True
            self._save()
            return True

    def _get_active_mutes(self) -> list[tuple[str, datetime]]:
        """Get list of active mutes.

        Returns:
            List of (key, expiry) tuples.
        """
        with self._lock:
            self._clean_expired()
            return [(key, exp) for key, exp in self._mutes.items()]

    def _clean_expired(self) -> None:
        """Remove expired mutes from memory. Does not save to disk immediately."""
        with self._lock:
            now = datetime.now()
            expired = [key for key, exp in self._mutes.items() if now >= exp]
            for key in expired:
                del self._mutes[key]
                # Same bookkeeping as the is-muted path, or listing mutes
                # first would swallow the "mute expired" notice.
                self._recently_expired.append(key)
            if expired:
                self._dirty = True

    def drain_expired(self) -> list[str]:
        """Return and clear the list of recently expired mute keys.

        Sweeps first: is-muted checks expire keys one at a time, so without
        this one /mute-server (three categories, one expiry) drained as up
        to three separate notices on different maintenance passes.
        """
        with self._lock:
            self._clean_expired()
            expired = self._recently_expired[:]
            self._recently_expired.clear()
            return expired

    def flush(self) -> None:
        """Persist deferred changes to disk if dirty; stays dirty if that fails."""
        with self._lock:
            if self._dirty:
                self._save()

    def save_note(self) -> str:
        """A line for the user when mutes are not on disk, else ''."""
        if self.save_error is None:
            return ""
        return (
            "\n⚠️ Could not save mutes to disk, so this change lasts only until "
            "the bot restarts. Check the data volume."
        )

    def _load(self) -> None:
        """Load mutes, keeping every well-formed entry.

        A file that is not a JSON object is set aside (``.corrupt-<ts>``)
        rather than overwritten by the next save.
        """
        with self._lock:
            self._mutes = {}
            data = load_json(self._json_path, dict, "mutes")
            if data is None:
                return
            rejected: list[str] = []
            for key, exp in data.items():
                try:
                    dt = datetime.fromisoformat(exp)
                except (TypeError, ValueError):
                    rejected.append(str(key))
                    continue
                # Naive-local, for comparison with datetime.now()
                self._mutes[str(key)] = dt.replace(tzinfo=None) if dt.tzinfo else dt
            log_rejected(str(self._json_path), rejected)

    def _save(self) -> bool:
        """Write mutes atomically. Clears the dirty flag only on success."""
        with self._lock:
            data = {key: exp.isoformat() for key, exp in self._mutes.items()}
            try:
                atomic_write_json(self._json_path, data, indent=2)
            except (OSError, TypeError) as e:
                self.save_error = str(e)
                logger.error(f"Failed to save mutes to {self._json_path}: {e}")
                return False
            self._dirty = False
            self.save_error = None
            return True
