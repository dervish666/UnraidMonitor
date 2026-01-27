"""Memory pressure monitor for system-wide memory management."""

import asyncio
import logging
from enum import Enum, auto
from typing import Callable, Awaitable

import docker
import psutil

from src.config import MemoryConfig

logger = logging.getLogger(__name__)


class MemoryState(Enum):
    """Current memory pressure state."""

    NORMAL = auto()
    WARNING = auto()  # Above warning threshold
    CRITICAL = auto()  # Above critical threshold
    KILLING = auto()  # Kill pending (countdown active)
    RECOVERING = auto()  # Killed containers, waiting for safe level


class MemoryMonitor:
    """Monitors system memory and manages container lifecycle under pressure."""

    def __init__(
        self,
        docker_client: docker.DockerClient,
        config: MemoryConfig,
        on_alert: Callable[[str, str], Awaitable[None]],
        on_ask_restart: Callable[[str], Awaitable[None]],
    ):
        """Initialize memory monitor.

        Args:
            docker_client: Docker client for container control.
            config: Memory management configuration.
            on_alert: Callback for sending alerts (title, message).
            on_ask_restart: Callback for asking to restart a container.
        """
        self._docker = docker_client
        self._config = config
        self._on_alert = on_alert
        self._on_ask_restart = on_ask_restart
        self._state = MemoryState.NORMAL
        self._killed_containers: list[str] = []
        self._running = False
        self._pending_kill: str | None = None
        self._kill_cancelled = False

    def is_enabled(self) -> bool:
        """Check if memory monitoring is enabled."""
        return self._config.enabled

    def get_memory_percent(self) -> float:
        """Get current system memory usage percentage."""
        return psutil.virtual_memory().percent
