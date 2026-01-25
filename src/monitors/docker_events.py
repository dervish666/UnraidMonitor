import asyncio
import logging
from datetime import datetime
from typing import Any, Callable, Awaitable

import docker
from docker.models.containers import Container

from src.models import ContainerInfo
from src.state import ContainerStateManager

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
    ):
        self.state_manager = state_manager
        self.ignored_containers = set(ignored_containers or [])
        self._client: docker.DockerClient | None = None
        self._running = False

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

        # Run blocking event loop in thread
        await asyncio.to_thread(self._event_loop)

    def stop(self) -> None:
        """Stop monitoring."""
        self._running = False
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
