"""Base class for mute managers with shared persistence logic."""

import json
import logging
import os
import tempfile
import threading
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
        self._lock = threading.RLock()
        self._load()
        self._dirty = False
        self._recently_expired: list[str] = []

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
        """Return and clear the list of recently expired mute keys."""
        with self._lock:
            expired = self._recently_expired[:]
            self._recently_expired.clear()
            return expired

    def flush(self) -> None:
        """Persist deferred changes to disk if dirty."""
        with self._lock:
            if self._dirty:
                self._save()
                self._dirty = False

    def _load(self) -> None:
        """Load mutes from JSON file."""
        with self._lock:
            if not self._json_path.exists():
                self._mutes = {}
                return

            try:
                with open(self._json_path, encoding="utf-8") as f:
                    data = json.load(f)
                    self._mutes = {}
                    for key, exp in data.items():
                        dt = datetime.fromisoformat(exp)
                        # Ensure naive-local for consistent comparison with datetime.now()
                        if dt.tzinfo is not None:
                            dt = dt.replace(tzinfo=None)
                        self._mutes[key] = dt
            except (json.JSONDecodeError, IOError, ValueError) as e:
                logger.warning(f"Failed to load mutes from {self._json_path}: {e}")
                self._mutes = {}

    def _save(self) -> None:
        """Save mutes to JSON file using atomic write pattern."""
        with self._lock:
            self._json_path.parent.mkdir(parents=True, exist_ok=True)

            try:
                data = {
                    key: exp.isoformat()
                    for key, exp in self._mutes.items()
                }

                # Atomic write: write to temp file, then rename
                fd, temp_path = tempfile.mkstemp(
                    dir=self._json_path.parent,
                    prefix=".tmp_mutes_",
                    suffix=".json",
                )
                try:
                    os.fchmod(fd, 0o644)
                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=2)
                    os.replace(temp_path, self._json_path)  # Atomic on POSIX
                    self._dirty = False
                except Exception:
                    # Clean up temp file on error
                    try:
                        os.unlink(temp_path)
                    except OSError:
                        pass
                    raise
            except IOError as e:
                logger.error(f"Failed to save mutes to {self._json_path}: {e}")
