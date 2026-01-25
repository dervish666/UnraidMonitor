from datetime import datetime, timedelta


class RateLimiter:
    """Rate limiter to prevent alert spam."""

    def __init__(self, cooldown_seconds: int = 900):
        self.cooldown_seconds = cooldown_seconds
        self._last_alert: dict[str, datetime] = {}
        self._suppressed_count: dict[str, int] = {}

    def should_alert(self, container_name: str) -> bool:
        """Check if an alert should be sent for this container."""
        last = self._last_alert.get(container_name)
        if last is None:
            return True

        elapsed = datetime.now() - last
        return elapsed >= timedelta(seconds=self.cooldown_seconds)

    def record_alert(self, container_name: str) -> None:
        """Record that an alert was sent."""
        self._last_alert[container_name] = datetime.now()
        self._suppressed_count[container_name] = 0

    def record_suppressed(self, container_name: str) -> None:
        """Record that an alert was suppressed."""
        current = self._suppressed_count.get(container_name, 0)
        self._suppressed_count[container_name] = current + 1

    def get_suppressed_count(self, container_name: str) -> int:
        """Get count of suppressed alerts since last sent alert."""
        return self._suppressed_count.get(container_name, 0)
