import threading

from src.models import ContainerInfo


class ContainerStateManager:
    """Thread-safe container state manager.

    This class is accessed from both the Docker event monitoring thread
    and the async event loop, so all access to _containers must be protected.
    """

    def __init__(self):
        self._containers: dict[str, ContainerInfo] = {}
        self._lock = threading.Lock()

    def update(self, info: ContainerInfo) -> None:
        with self._lock:
            self._containers[info.name] = info

    def get(self, name: str) -> ContainerInfo | None:
        with self._lock:
            return self._containers.get(name)

    def get_all(self) -> list[ContainerInfo]:
        with self._lock:
            return list(self._containers.values())

    def find_by_name(self, partial: str) -> list[ContainerInfo]:
        partial_lower = partial.lower()

        with self._lock:
            # Check for exact match first
            for c in self._containers.values():
                if c.name.lower() == partial_lower:
                    return [c]

            # Fall back to substring match
            return [
                c for c in self._containers.values()
                if partial_lower in c.name.lower()
            ]

    def get_summary(self) -> dict[str, int]:
        running = 0
        stopped = 0
        unhealthy = 0

        with self._lock:
            for c in self._containers.values():
                if c.status == "running":
                    running += 1
                else:
                    stopped += 1
                if c.health == "unhealthy":
                    unhealthy += 1

        return {
            "running": running,
            "stopped": stopped,
            "unhealthy": unhealthy,
        }
