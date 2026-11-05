from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.monitors.docker_events import DockerEventMonitor
from src.monitors.image_update_monitor import ImageUpdateMonitor
from src.monitors.log_watcher import LogWatcher
from src.monitors.memory_monitor import MemoryMonitor
from src.monitors.resource_monitor import ResourceMonitor
from src.unraid.client import UnraidClientWrapper
from src.unraid.monitors.system_monitor import UnraidSystemMonitor
from src.unraid.monitors.array_monitor import ArrayMonitor
from src.unraid.monitors.notification_monitor import UnraidNotificationMonitor
from src.nut.monitor import UpsMonitor


logger = logging.getLogger(__name__)


class _BackgroundTasks:
    """Holds references to all background tasks and stoppable components."""

    def __init__(self) -> None:
        self.monitor: DockerEventMonitor | None = None
        self.log_watcher: LogWatcher | None = None
        self.resource_monitor: ResourceMonitor | None = None
        self.memory_monitor: MemoryMonitor | None = None
        self.image_update_monitor: ImageUpdateMonitor | None = None
        self.unraid_client: UnraidClientWrapper | None = None
        self.unraid_system_monitor: UnraidSystemMonitor | None = None
        self.unraid_array_monitor: ArrayMonitor | None = None
        self.unraid_notification_monitor: UnraidNotificationMonitor | None = None
        self.ups_monitor: UpsMonitor | None = None
        self.mute_managers: list[Any] = []
        self._tasks: list[asyncio.Task[Any]] = []
        self._monitor_tasks: list[asyncio.Task[Any]] = []
        self._shutting_down = False
        self.dead_tasks: list[str] = []

    def add_task(self, task: asyncio.Task[Any], monitor: bool = True) -> None:
        """Track a background task; *monitor* tasks count toward liveness.

        A task that dies with an exception is logged at ERROR with its name:
        before, a crashed monitor vanished silently and the bot looked healthy
        while watching nothing. A clean return (a disabled monitor) is not death.
        """
        self._tasks.append(task)
        if monitor:
            self._monitor_tasks.append(task)
        task.add_done_callback(self._on_task_done)

    def _on_task_done(self, task: asyncio.Task[Any]) -> None:
        if self._shutting_down or task.cancelled():
            return
        name = task.get_name()
        exc = task.exception()
        if exc is None:
            logger.debug(f"Background task {name} finished")
            return
        logger.error(f"Background task {name} died: {type(exc).__name__}: {exc}", exc_info=exc)
        self.dead_tasks.append(name)

    def any_monitor_alive(self) -> bool:
        """False once every monitoring task has ended and at least one crashed.

        The heartbeat stops then, so the Docker HEALTHCHECK fails and the
        container is restarted instead of idling as "healthy". With no
        monitors registered yet (setup wizard, startup), or with every monitor
        disabled and returned cleanly, it is True: a restart would change nothing.
        """
        if any(not t.done() for t in self._monitor_tasks):
            return True
        return not any(
            not t.cancelled() and t.exception() is not None for t in self._monitor_tasks
        )

    async def shutdown(self) -> None:
        """Stop all monitors, flush state, and cancel all tasks.

        Each step is isolated: a monitor that already died, or fails to stop,
        must not skip the mute flush at the end.
        """
        self._shutting_down = True
        try:
            for label, stop in [
                ("docker events", self.monitor.stop_async if self.monitor else None),
                ("log watcher", self.log_watcher.stop if self.log_watcher else None),
                ("resource monitor", self.resource_monitor.stop if self.resource_monitor else None),
                ("memory monitor", self.memory_monitor.stop if self.memory_monitor else None),
                ("image updates", self.image_update_monitor.stop if self.image_update_monitor else None),
                ("unraid system", self.unraid_system_monitor.stop if self.unraid_system_monitor else None),
                ("unraid array", self.unraid_array_monitor.stop if self.unraid_array_monitor else None),
                ("unraid notifications",
                 self.unraid_notification_monitor.stop if self.unraid_notification_monitor else None),
                ("ups", self.ups_monitor.stop if self.ups_monitor else None),
            ]:
                if stop is None:
                    continue
                try:
                    result = stop()
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as e:
                    logger.error(f"Failed to stop {label}: {e}")
            for task in self._tasks:
                task.cancel()
            for task in self._tasks:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.debug(f"Task {task.get_name()} had already failed: {e}")
            if self.unraid_client:
                try:
                    await self.unraid_client.disconnect()
                except Exception as e:
                    logger.error(f"Failed to disconnect Unraid client: {e}")
        finally:
            # Flush any deferred mute state to disk
            for mgr in self.mute_managers:
                try:
                    mgr.flush()
                except Exception as e:
                    logger.error(f"Failed to flush mute manager: {e}")
