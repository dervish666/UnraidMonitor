"""Unraid array monitor for disk health and capacity monitoring."""

import asyncio
import logging
from typing import Any, Callable, Awaitable, TYPE_CHECKING

if TYPE_CHECKING:
    from src.config import UnraidConfig
    from src.unraid.client import UnraidClientWrapper
    from src.alerts.array_mute_manager import ArrayMuteManager

logger = logging.getLogger(__name__)

# A parity sync or disk rebuild is *writing* to its target, so the array reports
# the target as invalid/new until it finishes. There is no per-disk "being
# rebuilt" flag in the API -- a running parity operation is the only signal that
# these are expected rather than a fault. Every other status (DISK_DSBL,
# DISK_WRONG, DISK_NP_MISSING...) still alerts, sync or no sync.
_REBUILD_EXPECTED_STATUSES = {"DISK_INVALID", "DISK_NEW"}

# Measured against a live server mid-sync on 2026-08-02: `running`, `paused` and
# `errors` all came back null while `status` correctly read RUNNING at 45%.
# Trust `status`; treat the booleans as absent.
_PARITY_ACTIVE_STATUSES = {"RUNNING", "PAUSED"}


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
        self._task: asyncio.Task[None] | None = None
        self._alerted_disks: set[str] = set()  # Track disks that have been alerted
        self._parity_active = False  # Whether a parity op was running last poll

    @property
    def is_running(self) -> bool:
        return self._running

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

    async def check_once(self) -> dict[str, Any] | None:
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

        # Resolve this first: a running sync/rebuild explains disk statuses that
        # would otherwise read as faults.
        rebuilding = await self._check_parity_operation(status.get("parityCheckStatus") or {})

        # Check all disk types
        await self._check_disks(status.get("disks", []), "Data Disk", rebuilding)
        await self._check_disks(status.get("parities", []), "Parity Disk", rebuilding)
        await self._check_disks(status.get("caches", []), "Cache Disk", rebuilding)

        return status

    async def _check_parity_operation(self, parity_check: dict[str, Any]) -> bool:
        """Report parity sync/check progress and completion.

        Args:
            parity_check: The ``parityCheckStatus`` dict (may be empty).

        Returns:
            True while a parity operation is running or paused.
        """
        state = str(parity_check.get("status") or "").upper()
        active = state in _PARITY_ACTIVE_STATUSES

        if active and not self._parity_active:
            progress = parity_check.get("progress")
            speed = parity_check.get("speed")
            lines = [
                "A parity sync or disk rebuild is under way.",
                "The array is **not** fully protected until it completes.",
            ]
            if progress is not None:
                lines.insert(0, f"Progress: {progress}%")
            if speed:
                lines.insert(1 if progress is not None else 0, f"Speed: {speed} MB/s")
            if state == "PAUSED":
                lines.insert(0, "Currently PAUSED.")
            await self._on_alert(
                title="🔄 Parity Operation Running",
                message="\n".join(lines),
                alert_type="array",
            )

        elif self._parity_active and not active:
            errors = parity_check.get("errors")
            # errors is frequently null on a live server -- "unknown" is honest,
            # "0 errors" would be an invention.
            error_line = (
                f"Errors: {errors}" if errors is not None else "Error count: not reported"
            )
            outcome = {
                "COMPLETED": "✅ Parity Operation Complete",
                "CANCELLED": "⚠️ Parity Operation Cancelled",
                "FAILED": "🔴 Parity Operation Failed",
            }.get(state, "✅ Parity Operation Finished")
            await self._on_alert(
                title=outcome,
                message=f"Final status: {state or 'unknown'}\n{error_line}",
                alert_type="array",
            )
            # Let a genuine fault on the rebuilt disk alert now it is no longer
            # excused by the sync.
            self._alerted_disks = {k for k in self._alerted_disks if not k.endswith(":status")}

        self._parity_active = active
        return active

    async def _check_capacity(self, status: dict[str, Any]) -> None:
        """Check array capacity and alert if threshold exceeded.

        Args:
            status: Array status dict.
        """
        capacity = status.get("capacity", {})
        kilobytes = capacity.get("kilobytes", {})
        capacity_key = "array:capacity"

        try:
            used = int(kilobytes.get("used", 0))
            total = int(kilobytes.get("total", 1))  # Avoid division by zero

            if total == 0:
                return

            usage_percent = (used / total) * 100

            if usage_percent <= self._config.array_usage_threshold:
                # Re-arm so the next genuine crossing alerts again.
                self._alerted_disks.discard(capacity_key)
                return

            # Disk temp and status both remember what they've alerted on;
            # capacity didn't, so a full array texted the user on every poll
            # (every 5 minutes by default) until someone freed space.
            if capacity_key in self._alerted_disks:
                return

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
            self._alerted_disks.add(capacity_key)
        except (ValueError, TypeError) as e:
            logger.warning(f"Failed to parse capacity: {e}")

    async def _check_disks(
        self,
        disks: list[dict[str, Any]],
        disk_type: str,
        rebuilding: bool = False,
    ) -> None:
        """Check disk temperatures and status.

        Args:
            disks: List of disk dicts.
            disk_type: Type of disk (e.g., "Data Disk", "Parity Disk").
            rebuilding: True while a parity sync/rebuild is running, which makes
                DISK_INVALID/DISK_NEW on the target expected rather than a fault.
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
                    else:
                        # Condition cleared - allow re-alerting if it returns
                        self._alerted_disks.discard(disk_key)
                except (ValueError, TypeError):
                    logger.warning(f"Invalid temperature for {disk_name}: {temp}")

            # Check disk status
            status = disk.get("status", "")
            status_key = f"{disk_key}:status"

            if rebuilding and status in _REBUILD_EXPECTED_STATUSES:
                # Expected while the sync writes to this disk. Deliberately not
                # added to _alerted_disks, so a real fault appearing later still
                # alerts.
                logger.debug(
                    f"{disk_type} {disk_name} is {status} during a parity operation - expected"
                )
                continue

            if status and status != "DISK_OK":
                # Only alert if we haven't already alerted for this disk
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
            else:
                # Status recovered - allow re-alerting
                self._alerted_disks.discard(status_key)

    def clear_alert_state(self) -> None:
        """Clear the alerted disks tracking.

        Should be called when array is unmuted to allow re-alerting.
        """
        self._alerted_disks.clear()
        logger.debug("Cleared array alert state")
