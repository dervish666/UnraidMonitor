import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, TYPE_CHECKING

import docker
from docker.models.containers import Container

from src.models import ContainerInfo
from src.state import ContainerStateManager
from src.services.docker_client import SharedDockerClient

if TYPE_CHECKING:
    from src.alerts.manager import AlertSender
    from src.alerts.rate_limiter import RateLimiter
    from src.alerts.mute_manager import MuteManager

logger = logging.getLogger(__name__)


class CrashTracker:
    """Tracks crash events per container to detect restart loops.

    Records all crashes (regardless of mute/rate-limit status) and sends
    an escalated "restart loop" alert when a container exceeds the crash
    threshold within the time window.  Also tracks recovery — when a
    previously-crashed container starts successfully.
    """

    LOOP_THRESHOLD = 5  # Crashes in window to trigger escalation
    LOOP_WINDOW_SECONDS = 600  # 10-minute window
    ESCALATION_COOLDOWN_SECONDS = 600  # Don't re-escalate within 10 min
    RECOVERY_COOLDOWN_SECONDS = 300  # Don't spam recovery alerts

    def __init__(self) -> None:
        self._crashes: dict[str, list[datetime]] = {}
        self._last_escalation: dict[str, datetime] = {}
        self._last_recovery_alert: dict[str, datetime] = {}

    def record_crash(self, container_name: str) -> None:
        """Record a crash event (called for ALL non-zero exit crashes)."""
        now = datetime.now()
        if container_name not in self._crashes:
            self._crashes[container_name] = []
        self._crashes[container_name].append(now)
        # Trim events older than the window
        cutoff = now - timedelta(seconds=self.LOOP_WINDOW_SECONDS)
        self._crashes[container_name] = [
            t for t in self._crashes[container_name] if t > cutoff
        ]

    def check_restart_loop(self, container_name: str) -> tuple[bool, int]:
        """Check if container is in a restart loop.

        Returns:
            Tuple of (should_escalate, crash_count_in_window).
        """
        now = datetime.now()
        cutoff = now - timedelta(seconds=self.LOOP_WINDOW_SECONDS)
        recent = [t for t in self._crashes.get(container_name, []) if t > cutoff]
        count = len(recent)

        if count < self.LOOP_THRESHOLD:
            return False, count

        # Check escalation cooldown
        last = self._last_escalation.get(container_name)
        if last and (now - last).total_seconds() < self.ESCALATION_COOLDOWN_SECONDS:
            return False, count

        return True, count

    def record_escalation(self, container_name: str) -> None:
        """Record that an escalation alert was sent."""
        self._last_escalation[container_name] = datetime.now()

    def get_crash_count(self, container_name: str) -> int:
        """Get recent crash count for a container."""
        cutoff = datetime.now() - timedelta(seconds=self.LOOP_WINDOW_SECONDS)
        return len([t for t in self._crashes.get(container_name, []) if t > cutoff])

    def should_send_recovery(self, container_name: str) -> bool:
        """Check if a recovery alert should be sent for this container.

        Returns True if the container had recent crashes and hasn't had
        a recovery alert sent within the cooldown period.
        """
        # Only send recovery if the container actually crashed recently
        if self.get_crash_count(container_name) == 0:
            return False

        now = datetime.now()
        last = self._last_recovery_alert.get(container_name)
        if last and (now - last).total_seconds() < self.RECOVERY_COOLDOWN_SECONDS:
            return False

        return True

    def record_recovery_alert(self, container_name: str) -> None:
        """Record that a recovery alert was sent and clear crash history."""
        self._last_recovery_alert[container_name] = datetime.now()
        # Clear crash history since the container recovered
        self._crashes.pop(container_name, None)


def parse_container(container: Container) -> ContainerInfo:
    """Convert Docker SDK container to ContainerInfo."""
    # Get image name -- image may have been removed (e.g. after an update)
    try:
        img = container.image
        if img is not None:
            tags = img.tags
            image = tags[0] if tags else img.id
        else:
            image = container.attrs.get("Config", {}).get("Image", "unknown")
    except docker.errors.ImageNotFound:
        image = container.attrs.get("Config", {}).get("Image", "unknown")

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
        name=container.name or "",
        status=container.status,
        health=health,
        image=str(image),
        started_at=started_at,
    )


class DockerEventMonitor:
    # Reconnection settings
    INITIAL_BACKOFF_SECONDS = 1
    MAX_BACKOFF_SECONDS = 60
    MAX_QUEUE_SIZE = 1000  # Prevent unbounded memory growth

    def __init__(
        self,
        state_manager: ContainerStateManager,
        ignored_containers: list[str] | None = None,
        alert_manager: "AlertSender | None" = None,
        rate_limiter: "RateLimiter | None" = None,
        mute_manager: "MuteManager | None" = None,
        docker_socket_path: str = "unix:///var/run/docker.sock",
    ):
        self.state_manager = state_manager
        self.ignored_containers = set(ignored_containers or [])
        self.alert_manager = alert_manager
        self.rate_limiter = rate_limiter
        self.mute_manager = mute_manager
        self._docker_socket_path = docker_socket_path
        self._client: docker.DockerClient | None = None
        self._shared_client: SharedDockerClient | None = None
        self._running = False
        # Use bounded queue to prevent memory issues under load
        self._pending_alerts: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(
            maxsize=self.MAX_QUEUE_SIZE
        )
        self._alert_task: asyncio.Task[None] | None = None
        self._backoff_seconds = self.INITIAL_BACKOFF_SECONDS
        self._crash_tracker = CrashTracker()
        self._unhealthy_alerted: set[str] = set()  # Track containers already alerted as unhealthy

    @property
    def shared_client(self) -> SharedDockerClient | None:
        """Get the shared Docker client wrapper for passing to other components."""
        return self._shared_client

    def connect(self) -> None:
        """Connect to Docker socket."""
        self._client = docker.DockerClient(base_url=self._docker_socket_path)
        self._shared_client = SharedDockerClient(self._client)
        logger.info("Connected to Docker socket")

    def load_initial_state(self, containers: list[Any] | None = None) -> None:
        """Load all containers into state manager.

        Args:
            containers: Optional pre-fetched container list to avoid a
                        duplicate ``containers.list(all=True)`` call.

        Note: This method makes blocking Docker API calls when *containers* is
        not provided. When called from an async context, wrap in
        asyncio.to_thread().
        """
        if not self._client:
            raise RuntimeError("Not connected to Docker")

        if containers is None:
            containers = self._client.containers.list(all=True)
        for container in containers:
            if container.name not in self.ignored_containers:
                info = parse_container(container)
                self.state_manager.update(info)

        logger.info(f"Loaded {len(containers)} containers into state")

    async def start(self) -> None:
        """Start monitoring Docker events with automatic reconnection."""
        if not self._client:
            raise RuntimeError("Not connected to Docker")

        self._running = True
        self._loop = asyncio.get_event_loop()
        logger.info("Starting Docker event monitor")

        # Start the alert processor task
        if self.alert_manager:
            self._alert_task = asyncio.create_task(self._process_alerts())

        # Run event loop with reconnection
        while self._running:
            try:
                # Run blocking event loop in thread
                await asyncio.to_thread(self._event_loop)

                # If we exit normally (stop was called), break
                if not self._running:
                    break

            except Exception as e:
                if not self._running:
                    break

                logger.error(f"Docker event monitor error: {e}")
                logger.info(f"Reconnecting in {self._backoff_seconds} seconds...")

                await asyncio.sleep(self._backoff_seconds)

                # Exponential backoff with cap
                self._backoff_seconds = min(
                    self._backoff_seconds * 2,
                    self.MAX_BACKOFF_SECONDS
                )

                # Attempt to reconnect
                try:
                    self._reconnect()
                    self._backoff_seconds = self.INITIAL_BACKOFF_SECONDS
                    logger.info("Reconnected to Docker")
                except Exception as reconnect_error:
                    logger.error(f"Reconnection failed: {reconnect_error}")

    def _reconnect(self) -> None:
        """Attempt to reconnect to Docker daemon."""
        # Close existing client if any
        if self._client:
            try:
                self._client.close()
            except Exception as e:
                logger.debug(f"Error closing Docker client during reconnect: {e}")

        self._client = docker.DockerClient(base_url=self._docker_socket_path)
        # Update the shared wrapper so all consumers see the new client
        if self._shared_client is not None:
            self._shared_client.replace(self._client)

        # Fetch container list once and reuse
        containers = self._client.containers.list(all=True)
        current_names = {c.name for c in containers}
        for name in list(self.state_manager.get_all_names()):
            if name not in current_names:
                self.state_manager.remove(name)

        self.load_initial_state(containers=containers)
        logger.info("Docker reconnection successful")

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

                if event is None:
                    break  # Sentinel: shutdown requested
                alert_type = event.get("_alert_type")
                if alert_type == "recovery":
                    await self._handle_recovery_event(event)
                elif alert_type == "health":
                    await self._handle_health_event(event)
                else:
                    await self._handle_crash_event(event)
            except Exception as e:
                logger.error(f"Error processing alert: {e}")

    async def stop_async(self) -> None:
        """Stop monitoring and drain pending alerts."""
        self._running = False
        # Signal the alert processor to finish by putting a sentinel
        if self._alert_task and not self._alert_task.done():
            try:
                self._pending_alerts.put_nowait(None)
            except asyncio.QueueFull:
                pass
            try:
                await asyncio.wait_for(self._alert_task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._alert_task.cancel()
        if self._client:
            try:
                self._client.close()
            except Exception as e:
                logger.debug(f"Error closing Docker client during stop: {e}")
        logger.info("Stopping Docker event monitor")

    def stop(self) -> None:
        """Stop monitoring (sync version, does not drain alerts)."""
        self._running = False
        if self._alert_task:
            self._alert_task.cancel()
        if self._client:
            try:
                self._client.close()
            except Exception as e:
                logger.debug(f"Error closing Docker client during stop: {e}")
        logger.info("Stopping Docker event monitor")

    def _event_loop(self) -> None:
        """Blocking event loop - runs in thread.

        Raises exceptions on connection errors to trigger reconnection.
        """
        if not self._client:
            return

        try:
            for event in self._client.events(decode=True, filters={"type": "container"}):
                if not self._running:
                    break

                action = event.get("Action", "")
                container_name = event.get("Actor", {}).get("Attributes", {}).get("name", "")

                if container_name in self.ignored_containers:
                    continue

                if action in ("start", "die", "health_status"):
                    self._handle_event(event)

                # Queue start events for recovery alert processing (thread-safe)
                if action == "start" and self.alert_manager:
                    try:
                        self._loop.call_soon_threadsafe(
                            self._pending_alerts.put_nowait,
                            {**event, "_alert_type": "recovery"},
                        )
                    except asyncio.QueueFull:
                        logger.debug("Alert queue full, dropping recovery event")
                    except Exception as e:
                        logger.error(f"Failed to queue recovery event: {e}")

                # Queue health_status events for unhealthy alerts
                if action == "health_status" and self.alert_manager:
                    health = event.get("Actor", {}).get("Attributes", {}).get("health_status", "")
                    if health == "unhealthy":
                        try:
                            self._loop.call_soon_threadsafe(
                                self._pending_alerts.put_nowait,
                                {**event, "_alert_type": "health"},
                            )
                        except (asyncio.QueueFull, Exception):
                            pass
                    elif health == "healthy":
                        # Clear the alerted flag so we re-alert if it goes unhealthy again
                        self._unhealthy_alerted.discard(container_name)

                # Queue die events for crash alert processing (thread-safe)
                if action == "die" and self.alert_manager:
                    try:
                        self._loop.call_soon_threadsafe(
                            self._pending_alerts.put_nowait, event
                        )
                    except asyncio.QueueFull:
                        logger.warning("Alert queue full, dropping event")
                    except Exception as e:
                        logger.error(f"Failed to queue crash event: {e}")

        except docker.errors.APIError as e:
            logger.error(f"Docker API error: {e}")
            raise
        except Exception as e:
            if self._running:
                # Re-raise to trigger reconnection in start()
                raise
            logger.debug(f"Suppressed exception during shutdown: {e}")

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

        # Always track the crash for restart loop detection (even if muted/rate-limited)
        self._crash_tracker.record_crash(container_name)

        # Check if muted
        if self.mute_manager and self.mute_manager.is_muted(container_name):
            logger.debug(f"Suppressed crash alert for muted container: {container_name}")
            return

        # Check for restart loop — escalated alert uses a separate rate key
        should_escalate, crash_count = self._crash_tracker.check_restart_loop(container_name)
        if should_escalate:
            self._crash_tracker.record_escalation(container_name)
            window_min = CrashTracker.LOOP_WINDOW_SECONDS // 60
            logger.warning(
                f"Restart loop detected: {container_name} crashed {crash_count} times "
                f"in {window_min} minutes"
            )
            await self.alert_manager.send_crash_alert(
                container_name=container_name,
                exit_code=exit_code,
                image=self._get_container_image(container_name),
                uptime_seconds=self._get_container_uptime(container_name),
                restart_loop_count=crash_count,
            )
            return

        # Check rate limiter if available
        if self.rate_limiter:
            if not self.rate_limiter.should_alert(container_name):
                self.rate_limiter.record_suppressed(container_name)
                logger.debug(f"Rate-limited crash alert for {container_name}")
                return
            self.rate_limiter.record_alert(container_name)

        logger.info(f"Container {container_name} crashed with exit code {exit_code}")

        await self.alert_manager.send_crash_alert(
            container_name=container_name,
            exit_code=exit_code,
            image=self._get_container_image(container_name),
            uptime_seconds=self._get_container_uptime(container_name),
        )

    async def _handle_recovery_event(self, event: dict[str, Any]) -> None:
        """Handle a container start event and send recovery alert if it previously crashed."""
        if not self.alert_manager:
            return

        container_name = event.get("Actor", {}).get("Attributes", {}).get("name", "")

        if container_name in self.ignored_containers:
            return

        if not self._crash_tracker.should_send_recovery(container_name):
            return

        self._crash_tracker.record_recovery_alert(container_name)
        logger.info(f"Container {container_name} recovered after crash")

        await self.alert_manager.send_recovery_alert(container_name)

    async def _handle_health_event(self, event: dict[str, Any]) -> None:
        """Handle a container health_status event — alert on unhealthy transition."""
        if not self.alert_manager:
            return

        container_name = event.get("Actor", {}).get("Attributes", {}).get("name", "")
        if not container_name or container_name in self.ignored_containers:
            return

        # Only alert once per unhealthy transition
        if container_name in self._unhealthy_alerted:
            return
        self._unhealthy_alerted.add(container_name)

        # Check mute
        if self.mute_manager and self.mute_manager.is_muted(container_name):
            return

        logger.info(f"Container {container_name} is unhealthy")
        await self.alert_manager.send_health_alert(
            container_name=container_name,
            health_status="unhealthy",
        )

    def _get_container_image(self, container_name: str) -> str:
        """Get image name for a container from state manager."""
        info = self.state_manager.get(container_name)
        return info.image if info else "unknown"

    def _get_container_uptime(self, container_name: str) -> int | None:
        """Get uptime seconds for a container from state manager."""
        info = self.state_manager.get(container_name)
        return info.uptime_seconds if info else None
