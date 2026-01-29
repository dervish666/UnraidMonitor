import threading
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass
class RecentError:
    """A recent error with timestamp."""
    message: str
    timestamp: datetime


class RecentErrorsBuffer:
    """Buffer to track recent errors per container.

    Thread-safe: This class is accessed from multiple threads (log watcher
    adds errors, Telegram handlers read errors).
    """

    def __init__(self, max_age_seconds: int = 900, max_per_container: int = 50):
        self.max_age_seconds = max_age_seconds
        self.max_per_container = max_per_container
        self._errors: dict[str, list[RecentError]] = {}
        self._lock = threading.Lock()

    def add(self, container: str, message: str) -> None:
        """Add an error to the buffer."""
        with self._lock:
            if container not in self._errors:
                self._errors[container] = []

            self._errors[container].append(
                RecentError(message=message, timestamp=datetime.now())
            )

            # Prune old entries and cap at max
            self._prune_unlocked(container)

    def get_recent(self, container: str) -> list[str]:
        """Get unique recent error messages for a container."""
        with self._lock:
            if container not in self._errors:
                return []

            self._prune_unlocked(container)

            # Return unique messages, preserving order of first occurrence
            seen = set()
            unique = []
            for error in self._errors[container]:
                if error.message not in seen:
                    seen.add(error.message)
                    unique.append(error.message)
            return unique

    def _prune_unlocked(self, container: str) -> None:
        """Remove old entries and cap at max.

        Note: Must be called with self._lock held.
        """
        if container not in self._errors:
            return

        cutoff = datetime.now() - timedelta(seconds=self.max_age_seconds)
        self._errors[container] = [
            e for e in self._errors[container]
            if e.timestamp > cutoff
        ][-self.max_per_container:]
