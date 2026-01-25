import asyncio
import logging
from typing import Callable, Awaitable

import docker
from docker.models.containers import Container

logger = logging.getLogger(__name__)


def matches_error_pattern(
    line: str,
    error_patterns: list[str],
    ignore_patterns: list[str],
) -> bool:
    """Check if a log line matches any error pattern and no ignore pattern."""
    line_lower = line.lower()

    # Check ignore patterns first
    for pattern in ignore_patterns:
        if pattern.lower() in line_lower:
            return False

    # Check error patterns
    for pattern in error_patterns:
        if pattern.lower() in line_lower:
            return True

    return False


class LogWatcher:
    """Watch container logs for error patterns."""

    def __init__(
        self,
        containers: list[str],
        error_patterns: list[str],
        ignore_patterns: list[str],
        on_error: Callable[[str, str], Awaitable[None]] | None = None,
    ):
        self.containers = containers
        self.error_patterns = error_patterns
        self.ignore_patterns = ignore_patterns
        self.on_error = on_error
        self._client: docker.DockerClient | None = None
        self._running = False
        self._tasks: list[asyncio.Task] = []

    def connect(self) -> None:
        """Connect to Docker socket."""
        self._client = docker.DockerClient(base_url="unix:///var/run/docker.sock")
        logger.info("LogWatcher connected to Docker socket")

    async def start(self) -> None:
        """Start watching logs for all configured containers."""
        if not self._client:
            raise RuntimeError("Not connected to Docker")

        self._running = True

        # Start a log watcher task for each container
        for container_name in self.containers:
            task = asyncio.create_task(self._watch_container(container_name))
            self._tasks.append(task)

        logger.info(f"Started watching logs for {len(self.containers)} containers")

        # Wait for all tasks
        await asyncio.gather(*self._tasks, return_exceptions=True)

    def stop(self) -> None:
        """Stop watching logs."""
        self._running = False
        for task in self._tasks:
            task.cancel()
        logger.info("LogWatcher stopped")

    async def _watch_container(self, container_name: str) -> None:
        """Watch logs for a single container."""
        while self._running:
            try:
                await self._stream_logs(container_name)
            except docker.errors.NotFound:
                logger.warning(f"Container {container_name} not found, waiting...")
                await asyncio.sleep(30)
            except Exception as e:
                logger.error(f"Error watching {container_name}: {e}")
                await asyncio.sleep(5)

    async def _stream_logs(self, container_name: str) -> None:
        """Stream and process logs from a container."""
        if not self._client:
            return

        container = self._client.containers.get(container_name)

        # Stream logs (blocking, run in thread)
        def stream():
            for line in container.logs(stream=True, follow=True, tail=0):
                if not self._running:
                    break
                yield line.decode("utf-8", errors="replace").strip()

        async def process_lines():
            for line in await asyncio.to_thread(lambda: list(stream())):
                if not self._running:
                    break

                if matches_error_pattern(line, self.error_patterns, self.ignore_patterns):
                    logger.debug(f"Error in {container_name}: {line[:100]}")
                    if self.on_error:
                        await self.on_error(container_name, line)

        await process_lines()
