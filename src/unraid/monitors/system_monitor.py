"""Unraid system monitor for CPU, memory, and temperature monitoring."""

import asyncio
import logging
from typing import Callable, Awaitable, TYPE_CHECKING

if TYPE_CHECKING:
    from src.config import UnraidConfig
    from src.unraid.client import UnraidClientWrapper
    from src.alerts.server_mute_manager import ServerMuteManager

logger = logging.getLogger(__name__)


class UnraidSystemMonitor:
    """Monitors Unraid system metrics and triggers alerts."""

    def __init__(
        self,
        client: "UnraidClientWrapper",
        config: "UnraidConfig",
        on_alert: Callable[..., Awaitable[None]],
        mute_manager: "ServerMuteManager",
    ):
        """Initialize system monitor.

        Args:
            client: Connected UnraidClientWrapper.
            config: Unraid configuration with thresholds.
            on_alert: Async callback for sending alerts.
            mute_manager: Server mute manager.
        """
        self._client = client
        self._config = config
        self._on_alert = on_alert
        self._mute_manager = mute_manager
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the monitoring loop."""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._monitor_loop())
        logger.info("Unraid system monitor started")

    async def stop(self) -> None:
        """Stop the monitoring loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Unraid system monitor stopped")

    async def _monitor_loop(self) -> None:
        """Main monitoring loop."""
        while self._running:
            try:
                await self.check_once()
            except Exception as e:
                logger.error(f"Error in system monitor: {e}")

            await asyncio.sleep(self._config.poll_system_seconds)

    async def check_once(self) -> dict | None:
        """Check system metrics once and alert if needed.

        Returns:
            The metrics dict, or None on error.
        """
        try:
            metrics = await self._client.get_system_metrics()
        except Exception as e:
            logger.error(f"Failed to get system metrics: {e}")
            return None

        # Check if muted
        if self._mute_manager.is_server_muted():
            logger.debug("Server alerts muted, skipping checks")
            return metrics

        # Check CPU temperature
        cpu_temp = metrics.get("cpu_temperature", 0)
        if cpu_temp and cpu_temp > self._config.cpu_temp_threshold:
            await self._on_alert(
                title="High CPU Temperature",
                message=f"Temperature: {cpu_temp:.1f}°C (threshold: {self._config.cpu_temp_threshold}°C)\n"
                        f"Current load: {metrics.get('cpu_percent', 0):.1f}%",
                alert_type="server",
            )

        # Check CPU usage
        cpu_percent = metrics.get("cpu_percent", 0)
        if cpu_percent > self._config.cpu_usage_threshold:
            await self._on_alert(
                title="High CPU Usage",
                message=f"Usage: {cpu_percent:.1f}% (threshold: {self._config.cpu_usage_threshold}%)\n"
                        f"Temperature: {cpu_temp:.1f}°C",
                alert_type="server",
            )

        # Check memory usage
        memory_percent = metrics.get("memory_percent", 0)
        if memory_percent > self._config.memory_usage_threshold:
            memory_gb = metrics.get("memory_used", 0) / (1024**3)
            await self._on_alert(
                title="Memory Critical",
                message=f"Usage: {memory_percent:.1f}% (threshold: {self._config.memory_usage_threshold}%)\n"
                        f"Used: {memory_gb:.1f} GB",
                alert_type="server",
            )

        return metrics

    async def get_current_metrics(self) -> dict | None:
        """Get current metrics without alerting (for commands).

        Returns:
            Metrics dict or None on error.
        """
        try:
            return await self._client.get_system_metrics()
        except Exception as e:
            logger.error(f"Failed to get system metrics: {e}")
            return None

    async def get_array_status(self) -> dict | None:
        """Get array status (for commands).

        Returns:
            Array status dict or None on error.
        """
        try:
            return await self._client.get_array_status()
        except Exception as e:
            logger.error(f"Failed to get array status: {e}")
            return None
