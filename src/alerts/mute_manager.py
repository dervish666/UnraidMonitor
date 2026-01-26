import json
import re
import logging
from datetime import datetime, timedelta
from pathlib import Path

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


class MuteManager:
    """Manages temporary mutes for containers."""

    def __init__(self, json_path: str):
        """Initialize MuteManager.

        Args:
            json_path: Path to JSON file for persistence.
        """
        self._json_path = Path(json_path)
        self._mutes: dict[str, datetime] = {}
        self._load()

    def is_muted(self, container: str) -> bool:
        """Check if container is currently muted.

        Returns False if mute has expired.
        """
        if container not in self._mutes:
            return False

        expiry = self._mutes[container]
        if datetime.now() >= expiry:
            # Expired, clean up
            del self._mutes[container]
            self._save()
            return False

        return True

    def add_mute(self, container: str, duration: timedelta) -> datetime:
        """Add a mute for container.

        Args:
            container: Container name.
            duration: How long to mute.

        Returns:
            Expiry datetime.
        """
        expiry = datetime.now() + duration
        self._mutes[container] = expiry
        self._save()
        logger.info(f"Muted {container} until {expiry}")
        return expiry

    def remove_mute(self, container: str) -> bool:
        """Remove a mute early.

        Returns:
            True if mute was removed, False if not found.
        """
        if container not in self._mutes:
            return False

        del self._mutes[container]
        self._save()
        logger.info(f"Unmuted {container}")
        return True

    def get_active_mutes(self) -> list[tuple[str, datetime]]:
        """Get list of active mutes.

        Returns:
            List of (container, expiry) tuples.
        """
        # Clean expired mutes first
        now = datetime.now()
        expired = [c for c, exp in self._mutes.items() if now >= exp]
        for c in expired:
            del self._mutes[c]
        if expired:
            self._save()

        return [(c, exp) for c, exp in self._mutes.items()]

    def _load(self) -> None:
        """Load mutes from JSON file."""
        if not self._json_path.exists():
            self._mutes = {}
            return

        try:
            with open(self._json_path, encoding="utf-8") as f:
                data = json.load(f)
                self._mutes = {
                    c: datetime.fromisoformat(exp)
                    for c, exp in data.items()
                }
        except (json.JSONDecodeError, IOError, ValueError) as e:
            logger.warning(f"Failed to load mutes: {e}")
            self._mutes = {}

    def _save(self) -> None:
        """Save mutes to JSON file."""
        self._json_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            data = {
                c: exp.isoformat()
                for c, exp in self._mutes.items()
            }
            with open(self._json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except IOError as e:
            logger.error(f"Failed to save mutes: {e}")
