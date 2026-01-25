from dataclasses import dataclass


@dataclass
class ContainerStats:
    """Resource statistics for a container."""

    name: str
    cpu_percent: float
    memory_percent: float
    memory_bytes: int
    memory_limit: int

    @property
    def memory_display(self) -> str:
        """Format memory usage for display."""
        return self._format_bytes(self.memory_bytes)

    @property
    def memory_limit_display(self) -> str:
        """Format memory limit for display."""
        return self._format_bytes(self.memory_limit)

    @staticmethod
    def _format_bytes(bytes_val: int) -> str:
        """Format bytes as human-readable string."""
        gb = bytes_val / (1024**3)
        if gb >= 1.0:
            return f"{gb:.1f}GB"
        mb = bytes_val / (1024**2)
        return f"{mb:.0f}MB"


def calculate_cpu_percent(stats: dict) -> float:
    """Calculate CPU percentage from Docker stats.

    Docker provides cumulative CPU usage, so we need to calculate
    the delta between current and previous readings.

    Args:
        stats: Docker stats response dict.

    Returns:
        CPU usage as percentage (0-100 per core, can exceed 100 on multi-core).
    """
    cpu_stats = stats.get("cpu_stats", {})
    precpu_stats = stats.get("precpu_stats", {})

    cpu_usage = cpu_stats.get("cpu_usage", {}).get("total_usage", 0)
    precpu_usage = precpu_stats.get("cpu_usage", {}).get("total_usage", 0)

    system_usage = cpu_stats.get("system_cpu_usage", 0)
    presystem_usage = precpu_stats.get("system_cpu_usage", 0)

    cpu_delta = cpu_usage - precpu_usage
    system_delta = system_usage - presystem_usage

    if system_delta > 0 and cpu_delta >= 0:
        num_cpus = cpu_stats.get("online_cpus", 1)
        return (cpu_delta / system_delta) * num_cpus * 100.0

    return 0.0


def parse_container_stats(name: str, stats: dict) -> ContainerStats:
    """Parse Docker stats response into ContainerStats.

    Args:
        name: Container name.
        stats: Docker stats response dict.

    Returns:
        ContainerStats with parsed values.
    """
    cpu_percent = calculate_cpu_percent(stats)

    memory_stats = stats.get("memory_stats", {})
    memory_usage = memory_stats.get("usage", 0)
    memory_limit = memory_stats.get("limit", 1)  # Avoid division by zero

    # Subtract cache from memory usage if available
    cache = memory_stats.get("stats", {}).get("cache", 0)
    memory_usage = memory_usage - cache

    memory_percent = (memory_usage / memory_limit) * 100.0 if memory_limit > 0 else 0.0

    return ContainerStats(
        name=name,
        cpu_percent=round(cpu_percent, 1),
        memory_percent=round(memory_percent, 1),
        memory_bytes=memory_usage,
        memory_limit=memory_limit,
    )
