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

    def _get_next_killable(self) -> str | None:
        """Get the next container to kill from the killable list.

        Returns the first running container from killable_containers
        that hasn't already been killed in this pressure event.
        """
        running_names = {c.name for c in self._docker.containers.list()}

        for name in self._config.killable_containers:
            if name in self._killed_containers:
                continue
            if name in running_names:
                return name

        return None

    async def _stop_container(self, name: str) -> None:
        """Stop a container and record it as killed."""
        try:
            container = self._docker.containers.get(name)
            container.stop()
            self._killed_containers.append(name)
            logger.info(f"Stopped container {name} due to memory pressure")
        except docker.errors.NotFound:
            logger.warning(f"Container {name} not found when trying to stop")
        except Exception as e:
            logger.error(f"Failed to stop container {name}: {e}")

    async def _check_memory(self) -> None:
        """Check memory and handle state transitions."""
        percent = self.get_memory_percent()

        if self._state == MemoryState.NORMAL:
            if percent >= self._config.critical_threshold:
                self._state = MemoryState.CRITICAL
                await self._handle_critical(percent)
            elif percent >= self._config.warning_threshold:
                self._state = MemoryState.WARNING
                await self._handle_warning(percent)

        elif self._state == MemoryState.WARNING:
            if percent >= self._config.critical_threshold:
                self._state = MemoryState.CRITICAL
                await self._handle_critical(percent)
            elif percent < self._config.warning_threshold:
                self._state = MemoryState.NORMAL
                logger.info("Memory returned to normal levels")

        elif self._state == MemoryState.CRITICAL:
            if percent < self._config.warning_threshold:
                if self._killed_containers:
                    self._state = MemoryState.RECOVERING
                else:
                    self._state = MemoryState.NORMAL

        elif self._state == MemoryState.RECOVERING:
            if percent <= self._config.safe_threshold and self._killed_containers:
                container = self._killed_containers[0]
                await self._on_ask_restart(container)

    async def _handle_warning(self, percent: float) -> None:
        """Handle warning state - notify user."""
        killable = ", ".join(self._config.killable_containers) or "none configured"
        message = f"Memory at {percent:.0f}%. Killable containers: {killable}"
        await self._on_alert("Memory Warning", message)

    async def _handle_critical(self, percent: float) -> None:
        """Handle critical state - prepare to kill."""
        next_kill = self._get_next_killable()
        if next_kill:
            self._pending_kill = next_kill
            message = (
                f"Memory critical ({percent:.0f}%). "
                f"Will stop {next_kill} in {self._config.kill_delay_seconds} seconds "
                f"to protect priority services. Reply /cancel-kill to abort"
            )
            await self._on_alert("Memory Critical", message)
        else:
            message = f"Memory critical ({percent:.0f}%) but no killable containers available!"
            await self._on_alert("Memory Critical - No Action Available", message)

    async def _execute_kill_countdown(self) -> None:
        """Execute the kill countdown for pending container."""
        if not self._pending_kill:
            return

        container_name = self._pending_kill

        # Wait for kill delay
        await asyncio.sleep(self._config.kill_delay_seconds)

        # Check if cancelled
        if self._kill_cancelled:
            logger.info(f"Kill of {container_name} was cancelled")
            self._kill_cancelled = False
            self._pending_kill = None
            return

        # Check if memory is still critical
        if self.get_memory_percent() >= self._config.critical_threshold:
            await self._stop_container(container_name)
            percent = self.get_memory_percent()
            await self._on_alert(
                "Container Stopped",
                f"Stopped {container_name} to free memory. Memory now at {percent:.0f}%"
            )
        else:
            logger.info(f"Memory recovered, not killing {container_name}")

        self._pending_kill = None

    def cancel_pending_kill(self) -> bool:
        """Cancel a pending kill. Returns True if there was one to cancel."""
        if self._pending_kill:
            self._kill_cancelled = True
            return True
        return False

    def get_pending_kill(self) -> str | None:
        """Get the name of the container pending kill, if any."""
        return self._pending_kill

    async def confirm_restart(self, name: str) -> bool:
        """Confirm restart of a killed container.

        Returns True if container was started successfully.
        """
        if name not in self._killed_containers:
            return False

        try:
            container = self._docker.containers.get(name)
            container.start()
            self._killed_containers.remove(name)
            logger.info(f"Restarted container {name}")

            if not self._killed_containers:
                self._state = MemoryState.NORMAL

            return True
        except Exception as e:
            logger.error(f"Failed to restart container {name}: {e}")
            return False

    async def decline_restart(self, name: str) -> None:
        """Decline restart of a killed container."""
        if name in self._killed_containers:
            self._killed_containers.remove(name)
            logger.info(f"User declined restart of {name}")

        if not self._killed_containers:
            self._state = MemoryState.NORMAL

    def get_killed_containers(self) -> list[str]:
        """Get list of containers killed in this pressure event."""
        return self._killed_containers.copy()
