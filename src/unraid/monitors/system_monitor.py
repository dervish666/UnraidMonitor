"""Unraid system monitor for CPU, memory, and temperature monitoring."""

import asyncio
import logging
import time
from typing import Any, Callable, Awaitable, TYPE_CHECKING

if TYPE_CHECKING:
    from src.config import UnraidConfig
    from src.unraid.client import UnraidClientWrapper
    from src.alerts.server_mute_manager import ServerMuteManager

logger = logging.getLogger(__name__)

# Minimum seconds between repeated alerts of the same type
_ALERT_COOLDOWN = 300  # 5 minutes

# How long cached metrics remain valid before a fresh fetch is needed
_CACHE_TTL = 30  # seconds


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
        self._last_alert_times: dict[str, float] = {}
        self._cached_metrics: dict[str, Any] | None = None
        self._cached_array: dict[str, Any] | None = None
        self._metrics_cache_time: float = 0.0
        self._array_cache_time: float = 0.0

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
            return None

        self._cached_metrics = metrics
        self._metrics_cache_time = time.monotonic()

        # Check if muted
        if self._mute_manager.is_server_muted():
            logger.debug("Server alerts muted, skipping checks")
            return metrics

        # Check CPU temperature (None when not available from GraphQL schema)
        cpu_temp = metrics.get("cpu_temperature")
        if cpu_temp is not None and cpu_temp > self._config.cpu_temp_threshold:
            await self._rate_limited_alert(
                key="cpu_temp",
                title="High CPU Temperature",
                message=f"Temperature: {cpu_temp:.1f}°C (threshold: {self._config.cpu_temp_threshold}°C)\n"
                        f"Current load: {metrics.get('cpu_percent', 0):.1f}%",
                alert_type="server",
            )

        # Check CPU usage
        cpu_percent = metrics.get("cpu_percent", 0)
        if cpu_percent > self._config.cpu_usage_threshold:
            temp_info = f"\nTemperature: {cpu_temp:.1f}°C" if cpu_temp is not None else ""
            await self._rate_limited_alert(
                key="cpu_usage",
                title="High CPU Usage",
                message=f"Usage: {cpu_percent:.1f}% (threshold: {self._config.cpu_usage_threshold}%){temp_info}",
                alert_type="server",
            )

        # Check memory usage
        memory_percent = metrics.get("memory_percent", 0)
        if memory_percent > self._config.memory_usage_threshold:
            memory_gb = metrics.get("memory_used", 0) / (1024**3)
            await self._rate_limited_alert(
                key="memory",
                title="Memory Critical",
                message=f"Usage: {memory_percent:.1f}% (threshold: {self._config.memory_usage_threshold}%)\n"
                        f"Used: {memory_gb:.1f} GB",
                alert_type="server",
            )

        # Clean stale alert cooldowns
        stale_cutoff = time.monotonic() - (_ALERT_COOLDOWN * 2)
        stale_keys = [k for k, t in self._last_alert_times.items() if t < stale_cutoff]
        for k in stale_keys:
            del self._last_alert_times[k]

        return metrics

    async def _rate_limited_alert(self, key: str, **kwargs: Any) -> None:
        """Send an alert only if cooldown has elapsed for this key."""
        now = time.monotonic()
        last = self._last_alert_times.get(key, 0)
        if now - last < _ALERT_COOLDOWN:
            logger.debug(f"Suppressing duplicate {key} alert (cooldown)")
            return
        self._last_alert_times[key] = now
        await self._on_alert(**kwargs)

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
