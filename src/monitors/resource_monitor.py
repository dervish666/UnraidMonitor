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
