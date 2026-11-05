"""Unraid system monitor for CPU, memory, and temperature monitoring."""

import asyncio
import logging
import time
from typing import Any, Callable, Awaitable, TYPE_CHECKING

from src.constants import ALERT_REMINDER_SECONDS, EPISODE_RECOVERY_RATIO

if TYPE_CHECKING:
    from src.config import UnraidConfig
    from src.unraid.client import UnraidClientWrapper
    from src.alerts.server_mute_manager import ServerMuteManager
    from src.unraid.monitors.availability import UnraidAvailability

logger = logging.getLogger(__name__)

# Consecutive over-threshold polls before a CPU usage alert: one 30s sample is
# a compile or a scan, two in a row is load worth hearing about.
_CPU_SUSTAINED_POLLS = 2

# How long cached metrics remain valid before a fresh fetch is needed
_CACHE_TTL = 30  # seconds


class UnraidSystemMonitor:
    """Monitors Unraid system metrics and triggers alerts."""

    def __init__(
        self,
        client: "UnraidClientWrapper",
        config: "UnraidConfig",
        on_alert: Callable[..., Awaitable[bool]],
        mute_manager: "ServerMuteManager",
        availability: "UnraidAvailability | None" = None,
        memory_alerts: bool = True,
    ):
        """Initialize system monitor.

        Args:
            client: Connected UnraidClientWrapper.
            config: Unraid configuration with thresholds.
            on_alert: Async callback for sending alerts.
            mute_manager: Server mute manager.
            availability: Shared API-availability ladder (also fed by the
                array monitor), or None to skip outage alerts.
            memory_alerts: False when memory_management is on, so the
                MemoryMonitor is the only voice on memory pressure.
        """
        self._client = client
        self._config = config
        self._on_alert = on_alert
        self._mute_manager = mute_manager
        self._availability = availability
        self._running = False
        self._memory_alerts = memory_alerts
        # Open episodes: key -> monotonic time of the last delivered alert,
        # and when the episode began (for the reminder text).
        self._episodes: dict[str, float] = {}
        self._episode_started: dict[str, float] = {}
        self._cpu_over_polls = 0
        self._cached_metrics: dict[str, Any] | None = None
        self._cached_array: dict[str, Any] | None = None
        self._metrics_cache_time: float = 0.0
        self._array_cache_time: float = 0.0

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def availability(self) -> "UnraidAvailability | None":
        return self._availability

    async def start(self) -> None:
        """Start the monitoring loop.

        Runs as a long-lived coroutine — wrap in asyncio.create_task() from main.
        """
        if self._running:
            return

        self._running = True
        logger.info("Unraid system monitor started")

        while self._running:
            try:
                await self.check_once()
            except Exception as e:
                logger.error(f"Error in system monitor: {e}")

            await asyncio.sleep(self._config.poll_system_seconds)

    async def stop(self) -> None:
        """Stop the monitoring loop."""
        self._running = False
        logger.info("Unraid system monitor stopped")

    async def check_once(self) -> dict[str, Any] | None:
        """Check system metrics once and alert if needed.

        Returns:
            The metrics dict, or None on error.
        """
        try:
            metrics = await self._client.get_system_metrics()
        except Exception as e:
            logger.error(f"Failed to get system metrics: {e}")
            if self._availability is not None:
                await self._availability.record_failure(e)
            return None

        if self._availability is not None:
            await self._availability.record_success()
        self._cached_metrics = metrics
        self._metrics_cache_time = time.monotonic()

        # Check if muted
        if self._mute_manager.is_server_muted():
            logger.debug("Server alerts muted, skipping checks")
            return metrics

        # Check CPU temperature (None when not available from GraphQL schema)
        cpu_temp = metrics.get("cpu_temperature")
        if cpu_temp is not None:
            threshold = self._config.cpu_temp_threshold
            if cpu_temp > threshold:
                await self._episode_alert(
                    key="cpu_temp",
                    title="High CPU Temperature",
                    message=f"Temperature: {cpu_temp:.1f}°C (threshold: {threshold}°C)\n"
                            f"Current load: {metrics.get('cpu_percent', 0):.1f}%",
                )
            elif cpu_temp <= threshold * EPISODE_RECOVERY_RATIO:
                await self._episode_recover(
                    "cpu_temp", "CPU Temp Normal", f"CPU at {cpu_temp:.1f}°C (threshold {threshold}°C)."
                )

        # Check CPU usage
        cpu_percent = metrics.get("cpu_percent", 0)
        cpu_threshold = self._config.cpu_usage_threshold
        if cpu_percent > cpu_threshold:
            self._cpu_over_polls += 1
        else:
            self._cpu_over_polls = 0
        if self._cpu_over_polls >= _CPU_SUSTAINED_POLLS:
            temp_info = f"\nTemperature: {cpu_temp:.1f}°C" if cpu_temp is not None else ""
            await self._episode_alert(
                key="cpu_usage",
                title="High CPU Usage",
                message=f"Usage: {cpu_percent:.1f}% (threshold: {cpu_threshold}%){temp_info}",
            )
        elif cpu_percent <= cpu_threshold * EPISODE_RECOVERY_RATIO:
            await self._episode_recover(
                "cpu_usage", "CPU Load Normal", f"CPU at {cpu_percent:.1f}% (threshold {cpu_threshold}%)."
            )

        # Check memory usage. With memory management on, MemoryMonitor owns
        # memory pressure (its Warning fires at the same 90% this "Critical"
        # did, so the user got both with contradictory severity).
        memory_percent = metrics.get("memory_percent", 0)
        mem_threshold = self._config.memory_usage_threshold
        if self._memory_alerts and memory_percent > mem_threshold:
            memory_gb = metrics.get("memory_used", 0) / (1024**3)
            memory_total_gb = metrics.get("memory_total", 0) / (1024**3)
            used_line = (
                f"Used: {memory_gb:.1f} of {memory_total_gb:.1f} GB"
                if memory_total_gb else f"Used: {memory_gb:.1f} GB"
            )
            await self._episode_alert(
                key="memory",
                title="Memory Critical",
                message=f"Usage: {memory_percent:.1f}% (threshold: {mem_threshold}%)\n"
                        f"{used_line}",
            )
        elif self._memory_alerts and memory_percent <= mem_threshold * EPISODE_RECOVERY_RATIO:
            await self._episode_recover(
                "memory", "Memory Normal", f"Memory at {memory_percent:.1f}% (threshold {mem_threshold}%)."
            )

        return metrics

    async def _episode_alert(self, key: str, title: str, message: str) -> None:
        """Alert once per episode, then remind every ALERT_REMINDER_SECONDS.

        The episode (and its timer) starts only on delivery: a send lost to a
        Telegram outage is retried next poll.
        """
        now = time.monotonic()
        last = self._episodes.get(key)
        if last is not None and now - last < ALERT_REMINDER_SECONDS:
            return
        if last is not None:
            message += f"\n\nStill going after {(now - self._episode_started[key]) / 3600:.0f}h."
        if await self._on_alert(title=title, message=message, alert_type="server"):
            self._episodes[key] = now
            self._episode_started.setdefault(key, now)

    async def _episode_recover(self, key: str, title: str, message: str) -> None:
        """Close an open episode with one "back to normal" message."""
        if key not in self._episodes:
            return
        if await self._on_alert(title=title, message=message, alert_type="recovery"):
            del self._episodes[key]
            self._episode_started.pop(key, None)

    async def get_current_metrics(self) -> dict[str, Any] | None:
        """Get current metrics, using cache if fresh.

        Returns:
            Metrics dict or None on error.
        """
        if self._cached_metrics is not None and (time.monotonic() - self._metrics_cache_time) < _CACHE_TTL:
            return self._cached_metrics
        try:
            metrics = await self._client.get_system_metrics()
            self._cached_metrics = metrics
            self._metrics_cache_time = time.monotonic()
            return metrics
        except Exception as e:
            logger.error(f"Failed to get system metrics: {e}")
            return None

    async def get_array_status(self) -> dict[str, Any] | None:
        """Get array status, using cache if fresh.

        Returns:
            Array status dict or None on error.
        """
        if self._cached_array is not None and (time.monotonic() - self._array_cache_time) < _CACHE_TTL:
            return self._cached_array
        try:
            result = await self._client.get_array_status()
            self._cached_array = result
            self._array_cache_time = time.monotonic()
            return result
        except Exception as e:
            logger.error(f"Failed to get array status: {e}")
            return None
