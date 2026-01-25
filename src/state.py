from src.models import ContainerInfo


class ContainerStateManager:
    def __init__(self):
        self._containers: dict[str, ContainerInfo] = {}

    def update(self, info: ContainerInfo) -> None:
        self._containers[info.name] = info

    def get(self, name: str) -> ContainerInfo | None:
        return self._containers.get(name)

    def get_all(self) -> list[ContainerInfo]:
        return list(self._containers.values())

    def find_by_name(self, partial: str) -> list[ContainerInfo]:
        partial_lower = partial.lower()
        return [
            c for c in self._containers.values()
            if partial_lower in c.name.lower()
        ]

    def get_summary(self) -> dict[str, int]:
        running = 0
        stopped = 0
        unhealthy = 0

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
