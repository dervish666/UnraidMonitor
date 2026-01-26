import asyncio
import logging
from datetime import datetime
from typing import Any, Callable, Awaitable, TYPE_CHECKING

import docker
from docker.models.containers import Container

from src.models import ContainerInfo
from src.state import ContainerStateManager

if TYPE_CHECKING:
    from src.alerts.manager import AlertManager
    from src.alerts.rate_limiter import RateLimiter
    from src.alerts.mute_manager import MuteManager

logger = logging.getLogger(__name__)


def parse_container(container: Container) -> ContainerInfo:
    """Convert Docker SDK container to ContainerInfo."""
    # Get image name
    tags = container.image.tags
    image = tags[0] if tags else container.image.id

    # Get health status if available
    state = container.attrs.get("State", {})
    health_info = state.get("Health")
    health = health_info.get("Status") if health_info else None

    # Parse started_at timestamp
    started_at_str = state.get("StartedAt")
    started_at = None
    if started_at_str and not started_at_str.startswith("0001"):
        try:
            # Remove nanoseconds and parse
            clean_ts = started_at_str.split(".")[0] + "Z"
            started_at = datetime.fromisoformat(clean_ts.replace("Z", "+00:00"))
        except (ValueError, IndexError):
            pass

    return ContainerInfo(
        name=container.name,
        status=container.status,
        health=health,
        image=image,
        started_at=started_at,
    )


class DockerEventMonitor:
    def __init__(
        self,
        state_manager: ContainerStateManager,
        ignored_containers: list[str] | None = None,
        alert_manager: "AlertManager | None" = None,
        rate_limiter: "RateLimiter | None" = None,
        mute_manager: "MuteManager | None" = None,
    ):
        self.state_manager = state_manager
        self.ignored_containers = set(ignored_containers or [])
        self.alert_manager = alert_manager
        self.rate_limiter = rate_limiter
        self.mute_manager = mute_manager
        self._client: docker.DockerClient | None = None
        self._running = False
        self._pending_alerts: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._alert_task: asyncio.Task | None = None

    def connect(self) -> None:
        """Connect to Docker socket."""
        self._client = docker.DockerClient(base_url="unix:///var/run/docker.sock")
        logger.info("Connected to Docker socket")

    def load_initial_state(self) -> None:
        """Load all containers into state manager."""
        if not self._client:
            raise RuntimeError("Not connected to Docker")

        containers = self._client.containers.list(all=True)
        for container in containers:
            if container.name not in self.ignored_containers:
                info = parse_container(container)
                self.state_manager.update(info)

        logger.info(f"Loaded {len(containers)} containers into state")

    async def start(self) -> None:
        """Start monitoring Docker events."""
        if not self._client:
            raise RuntimeError("Not connected to Docker")

        self._running = True
        logger.info("Starting Docker event monitor")

        # Start the alert processor task
        if self.alert_manager:
            self._alert_task = asyncio.create_task(self._process_alerts())

        # Run blocking event loop in thread
        await asyncio.to_thread(self._event_loop)

    async def _process_alerts(self) -> None:
        """Process alerts from the queue - runs as async task."""
        while self._running:
            try:
                # Wait for an event with timeout to allow checking _running flag
                try:
                    event = await asyncio.wait_for(
                        self._pending_alerts.get(), timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue

                await self._handle_crash_event(event)
            except Exception as e:
                logger.error(f"Error processing alert: {e}")

    def stop(self) -> None:
        """Stop monitoring."""
        self._running = False
        if self._alert_task:
            self._alert_task.cancel()
        logger.info("Stopping Docker event monitor")

    def _event_loop(self) -> None:
        """Blocking event loop - runs in thread."""
        if not self._client:
            return

        for event in self._client.events(decode=True, filters={"type": "container"}):
            if not self._running:
                break

            action = event.get("Action", "")
            container_name = event.get("Actor", {}).get("Attributes", {}).get("name", "")

            if container_name in self.ignored_containers:
                continue

            if action in ("start", "die", "health_status"):
                self._handle_event(event)

            # Queue die events for crash alert processing
            if action == "die" and self.alert_manager:
                try:
                    self._pending_alerts.put_nowait(event)
                except Exception as e:
                    logger.error(f"Failed to queue crash event: {e}")

    def _handle_event(self, event: dict[str, Any]) -> None:
        """Handle a Docker event."""
        if not self._client:
            return

        container_name = event.get("Actor", {}).get("Attributes", {}).get("name", "")
        action = event.get("Action", "")

        logger.info(f"Docker event: {action} for {container_name}")

        try:
            container = self._client.containers.get(container_name)
            info = parse_container(container)
            self.state_manager.update(info)
        except docker.errors.NotFound:
            logger.warning(f"Container {container_name} not found after event")
        except Exception as e:
            logger.error(f"Error handling event for {container_name}: {e}")

    async def _handle_crash_event(self, event: dict[str, Any]) -> None:
        """Handle a container crash event and send alert if appropriate."""
        if not self.alert_manager:
            return

        attributes = event.get("Actor", {}).get("Attributes", {})
        container_name = attributes.get("name", "")
        exit_code_str = attributes.get("exitCode", "0")

        try:
            exit_code = int(exit_code_str)
        except ValueError:
            exit_code = 0

        # Skip if exit code is 0 (normal stop)
        if exit_code == 0:
            logger.debug(f"Container {container_name} exited normally (code 0)")
            return

        # Skip if container is in ignored list
        if container_name in self.ignored_containers:
            logger.debug(f"Ignoring crash alert for ignored container: {container_name}")
            return

        # Check if muted
        if self.mute_manager and self.mute_manager.is_muted(container_name):
            logger.debug(f"Suppressed crash alert for muted container: {container_name}")
            return

        # Check rate limiter if available
        if self.rate_limiter:
            if not self.rate_limiter.should_alert(container_name):
                self.rate_limiter.record_suppressed(container_name)
                logger.debug(f"Rate-limited crash alert for {container_name}")
                return
            self.rate_limiter.record_alert(container_name)

        # Get container info for image name and uptime
        container_info = self.state_manager.get(container_name)
        image = container_info.image if container_info else "unknown"
        uptime_seconds = container_info.uptime_seconds if container_info else None

        logger.info(f"Container {container_name} crashed with exit code {exit_code}")

        await self.alert_manager.send_crash_alert(
            container_name=container_name,
            exit_code=exit_code,
            image=image,
            uptime_seconds=uptime_seconds,
        )
