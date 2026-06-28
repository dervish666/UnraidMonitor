"""Memory pressure monitor for system-wide memory management."""

import asyncio
import logging
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Callable, Awaitable

import docker
import psutil

from src.config import MemoryConfig
from src.monitors.resource_monitor import parse_container_stats
from src.utils.formatting import format_bytes

logger = logging.getLogger(__name__)

# (name, memory_bytes | None) for each killable container offered in an alert.
# memory_bytes is None when its usage could not be read in time.
KillableEntry = tuple[str, "int | None"]


class MemoryState(Enum):
    """Current memory pressure state."""

    NORMAL = auto()
    WARNING = auto()  # Above warning threshold
    CRITICAL = auto()  # Above critical threshold
    KILLING = auto()  # Kill pending (countdown active)
    RECOVERING = auto()  # Killed containers, waiting for safe level


@dataclass
class KillResult:
    """Outcome of stopping a container, with memory context for the user."""

    success: bool
    name: str
    freed_bytes: int | None = None  # container memory just before it was stopped
    system_percent: float | None = None  # system memory % after the stop
    system_available_bytes: int | None = None  # system memory free after the stop


class MemoryMonitor:
    """Monitors system memory and manages container lifecycle under pressure."""

    def __init__(
        self,
        docker_client: docker.DockerClient,
        config: MemoryConfig,
        on_alert: Callable[[str, str, str, list[KillableEntry]], Awaitable[None]],
        on_ask_restart: Callable[[str], Awaitable[None]],
        check_interval: int = 10,
        error_sleep: int = 30,
        stats_timeout: float = 8.0,
    ):
        """Initialize memory monitor.

        Args:
            docker_client: Docker client for container control.
            config: Memory management configuration.
            on_alert: Callback for sending alerts (title, message, alert_type, killable).
                alert_type is "warning", "critical", or "info".
                killable is a list of (name, memory_bytes | None) tuples for the
                containers relevant to kill buttons.
            on_ask_restart: Callback for asking to restart a container.
            check_interval: Seconds between memory checks.
            error_sleep: Seconds to sleep after an error.
            stats_timeout: Max seconds to spend reading killable container memory
                before falling back to showing names without memory figures.
        """
        self._docker = docker_client
        self._config = config
        self._on_alert = on_alert
        self._on_ask_restart = on_ask_restart
        self._check_interval = check_interval
        self._error_sleep = error_sleep
        self._stats_timeout = stats_timeout
        self._state = MemoryState.NORMAL
        self._killed_containers: list[str] = []
        self._running = False
        self._pending_kill: str | None = None
        self._kill_cancel_event: asyncio.Event | None = None
        self._restart_prompted = False
        self._kill_lock = asyncio.Lock()

    @property
    def is_running(self) -> bool:
        return self._running

    def is_enabled(self) -> bool:
        """Check if memory monitoring is enabled."""
        return self._config.enabled

    def get_memory_percent(self) -> float:
        """Get current system memory usage percentage."""
        return float(psutil.virtual_memory().percent)

    async def _get_running_killable(self) -> list[str]:
        """Return killable containers that are currently running.

        Preserves the configured priority order (lowest priority first)
        and excludes any already stopped in this pressure event.
        """
        running_names = await asyncio.to_thread(
            lambda: {c.name for c in self._docker.containers.list()}
        )
        return [
            name
            for name in self._config.killable_containers
            if name in running_names and name not in self._killed_containers
        ]

    async def _get_next_killable(self) -> str | None:
        """Get the next container to kill from the killable list.

        Returns the first running container from killable_containers
        that hasn't already been killed in this pressure event.
        """
        running_killable = await self._get_running_killable()
        return running_killable[0] if running_killable else None

    async def _container_memory_bytes(self, name: str) -> int | None:
        """Current cache-adjusted memory usage (bytes) for a running container.

        Returns None if the container isn't running or its stats can't be read.
        Reuses the resource monitor's parser so the figure matches /resources.
        """
        try:
            def _get_stats() -> dict[str, Any] | None:
                container = self._docker.containers.get(name)
                if container.status != "running":
                    return None
                return container.stats(stream=False)  # type: ignore[return-value]

            raw_stats = await asyncio.to_thread(_get_stats)
            if raw_stats is None:
                return None
            return parse_container_stats(name, raw_stats).memory_bytes
        except docker.errors.NotFound:
            return None
        except Exception as e:
            logger.debug(f"Could not read memory for {name}: {e}")
            return None

    async def get_killable_memory(self) -> list[KillableEntry]:
        """Running killable containers paired with their current memory usage.

        Only the killable containers are queried (not every running container),
        and the whole batch is bounded by ``stats_timeout`` -- if Docker is slow
        under pressure we fall back to names with no memory figure rather than
        delay the alert.
        """
        names = await self._get_running_killable()
        if not names:
            return []
        try:
            mems = await asyncio.wait_for(
                asyncio.gather(*(self._container_memory_bytes(n) for n in names)),
                timeout=self._stats_timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("Timed out reading killable container memory; showing names only")
            return [(name, None) for name in names]
        return list(zip(names, mems))

    def _system_memory(self) -> tuple[float, int]:
        """Current system memory as (percent_used, available_bytes)."""
        vm = psutil.virtual_memory()
        return float(vm.percent), int(vm.available)

    async def _stop_container(self, name: str) -> int | None:
        """Stop a container and record it as killed.

        Returns the container's memory usage (bytes) just before it was stopped,
        or None if it couldn't be read or the stop failed.
        """
        freed = await self._container_memory_bytes(name)
        try:
            def _do_stop() -> None:
                container = self._docker.containers.get(name)
                container.stop(timeout=10)

            await asyncio.to_thread(_do_stop)
            self._killed_containers.append(name)
            logger.info(f"Stopped container {name} due to memory pressure")
            return freed
        except docker.errors.NotFound:
            logger.warning(f"Container {name} not found when trying to stop")
            return None
        except Exception as e:
            logger.error(f"Failed to stop container {name}: {e}")
            return None

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
                self._killed_containers.clear()
                self._restart_prompted = False
                logger.info("Memory returned to normal levels")

        elif self._state == MemoryState.CRITICAL:
            if percent < self._config.warning_threshold:
                if self._killed_containers:
                    self._state = MemoryState.RECOVERING
                else:
                    self._state = MemoryState.NORMAL

        elif self._state == MemoryState.RECOVERING:
            if percent <= self._config.safe_threshold and self._killed_containers and not self._restart_prompted:
                container = self._killed_containers[0]
                self._restart_prompted = True
                await self._on_ask_restart(container)

    async def _handle_warning(self, percent: float) -> None:
        """Handle warning state - notify user.

        Only lists/offers containers that are killable *and* currently
        running -- stopped containers can't free any memory -- and annotates
        each with how much memory it is currently using.
        """
        killable = await self.get_killable_memory()
        listed = ", ".join(
            name + (f" ({format_bytes(mem)})" if mem else "") for name, mem in killable
        ) or "none running"
        message = f"Memory at {percent:.0f}%. Killable containers: {listed}"
        await self._on_alert("Memory Warning", message, "warning", killable)

    async def _handle_critical(self, percent: float) -> None:
        """Handle critical state - prepare to kill."""
        next_kill = await self._get_next_killable()
        if next_kill:
            self._pending_kill = next_kill
            mem = await self._container_memory_bytes(next_kill)
            using = f" (using {format_bytes(mem)})" if mem else ""
            message = (
                f"Memory critical ({percent:.0f}%). "
                f"Will stop {next_kill}{using} in {self._config.kill_delay_seconds} seconds "
                f"to protect priority services."
            )
            await self._on_alert("Memory Critical", message, "critical", [(next_kill, mem)])
        else:
            message = f"Memory critical ({percent:.0f}%) but no killable containers available!"
            await self._on_alert("Memory Critical - No Action Available", message, "critical", [])

    async def _execute_kill_countdown(self) -> None:
        """Execute the kill countdown for pending container."""
        async with self._kill_lock:
            if not self._pending_kill:
                return
            container_name = self._pending_kill
            self._kill_cancel_event = asyncio.Event()
            cancel_event = self._kill_cancel_event

        # Wait outside the lock so cancel_pending_kill can acquire it
        try:
            await asyncio.wait_for(
                cancel_event.wait(),
                timeout=self._config.kill_delay_seconds
            )
            async with self._kill_lock:
                logger.info(f"Kill of {container_name} was cancelled")
                self._pending_kill = None
                self._kill_cancel_event = None
            return
        except asyncio.TimeoutError:
            pass

        async with self._kill_lock:
            if self._pending_kill != container_name:
                logger.info(f"Pending kill changed, aborting kill of {container_name}")
                self._kill_cancel_event = None
                return

            if cancel_event.is_set():
                logger.info(f"Kill of {container_name} was cancelled (late)")
                self._pending_kill = None
                self._kill_cancel_event = None
                return

            percent = self.get_memory_percent()
            if percent >= self._config.critical_threshold:
                freed = await self._stop_container(container_name)
                now_percent, available = self._system_memory()
                freed_note = f" It was using ~{format_bytes(freed)}." if freed else ""
                await self._on_alert(
                    "Container Stopped",
                    f"Stopped {container_name} to free memory.{freed_note} "
                    f"System memory now {now_percent:.0f}% ({format_bytes(available)} free).",
                    "info",
                    [],
                )
            else:
                logger.info(f"Memory recovered, not killing {container_name}")

            self._pending_kill = None
            self._kill_cancel_event = None

    async def cancel_pending_kill(self) -> bool:
        """Cancel a pending kill. Returns True if there was one to cancel."""
        async with self._kill_lock:
            if self._pending_kill and self._kill_cancel_event:
                self._kill_cancel_event.set()
                return True
            return False

    async def kill_container(self, name: str) -> KillResult:
        """Kill a container immediately (from button press).

        Cancels any pending auto-kill first, then stops the named container.
        Returns a KillResult carrying how much memory the container was using
        and the system memory level after the stop, so the user gets feedback.
        """
        # Cancel any pending auto-kill to avoid double-stopping
        await self.cancel_pending_kill()

        # Capture usage before stopping -- afterwards the container is gone.
        freed = await self._container_memory_bytes(name)

        try:
            def _do_kill() -> None:
                container = self._docker.containers.get(name)
                container.stop(timeout=10)

            await asyncio.to_thread(_do_kill)
            if name not in self._killed_containers:
                self._killed_containers.append(name)
            logger.info(f"Stopped container {name} via kill button")
            percent, available = self._system_memory()
            return KillResult(True, name, freed, percent, available)
        except docker.errors.NotFound:
            logger.warning(f"Container {name} not found when trying to kill")
            return KillResult(False, name)
        except Exception as e:
            logger.error(f"Failed to kill container {name}: {e}")
            return KillResult(False, name)

    def get_pending_kill(self) -> str | None:
        """Get the name of the container pending kill, if any."""
        return self._pending_kill

    async def confirm_restart(self, name: str) -> bool:
        """Confirm restart of a killed container.

        Returns True if container was started successfully.
        """
        async with self._kill_lock:
            if name not in self._killed_containers:
                return False

        try:
            def _do_start() -> None:
                container = self._docker.containers.get(name)
                container.start()

            await asyncio.to_thread(_do_start)

            async with self._kill_lock:
                self._killed_containers.remove(name)
                self._restart_prompted = False
                logger.info(f"Restarted container {name}")
                if not self._killed_containers:
                    self._state = MemoryState.NORMAL

            return True
        except Exception as e:
            logger.error(f"Failed to restart container {name}: {e}")
            return False

    async def decline_restart(self, name: str) -> None:
        """Decline restart of a killed container."""
        async with self._kill_lock:
            if name in self._killed_containers:
                self._killed_containers.remove(name)
                logger.info(f"User declined restart of {name}")

            if not self._killed_containers:
                self._restart_prompted = False
                self._state = MemoryState.NORMAL

    def get_killed_containers(self) -> list[str]:
        """Get list of containers killed in this pressure event."""
        return self._killed_containers.copy()

    async def start(self) -> None:
        """Start the memory monitoring loop."""
        if not self.is_enabled():
            logger.info("Memory monitoring disabled")
            return

        self._running = True
        logger.info("Memory monitor started")

        while self._running:
            try:
                await self._check_memory()

                # Handle kill countdown if in critical state with pending kill
                if self._state == MemoryState.CRITICAL and self._pending_kill:
                    await self._execute_kill_countdown()

                    # After kill, wait for stabilization
                    if self._killed_containers:
                        self._state = MemoryState.RECOVERING
                        await asyncio.sleep(self._config.stabilization_wait)
                        continue

                await asyncio.sleep(self._check_interval)

            except asyncio.CancelledError:
                logger.info("Memory monitor cancelled")
                raise
            except Exception as e:
                logger.error(f"Error in memory monitor: {e}")
                await asyncio.sleep(self._error_sleep)

    def stop(self) -> None:
        """Stop the memory monitoring loop."""
        self._running = False
        logger.info("Memory monitor stopped")
