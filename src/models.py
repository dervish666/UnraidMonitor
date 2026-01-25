from dataclasses import dataclass
from datetime import datetime


@dataclass
class ContainerInfo:
    name: str
    status: str  # running, exited, paused
    health: str | None  # healthy, unhealthy, starting, None
    image: str
    started_at: datetime | None

    @property
    def uptime_seconds(self) -> int | None:
        """Calculate container uptime in seconds."""
        if self.started_at is None:
            return None
        elapsed = datetime.now(self.started_at.tzinfo) - self.started_at
        return int(elapsed.total_seconds())
