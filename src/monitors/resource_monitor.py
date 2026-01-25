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
