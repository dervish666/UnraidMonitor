import threading
from datetime import datetime, timedelta


class RateLimiter:
    """Rate limiter to prevent alert spam."""

    # Remove entries older than this to prevent unbounded growth
    _STALE_THRESHOLD = timedelta(hours=24)
    _CLEANUP_EVERY_N_CHECKS = 100

    def __init__(self, cooldown_seconds: int = 900):
        self.cooldown_seconds = cooldown_seconds
        self._last_alert: dict[str, datetime] = {}
        self._suppressed_count: dict[str, int] = {}
        self._check_count: int = 0
        self._lock = threading.Lock()

    def should_alert(self, container_name: str) -> bool:
        """Check if an alert should be sent for this container."""
        with self._lock:
            self._check_count += 1
            if self._check_count >= self._CLEANUP_EVERY_N_CHECKS:
                self._check_count = 0
                self._cleanup_stale_locked()

            last = self._last_alert.get(container_name)
            if last is None:
                return True

            elapsed = datetime.now() - last
            return elapsed >= timedelta(seconds=self.cooldown_seconds)

    def record_alert(self, container_name: str) -> None:
        """Record that an alert was sent."""
        with self._lock:
            self._last_alert[container_name] = datetime.now()
            self._suppressed_count[container_name] = 0

    def cancel_alert(self, container_name: str, suppressed: int) -> None:
        """Undo record_alert() for an alert that was never delivered.

        The send was reserved before an await to stop two lines racing into
        two alerts; when it then fails, the cooldown must not start.
        """
        with self._lock:
            self._last_alert.pop(container_name, None)
            self._suppressed_count[container_name] = suppressed

    def record_suppressed(self, container_name: str) -> None:
        """Record that an alert was suppressed."""
        with self._lock:
            current = self._suppressed_count.get(container_name, 0)
            self._suppressed_count[container_name] = current + 1

    def get_suppressed_count(self, container_name: str) -> int:
        """Get count of suppressed alerts since last sent alert."""
        with self._lock:
            return self._suppressed_count.get(container_name, 0)

    def cleanup_stale(self) -> int:
        """Remove entries older than the stale threshold.

        Returns:
            Number of entries removed.
        """
        with self._lock:
            return self._cleanup_stale_locked()

    def _cleanup_stale_locked(self) -> int:
        """Internal cleanup, must be called with lock held."""
        now = datetime.now()
        stale_keys = [
            key for key, ts in self._last_alert.items()
            if now - ts > self._STALE_THRESHOLD
        ]
        for key in stale_keys:
            del self._last_alert[key]
            self._suppressed_count.pop(key, None)
        return len(stale_keys)
