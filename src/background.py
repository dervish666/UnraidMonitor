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
        self.mute_managers: list[Any] = []
        self._tasks: list[asyncio.Task[Any]] = []

    def add_task(self, task: asyncio.Task[Any]) -> None:
        self._tasks.append(task)

    async def shutdown(self) -> None:
        """Stop all monitors, flush state, and cancel all tasks."""
        if self.monitor:
            await self.monitor.stop_async()
        if self.log_watcher:
            self.log_watcher.stop()
        if self.resource_monitor is not None:
            self.resource_monitor.stop()
        if self.memory_monitor is not None:
            self.memory_monitor.stop()
        if self.image_update_monitor is not None:
            self.image_update_monitor.stop()
        if self.unraid_system_monitor:
            await self.unraid_system_monitor.stop()
        if self.unraid_array_monitor:
            await self.unraid_array_monitor.stop()
        if self.unraid_notification_monitor is not None:
            self.unraid_notification_monitor.stop()
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        if self.unraid_client:
            await self.unraid_client.disconnect()
        # Flush any deferred mute state to disk
        for mgr in self.mute_managers:
            try:
                mgr.flush()
            except Exception as e:
                logger.error(f"Failed to flush mute manager: {e}")
