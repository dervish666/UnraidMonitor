"""Unraid array monitor for disk health and capacity monitoring."""

import asyncio
import logging
from typing import Callable, Awaitable, TYPE_CHECKING

if TYPE_CHECKING:
    from src.config import UnraidConfig
    from src.unraid.client import UnraidClientWrapper
    from src.alerts.array_mute_manager import ArrayMuteManager

logger = logging.getLogger(__name__)


class ArrayMonitor:
    """Monitors Unraid array disks and capacity, triggering alerts on problems."""

    def __init__(
        self,
        client: "UnraidClientWrapper",
        config: "UnraidConfig",
        on_alert: Callable[..., Awaitable[None]],
        mute_manager: "ArrayMuteManager",
    ):
        """Initialize array monitor.

        Args:
            client: Connected UnraidClientWrapper.
            config: Unraid configuration with thresholds.
            on_alert: Async callback for sending alerts.
            mute_manager: Array mute manager.
        """
        self._client = client
        self._config = config
        self._on_alert = on_alert
        self._mute_manager = mute_manager
        self._running = False
        self._task: asyncio.Task | None = None
        self._alerted_disks: set[str] = set()  # Track disks that have been alerted

    async def start(self) -> None:
        """Start the monitoring loop."""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._monitor_loop())
        logger.info("Array monitor started")

    async def stop(self) -> None:
        """Stop the monitoring loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Array monitor stopped")

    async def _monitor_loop(self) -> None:
        """Main monitoring loop."""
        while self._running:
            try:
                await self.check_once()
            except Exception as e:
                logger.error(f"Error in array monitor: {e}")

            await asyncio.sleep(self._config.poll_array_seconds)

    async def check_once(self) -> dict | None:
        """Check array status once and alert if needed.

        Returns:
            The array status dict, or None on error.
        """
        try:
            status = await self._client.get_array_status()
        except Exception as e:
            logger.error(f"Failed to get array status: {e}")
            return None

        # Check if muted
        if self._mute_manager.is_array_muted():
            logger.debug("Array alerts muted, skipping checks")
            return status

        # Check array capacity
        await self._check_capacity(status)

        # Check all disk types
        await self._check_disks(status.get("disks", []), "Data Disk")
        await self._check_disks(status.get("parities", []), "Parity Disk")
        await self._check_disks(status.get("caches", []), "Cache Disk")

        return status

    async def _check_capacity(self, status: dict) -> None:
        """Check array capacity and alert if threshold exceeded.

        Args:
            status: Array status dict.
        """
        capacity = status.get("capacity", {})
        kilobytes = capacity.get("kilobytes", {})

        try:
            used = int(kilobytes.get("used", 0))
            total = int(kilobytes.get("total", 1))  # Avoid division by zero

            if total == 0:
                return

            usage_percent = (used / total) * 100

            if usage_percent > self._config.array_usage_threshold:
                used_tb = used / (1024**3)  # Convert KB to TB
                total_tb = total / (1024**3)
                free_tb = (total - used) / (1024**3)

                await self._on_alert(
                    title="💾 Array Capacity Warning",
                    message=(
                        f"Usage: {usage_percent:.1f}% (threshold: {self._config.array_usage_threshold}%)\n"
                        f"Used: {used_tb:.2f} TB / {total_tb:.2f} TB\n"
                        f"Free: {free_tb:.2f} TB"
                    ),
                    alert_type="array",
                )
        except (ValueError, TypeError) as e:
            logger.warning(f"Failed to parse capacity: {e}")

    async def _check_disks(self, disks: list[dict], disk_type: str) -> None:
        """Check disk temperatures and status.

        Args:
            disks: List of disk dicts.
            disk_type: Type of disk (e.g., "Data Disk", "Parity Disk").
        """
        for disk in disks:
            disk_name = disk.get("name", "Unknown")
            disk_key = f"{disk_type}:{disk_name}"

            # Check temperature
            temp = disk.get("temp")
            if temp is not None:
                try:
                    temp_value = int(temp)
                    if temp_value > self._config.disk_temp_threshold:
                        # Only alert if we haven't already alerted for this disk
                        if disk_key not in self._alerted_disks:
                            await self._on_alert(
                                title=f"💾 {disk_type} High Temperature",
                                message=(
                                    f"Disk: {disk_name}\n"
                                    f"Temperature: {temp_value}°C (threshold: {self._config.disk_temp_threshold}°C)"
                                ),
                                alert_type="array",
                            )
                            self._alerted_disks.add(disk_key)
                except (ValueError, TypeError):
                    logger.warning(f"Invalid temperature for {disk_name}: {temp}")

            # Check disk status
            status = disk.get("status", "")
            if status and status != "DISK_OK":
                # Only alert if we haven't already alerted for this disk
                status_key = f"{disk_key}:status"
                if status_key not in self._alerted_disks:
                    await self._on_alert(
                        title=f"💾 {disk_type} Problem",
                        message=(
                            f"Disk: {disk_name}\n"
                            f"Status: {status}\n"
                            f"Expected: DISK_OK"
                        ),
                        alert_type="array",
                    )
                    self._alerted_disks.add(status_key)

    def clear_alert_state(self) -> None:
        """Clear the alerted disks tracking.

        Should be called when array is unmuted to allow re-alerting.
        """
        self._alerted_disks.clear()
        logger.debug("Cleared array alert state")
