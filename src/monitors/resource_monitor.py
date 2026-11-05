import asyncio
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, TYPE_CHECKING

import docker

from src.utils.poll_health import PollHealth
from src.constants import ALERT_REMINDER_SECONDS, EPISODE_RECOVERY_RATIO
from src.utils.formatting import format_bytes
from src.services.docker_client import DockerClientLike

if TYPE_CHECKING:
    from src.config import ResourceConfig
    from src.alerts.manager import AlertSender
    from src.alerts.rate_limiter import RateLimiter
    from src.alerts.mute_manager import MuteManager

logger = logging.getLogger(__name__)


@dataclass
class ContainerStats:
    """Resource statistics for a container."""

    name: str
    cpu_percent: float
    memory_percent: float
    memory_bytes: int
    memory_limit: int
    cpu_cores: int = 1
    net_rx_bytes: int = 0
    net_tx_bytes: int = 0
    block_read_bytes: int = 0
    block_write_bytes: int = 0
    pids: int = 0

    @property
    def memory_display(self) -> str:
        """Format memory usage for display."""
        return format_bytes(self.memory_bytes)

    @property
    def memory_limit_display(self) -> str:
        """Format memory limit for display."""
        return format_bytes(self.memory_limit)


def calculate_cpu_percent(stats: dict[str, Any]) -> float:
    """Calculate CPU percentage from Docker stats.

    Docker provides cumulative CPU usage, so we need to calculate
    the delta between current and previous readings.

    Args:
        stats: Docker stats response dict.

    Returns:
        CPU usage as percentage (0-100 per core, can exceed 100 on multi-core).
    """
    cpu_stats = stats.get("cpu_stats", {})
    precpu_stats = stats.get("precpu_stats", {})

    cpu_usage: int = cpu_stats.get("cpu_usage", {}).get("total_usage", 0)
    precpu_usage: int = precpu_stats.get("cpu_usage", {}).get("total_usage", 0)

    system_usage: int = cpu_stats.get("system_cpu_usage", 0)
    presystem_usage: int = precpu_stats.get("system_cpu_usage", 0)

    cpu_delta = cpu_usage - precpu_usage
    system_delta = system_usage - presystem_usage

    if system_delta > 0 and cpu_delta >= 0:
        num_cpus: int = cpu_stats.get("online_cpus", 1)
        return (cpu_delta / system_delta) * num_cpus * 100.0

    return 0.0


def host_cpu_cores(docker_client: Any) -> int:
    """Core count of the Docker host, used as the ceiling for CPU percentages.

    Docker sums container CPU across cores, so 800% is full load on an 8-core box.
    Falls back to this process's own view if the daemon does not answer.
    """
    try:
        ncpu = int(docker_client.info().get("NCPU") or 0)
        if ncpu > 0:
            return ncpu
    except Exception as e:
        fallback = os.cpu_count() or 1
        logger.warning(
            f"Could not read host CPU count from Docker: {e}; using {fallback} from os.cpu_count()"
        )
        return fallback
    return os.cpu_count() or 1


def parse_container_stats(name: str, stats: dict[str, Any]) -> ContainerStats:
    """Parse Docker stats response into ContainerStats.

    Args:
        name: Container name.
        stats: Docker stats response dict.

    Returns:
        ContainerStats with parsed values.
    """
    cpu_percent = calculate_cpu_percent(stats)

    memory_stats = stats.get("memory_stats", {})
    memory_usage = memory_stats.get("usage", 0)
    memory_limit = memory_stats.get("limit", 1)  # Avoid division by zero

    # Subtract page cache, as `docker stats` does (clamp to 0). cgroup v1 names
    # it "cache"; cgroup v2 (Unraid 7) has no "cache" and uses "inactive_file".
    mem_detail = memory_stats.get("stats", {})
    cache = mem_detail.get("cache", mem_detail.get("inactive_file", 0))
    memory_usage = max(0, memory_usage - cache)

    memory_percent = (memory_usage / memory_limit) * 100.0 if memory_limit > 0 else 0.0

    # Parse network I/O
    net_rx_bytes = 0
    net_tx_bytes = 0
    networks = stats.get("networks", {})
    for iface_stats in networks.values():
        net_rx_bytes += iface_stats.get("rx_bytes", 0)
        net_tx_bytes += iface_stats.get("tx_bytes", 0)

    # Parse block I/O
    block_read_bytes = 0
    block_write_bytes = 0
    blkio_stats = stats.get("blkio_stats", {})
    for entry in blkio_stats.get("io_service_bytes_recursive", None) or []:
        op = entry.get("op", "").lower()
        if op == "read":
            block_read_bytes += entry.get("value", 0)
        elif op == "write":
            block_write_bytes += entry.get("value", 0)

    # Parse PIDs
    pids = stats.get("pids_stats", {}).get("current", 0) or 0

    return ContainerStats(
        name=name,
        cpu_percent=round(cpu_percent, 1),
        memory_percent=round(memory_percent, 1),
        memory_bytes=memory_usage,
        memory_limit=memory_limit,
        cpu_cores=max(1, int(stats.get("cpu_stats", {}).get("online_cpus", 0) or 0) or (os.cpu_count() or 1)),
        net_rx_bytes=net_rx_bytes,
        net_tx_bytes=net_tx_bytes,
        block_read_bytes=block_read_bytes,
        block_write_bytes=block_write_bytes,
        pids=pids,
    )


@dataclass
class ViolationState:
    """Tracks sustained threshold violation for a container."""

    metric: str  # "cpu" or "memory"
    started_at: datetime
    current_value: float
    threshold: float
    # monotonic time of the last delivered alert; None until the episode is
    # announced. Once set, the violation only ends below the recovery ratio.
    alerted_at: float | None = None


class ResourceMonitor:
    """Monitors container resource usage and sends alerts."""

    def __init__(
        self,
        docker_client: DockerClientLike,
        config: "ResourceConfig",
        alert_manager: "AlertSender",
        rate_limiter: "RateLimiter",
        mute_manager: "MuteManager | None" = None,
    ):
        self._docker = docker_client
        self._config = config
        self._alert_manager = alert_manager
        self._rate_limiter = rate_limiter
        self._mute_manager = mute_manager
        self._violations: dict[str, dict[str, ViolationState]] = {}
        # Ended episodes whose recovery message has not been delivered yet
        # (muted, or the send failed), keyed (container, metric).
        self._pending_recoveries: dict[tuple[str, str], ViolationState] = {}
        self._violation_last_seen: dict[str, float] = {}
        self._running = False
        self._stats_semaphore = asyncio.Semaphore(10)
        self._last_cleanup: float = 0.0
        self.poll_health = PollHealth()

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_enabled(self) -> bool:
        """Check if resource monitoring is enabled."""
        return self._config.enabled

    async def get_all_stats(self) -> list[ContainerStats]:
        """Get current stats for all running containers.

        Returns:
            List of ContainerStats for all running containers.
        """
        stats, _ = await self._collect_stats()
        return stats

    async def _collect_stats(self) -> tuple[list[ContainerStats], int]:
        """(readings, how many running containers had none)."""
        containers = await asyncio.to_thread(
            self._docker.containers.list, filters={"status": "running"}
        )

        async def fetch_one(container: Any) -> ContainerStats | None:
            async with self._stats_semaphore:
                try:
                    raw_stats = await asyncio.to_thread(container.stats, stream=False)
                    return parse_container_stats(container.name, raw_stats)
                except Exception as e:
                    logger.warning(f"Failed to get stats for {container.name}: {e}")
                    return None

        results = await asyncio.gather(*(fetch_one(c) for c in containers))
        readings = [s for s in results if s is not None]
        return readings, len(results) - len(readings)

    async def get_container_stats(self, name: str) -> ContainerStats | None:
        """Get current stats for a specific container.

        Args:
            name: Container name.

        Returns:
            ContainerStats or None if container not found.
        """
        try:
            def _get_stats() -> dict[str, Any] | None:
                container = self._docker.containers.get(name)
                if container.status != "running":
                    return None
                stats: dict[str, Any] = container.stats(stream=False)
                return stats

            raw_stats = await asyncio.to_thread(_get_stats)
            if raw_stats is None:
                return None
            return parse_container_stats(name, raw_stats)
        except docker.errors.NotFound:
            return None
        except Exception as e:
            logger.warning(f"Failed to get stats for {name}: {e}")
            return None

    def _check_thresholds(self, stats: ContainerStats) -> list[ViolationState]:
        """Check if container exceeds thresholds and track violations.

        Args:
            stats: Current container stats.

        Returns:
            Announced violations that just ended (owed a recovery message).
        """
        cpu_threshold, memory_threshold = self._config.get_thresholds(stats.name)

        # Ensure container has a violations dict
        if stats.name not in self._violations:
            self._violations[stats.name] = {}

        container_violations = self._violations[stats.name]
        recovered: list[ViolationState] = []

        # Check CPU
        recovered += self._update_violation(
            container_violations,
            metric="cpu",
            current_value=stats.cpu_percent,
            threshold=cpu_threshold,
        )

        # Check Memory
        recovered += self._update_violation(
            container_violations,
            metric="memory",
            current_value=stats.memory_percent,
            threshold=memory_threshold,
        )

        # Clean up empty violation dicts
        if not container_violations:
            del self._violations[stats.name]
        return recovered

    def _update_violation(
        self,
        violations: dict[str, ViolationState],
        metric: str,
        current_value: float,
        threshold: int,
    ) -> list[ViolationState]:
        """Update violation state for a single metric; returns it if it just ended.

        Args:
            violations: Container's violation dict to update.
            metric: "cpu" or "memory".
            current_value: Current metric value.
            threshold: Threshold value.
        """
        if current_value > threshold:
            if metric in violations:
                # Update existing violation
                violations[metric].current_value = current_value
                violations[metric].threshold = threshold
            else:
                # Start new violation
                violations[metric] = ViolationState(
                    metric=metric,
                    started_at=datetime.now(),
                    current_value=current_value,
                    threshold=threshold,
                )
        elif metric in violations:
            violation = violations[metric]
            violation.current_value = current_value
            if violation.alerted_at is None:
                # Never announced: a dip resets the sustained timer.
                del violations[metric]
            elif current_value <= threshold * EPISODE_RECOVERY_RATIO:
                del violations[metric]
                return [violation]
            # Announced and in the band just under the threshold: hold it.
        return []

    def _is_sustained(self, violation: ViolationState) -> bool:
        """Check if a violation has exceeded the sustained threshold.

        Args:
            violation: Violation state to check.

        Returns:
            True if violation is sustained.
        """
        elapsed = datetime.now() - violation.started_at
        return elapsed.total_seconds() >= self._config.sustained_threshold_seconds

    def _get_sustained_violations(self, container_name: str) -> list[ViolationState]:
        """Get list of sustained violations for a container.

        Args:
            container_name: Container to check.

        Returns:
            List of sustained ViolationState objects.
        """
        container_violations = self._violations.get(container_name, {})
        return [v for v in container_violations.values() if self._is_sustained(v)]

    async def _send_alert(self, stats: ContainerStats, violation: ViolationState) -> None:
        """Alert once per sustained episode, then remind every ALERT_REMINDER_SECONDS.

        Args:
            stats: Current container stats.
            violation: The sustained violation.
        """
        # Check if muted
        if self._mute_manager and self._mute_manager.is_muted(stats.name):
            logger.debug(f"Suppressed resource alert for muted container: {stats.name}")
            return

        now = time.monotonic()
        if violation.alerted_at is not None and now - violation.alerted_at < ALERT_REMINDER_SECONDS:
            return

        duration = int((datetime.now() - violation.started_at).total_seconds())

        sent = await self._alert_manager.send_resource_alert(
            container_name=stats.name,
            metric=violation.metric,
            current_value=violation.current_value,
            threshold=int(violation.threshold),
            duration_seconds=duration,
            memory_bytes=stats.memory_bytes,
            memory_limit=stats.memory_limit,
            memory_percent=stats.memory_percent,
            cpu_percent=stats.cpu_percent,
        )
        if sent:
            violation.alerted_at = now

    MAX_PENDING_RECOVERIES = 50

    async def _flush_recoveries(self) -> None:
        """Deliver owed recovery messages; keep each until Telegram takes it."""
        for (name, metric), violation in list(self._pending_recoveries.items()):
            if metric in self._violations.get(name, {}):
                # Back over the line before the recovery got out: the new
                # episode supersedes it.
                del self._pending_recoveries[(name, metric)]
                continue
            if self._mute_manager and self._mute_manager.is_muted(name):
                continue
            if await self._alert_manager.send_resource_recovery(
                container_name=name,
                metric=violation.metric,
                current_value=violation.current_value,
                threshold=int(violation.threshold),
            ):
                del self._pending_recoveries[(name, metric)]
        while len(self._pending_recoveries) > self.MAX_PENDING_RECOVERIES:
            dropped = next(iter(self._pending_recoveries))
            del self._pending_recoveries[dropped]
            logger.error(f"Dropped undelivered resource recovery for {dropped[0]}: too many pending")

    async def start(self) -> None:
        """Start the monitoring loop."""
        if not self._config.enabled:
            logger.info("Resource monitoring disabled")
            return

        self._running = True
        logger.info(
            f"Starting resource monitor (poll interval: {self._config.poll_interval_seconds}s)"
        )

        while self._running:
            try:
                await self._poll_cycle()
                self.poll_health.ok()
            except Exception as e:
                self.poll_health.failed(e)
                logger.error(f"Error in resource monitor poll cycle: {e}")

            # Wait for next poll
            await asyncio.sleep(self._config.poll_interval_seconds)

    def stop(self) -> None:
        """Stop the monitoring loop."""
        self._running = False
        logger.info("Stopping resource monitor")

    async def _poll_cycle(self) -> None:
        """Execute one polling cycle."""
        now = time.monotonic()
        if now - self._last_cleanup > 300:
            self._last_cleanup = now
            self._rate_limiter.cleanup_stale()

        stats_list, failed = await self._collect_stats()
        if failed and not stats_list:
            # Nothing running reads as an empty list too; this is not that.
            # Raising lets the loop record the failure in /health.
            raise RuntimeError(f"stats failed for all {failed} running containers")
        active_names = {s.name for s in stats_list}

        for stats in stats_list:
            for ended in self._check_thresholds(stats):
                self._pending_recoveries[(stats.name, ended.metric)] = ended

            # Check for sustained violations and send alerts
            sustained = self._get_sustained_violations(stats.name)
            for violation in sustained:
                await self._send_alert(stats, violation)

        await self._flush_recoveries()

        # Mark last-seen time and evict violations for containers gone > 10 minutes
        for name in active_names:
            self._violation_last_seen[name] = now
        stale = [
            name for name in self._violations
            if name not in active_names
            and now - self._violation_last_seen.get(name, 0) > 600
        ]
        for name in stale:
            del self._violations[name]
            self._violation_last_seen.pop(name, None)
