from dataclasses import dataclass
from datetime import datetime


@dataclass
class ContainerInfo:
    name: str
    status: str  # running, exited, paused
    health: str | None  # healthy, unhealthy, starting, None
    image: str
    started_at: datetime | None
