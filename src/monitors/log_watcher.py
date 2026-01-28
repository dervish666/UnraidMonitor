import asyncio
import logging
from typing import Callable, Awaitable, TYPE_CHECKING

import docker

if TYPE_CHECKING:
    from src.alerts.ignore_manager import IgnoreManager
    from src.alerts.recent_errors import RecentErrorsBuffer

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


def should_alert_for_error(
    container: str,
    line: str,
    error_patterns: list[str],
    ignore_patterns: list[str],
    ignore_manager: "IgnoreManager | None" = None,
) -> bool:
    """Check if an error line should trigger an alert.

    Args:
        container: Container name.
        line: Log line to check.
        error_patterns: Patterns that indicate an error.
        ignore_patterns: Global patterns to ignore.
        ignore_manager: Optional IgnoreManager for per-container ignores.

    Returns:
        True if should alert, False if should be ignored.
    """
    # First check if it matches an error pattern
    if not matches_error_pattern(line, error_patterns, ignore_patterns):
        return False

    # Then check per-container ignores
    if ignore_manager and ignore_manager.is_ignored(container, line):
        return False

    return True


class LogWatcher:
    """Watch container logs for error patterns."""

    def __init__(
        self,
        containers: list[str],
        error_patterns: list[str],
        ignore_patterns: list[str],
        on_error: Callable[[str, str], Awaitable[None]] | None = None,
        ignore_manager: "IgnoreManager | None" = None,
        recent_errors_buffer: "RecentErrorsBuffer | None" = None,
        docker_socket_path: str = "unix:///var/run/docker.sock",
    ):
        self.containers = containers
        self.error_patterns = error_patterns
        self.ignore_patterns = ignore_patterns
        self.on_error = on_error
        self.ignore_manager = ignore_manager
        self.recent_errors_buffer = recent_errors_buffer
        self._docker_socket_path = docker_socket_path
        self._client: docker.DockerClient | None = None
        self._running = False
        self._tasks: list[asyncio.Task] = []

    def connect(self) -> None:
        """Connect to Docker socket."""
        self._client = docker.DockerClient(base_url=self._docker_socket_path)
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

        # Use queue to bridge blocking log stream to async processing
        queue: asyncio.Queue[str | None] = asyncio.Queue()

        def stream_to_queue():
            """Blocking function that streams logs and puts them in the queue."""
            try:
                for line in container.logs(stream=True, follow=True, tail=0):
                    if not self._running:
                        break
                    decoded = line.decode("utf-8", errors="replace").strip()
                    if decoded:  # Skip empty lines
                        queue.put_nowait(decoded)
            except Exception as e:
                logger.error(f"Error streaming logs from {container_name}: {e}")
            finally:
                # Signal end of stream
                queue.put_nowait(None)

        # Start the blocking stream in a thread
        stream_task = asyncio.create_task(asyncio.to_thread(stream_to_queue))

        try:
            # Process lines from queue as they arrive
            while self._running:
                try:
                    line = await asyncio.wait_for(queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue

                if line is None:  # End of stream
                    break

                if should_alert_for_error(
                    container=container_name,
                    line=line,
                    error_patterns=self.error_patterns,
                    ignore_patterns=self.ignore_patterns,
                    ignore_manager=self.ignore_manager,
                ):
                    logger.info(f"Error detected in {container_name}: {line[:100]}")

                    # Store in recent errors buffer
                    if self.recent_errors_buffer:
                        self.recent_errors_buffer.add(container_name, line)

                    if self.on_error:
                        await self.on_error(container_name, line)
        finally:
            stream_task.cancel()
            try:
                await stream_task
            except asyncio.CancelledError:
                pass
