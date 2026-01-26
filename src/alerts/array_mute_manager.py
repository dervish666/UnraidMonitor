import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


class ArrayMuteManager:
    """Manages mutes for array/disk alerts independently from server alerts."""

    def __init__(self, json_path: str):
        """Initialize ArrayMuteManager.

        Args:
            json_path: Path to JSON file for persistence.
        """
        self._json_path = Path(json_path)
        self._mute_expiry: datetime | None = None
        self._load()

    def is_array_muted(self) -> bool:
        """Check if array/disk alerts are currently muted.

        Returns:
            True if muted and not expired, False otherwise.
        """
        if self._mute_expiry is None:
            return False

        if datetime.now() >= self._mute_expiry:
            self._mute_expiry = None
            self._save()
            return False

        return True

    def mute_array(self, duration: timedelta) -> datetime:
        """Mute array/disk alerts for the specified duration.

        Args:
            duration: How long to mute alerts.

        Returns:
            The expiry time for the mute.
        """
        expiry = datetime.now() + duration
        self._mute_expiry = expiry
        self._save()
        logger.info(f"Muted array alerts until {expiry}")
        return expiry

    def unmute_array(self) -> bool:
        """Unmute array/disk alerts.

        Returns:
            True if the array was muted before unmuting, False otherwise.
        """
        if self._mute_expiry is None:
            return False

        self._mute_expiry = None
        self._save()
        logger.info("Unmuted array alerts")
        return True

    def get_mute_expiry(self) -> datetime | None:
        """Get the mute expiry time.

        Returns:
            The expiry time if muted, None otherwise.
        """
        if self._mute_expiry is None:
            return None

        if datetime.now() >= self._mute_expiry:
            self._mute_expiry = None
            self._save()
            return None

        return self._mute_expiry

    def _load(self) -> None:
        """Load mute state from JSON file."""
        if not self._json_path.exists():
            self._mute_expiry = None
            return

        try:
            with open(self._json_path, encoding="utf-8") as f:
                data = json.load(f)
                expiry_str = data.get("expiry")
                if expiry_str:
                    self._mute_expiry = datetime.fromisoformat(expiry_str)
                else:
                    self._mute_expiry = None
        except (json.JSONDecodeError, IOError, ValueError) as e:
            logger.warning(f"Failed to load array mutes: {e}")
            self._mute_expiry = None

    def _save(self) -> None:
        """Save mute state to JSON file."""
        self._json_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            data = {}
            if self._mute_expiry is not None:
                data["expiry"] = self._mute_expiry.isoformat()

            with open(self._json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except IOError as e:
            logger.error(f"Failed to save array mutes: {e}")
