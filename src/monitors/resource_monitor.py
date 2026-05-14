import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, TYPE_CHECKING

import docker

from src.utils.formatting import format_bytes

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

    # Subtract cache from memory usage if available (clamp to 0)
    cache = memory_stats.get("stats", {}).get("cache", 0)
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


class ResourceMonitor:
    """Monitors container resource usage and sends alerts."""

    def __init__(
        self,
        docker_client: docker.DockerClient,
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
        self._running = False
        self._stats_semaphore = asyncio.Semaphore(10)
        self._last_cleanup: float = 0.0

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
        return [s for s in results if s is not None]

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
                return container.stats(stream=False)  # type: ignore[return-value]

            raw_stats = await asyncio.to_thread(_get_stats)
            if raw_stats is None:
                return None
            return parse_container_stats(name, raw_stats)
        except docker.errors.NotFound:
            return None
        except Exception as e:
            logger.warning(f"Failed to get stats for {name}: {e}")
            return None

    def _check_thresholds(self, stats: ContainerStats) -> None:
        """Check if container exceeds thresholds and track violations.

        Args:
            stats: Current container stats.
        """
        cpu_threshold, memory_threshold = self._config.get_thresholds(stats.name)

        # Ensure container has a violations dict
        if stats.name not in self._violations:
            self._violations[stats.name] = {}

        container_violations = self._violations[stats.name]

        # Check CPU
        self._update_violation(
            container_violations,
            metric="cpu",
            current_value=stats.cpu_percent,
            threshold=cpu_threshold,
        )

        # Check Memory
        self._update_violation(
            container_violations,
            metric="memory",
            current_value=stats.memory_percent,
            threshold=memory_threshold,
        )

        # Clean up empty violation dicts
        if not container_violations:
            del self._violations[stats.name]

    def _update_violation(
        self,
        violations: dict[str, ViolationState],
        metric: str,
        current_value: float,
        threshold: int,
    ) -> None:
        """Update violation state for a single metric.

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
            else:
                # Start new violation
                violations[metric] = ViolationState(
                    metric=metric,
                    started_at=datetime.now(),
                    current_value=current_value,
                    threshold=threshold,
                )
        elif metric in violations:
            # Violation cleared
            del violations[metric]

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
        """Send an alert for a sustained violation.

        Args:
            stats: Current container stats.
            violation: The sustained violation.
        """
        # Check if muted
        if self._mute_manager and self._mute_manager.is_muted(stats.name):
            logger.debug(f"Suppressed resource alert for muted container: {stats.name}")
            return

        # Use rate limiter key that includes metric to allow separate cpu/memory alerts
        rate_key = f"{stats.name}:{violation.metric}"

        if not self._rate_limiter.should_alert(rate_key):
            self._rate_limiter.record_suppressed(rate_key)
            logger.debug(f"Rate-limited {violation.metric} alert for {stats.name}")
            return

        self._rate_limiter.record_alert(rate_key)

        duration = int((datetime.now() - violation.started_at).total_seconds())

        await self._alert_manager.send_resource_alert(
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
            except Exception as e:
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

        stats_list = await self.get_all_stats()
        active_names = {s.name for s in stats_list}

        for stats in stats_list:
            self._check_thresholds(stats)

            # Check for sustained violations and send alerts
            sustained = self._get_sustained_violations(stats.name)
            for violation in sustained:
                await self._send_alert(stats, violation)

        # Mark last-seen time and evict violations for containers gone > 10 minutes
        if not hasattr(self, "_violation_last_seen"):
            self._violation_last_seen: dict[str, float] = {}
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
