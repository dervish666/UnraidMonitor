import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, TYPE_CHECKING

import docker
from docker.models.containers import Container

from src.models import ContainerInfo
from src.state import ContainerStateManager
from src.services.docker_client import SharedDockerClient
from src.utils.poll_health import PollHealth

if TYPE_CHECKING:
    from src.alerts.manager import AlertSender
    from src.alerts.rate_limiter import RateLimiter
    from src.alerts.mute_manager import MuteManager

logger = logging.getLogger(__name__)


def split_event_action(action: str) -> tuple[str, str]:
    """Split a Docker event Action into (verb, detail).

    Docker sends health events as ``"health_status: unhealthy"`` (verb and
    status in one string, no ``health_status`` attribute), and exec events as
    ``"exec_start: sh -c ..."``. Comparing the raw Action to ``"health_status"``
    matched nothing, so unhealthy alerts and auto-heal never fired.
    """
    verb, _, detail = action.partition(":")
    return verb.strip(), detail.strip()


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

    _CLEANUP_INTERVAL = 100

    def __init__(self) -> None:
        self._crashes: dict[str, list[datetime]] = {}
        self._last_escalation: dict[str, datetime] = {}
        self._last_recovery_alert: dict[str, datetime] = {}
        self._record_count = 0

    def _cleanup_stale(self) -> None:
        """Remove entries for containers with no recent activity."""
        now = datetime.now()
        stale_cutoff = now - timedelta(hours=24)
        for name in list(self._crashes):
            if not self._crashes[name]:
                del self._crashes[name]
        for d in (self._last_escalation, self._last_recovery_alert):
            for name in [k for k, v in d.items() if v < stale_cutoff]:
                del d[name]

    def record_crash(self, container_name: str) -> None:
        """Record a crash event (called for ALL non-zero exit crashes)."""
        now = datetime.now()
        if container_name not in self._crashes:
            self._crashes[container_name] = []
        self._crashes[container_name].append(now)
        cutoff = now - timedelta(seconds=self.LOOP_WINDOW_SECONDS)
        self._crashes[container_name] = [
            t for t in self._crashes[container_name] if t > cutoff
        ]
        self._record_count += 1
        if self._record_count % self._CLEANUP_INTERVAL == 0:
            self._cleanup_stale()

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
        # Only send recovery if the container crashed recently, and only once:
        # a second crash in the window means it is looping, not recovering.
        if self.get_crash_count(container_name) != 1:
            return False

        now = datetime.now()
        last = self._last_recovery_alert.get(container_name)
        if last and (now - last).total_seconds() < self.RECOVERY_COOLDOWN_SECONDS:
            return False

        return True

    def record_recovery_alert(self, container_name: str) -> None:
        """Record that a recovery alert was sent.

        Crash history is left to age out of the window. Clearing it here reset
        the count on every recovery alert, so a container crashing every 90s
        never reached the restart-loop threshold.
        """
        self._last_recovery_alert[container_name] = datetime.now()

    def get_active_crash_loops(self, min_count: int = 3) -> list[tuple[str, int]]:
        """Return containers with crash counts >= min_count."""
        result: list[tuple[str, int]] = []
        for name in self._crashes:
            count = self.get_crash_count(name)
            if count >= min_count:
                result.append((name, count))
        return result


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
    RECONCILE_SECONDS = 30  # Re-check unannounced unhealthy containers and lost crash alerts
    MAX_PENDING_CRASHES = 20
    # A die within this long of a kill/stop event for the same container is an
    # intentional stop (docker stop, /restart, /pull, auto-heal, memory kill,
    # the Unraid Stop button, appdata backup), not a crash. docker stop always
    # emits kill before die, so the kill event covers the bot's own actions too.
    INTENTIONAL_STOP_WINDOW = 30.0

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
        # One wrapper for the monitor's lifetime: consumers wired at startup
        # keep it, and every (re)connect swaps the client inside it.
        self._shared_client = SharedDockerClient(None)
        self._running = False
        # Use bounded queue to prevent memory issues under load
        self._pending_alerts: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(
            maxsize=self.MAX_QUEUE_SIZE
        )
        self._alert_task: asyncio.Task[None] | None = None
        self._backoff_seconds = self.INITIAL_BACKOFF_SECONDS
        self._crash_tracker = CrashTracker()
        # "last event" counts a (re)connected stream as well as each event,
        # so a quiet but healthy daemon does not read as stale.
        self.poll_health = PollHealth(noun="event")
        self._unhealthy_alerted: set[str] = set()  # Track containers already alerted as unhealthy
        # Seen unhealthy but not yet announced (muted, or the send failed).
        # Docker only emits a health event on a status change, so without
        # this a container that stays unhealthy past its mute stays silent.
        self._unhealthy_pending: set[str] = set()
        self._last_reconcile = time.monotonic()
        # Crash alerts a send failed to deliver: name -> (send kwargs, escalation).
        # Retried every reconcile pass; bounded, oldest dropped first.
        self._crash_pending: dict[str, tuple[dict[str, Any], bool]] = {}
        # Other producers' retry passes run on the same cadence (log errors).
        self.reconcile_hooks: list[Callable[[], Awaitable[None]]] = []
        # monotonic time of the last kill/stop and oom event per container,
        # written from the events thread, read by the alert task.
        self._stopped_at: dict[str, float] = {}
        self._oom_at: dict[str, float] = {}
        self._auto_healer: Any | None = None

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def crash_tracker(self) -> CrashTracker:
        return self._crash_tracker

    @property
    def shared_client(self) -> SharedDockerClient:
        """Get the shared Docker client wrapper for passing to other components."""
        return self._shared_client

    def set_auto_healer(self, auto_healer: Any) -> None:
        """Late-bind the AutoHealer (created after the ContainerController exists)."""
        self._auto_healer = auto_healer

    def connect(self) -> None:
        """Connect to Docker socket.

        On failure the shared wrapper stays disconnected, so consumers can be
        wired now; ``start()`` keeps retrying and fills it in.
        """
        try:
            self._client = docker.DockerClient(base_url=self._docker_socket_path)
        except Exception:
            self._client = None
            raise
        self._shared_client.replace(self._client)
        self.poll_health.ok()
        logger.info("Connected to Docker socket")

    @property
    def connected(self) -> bool:
        return self._client is not None

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
        """Start monitoring Docker events with automatic reconnection.

        If Docker was unreachable at startup, keep trying with backoff; the
        rest of the bot runs meanwhile.
        """
        self._running = True
        self._loop = asyncio.get_event_loop()
        while self._running and not self._client:
            try:
                await asyncio.to_thread(self._reconnect)
                self._backoff_seconds = self.INITIAL_BACKOFF_SECONDS
                logger.info("Connected to Docker after startup")
            except Exception as e:
                self.poll_health.failed(e)
                logger.warning(
                    f"Docker still unreachable ({e}); retrying in {self._backoff_seconds}s"
                )
                self._client = None
                await asyncio.sleep(self._backoff_seconds)
                self._backoff_seconds = min(self._backoff_seconds * 2, self.MAX_BACKOFF_SECONDS)
        if not self._running:
            return
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

                self.poll_health.failed(e)
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
                    # 1 + 2N blocking Docker calls; keep them off the event loop.
                    await asyncio.to_thread(self._reconnect)
                    self._backoff_seconds = self.INITIAL_BACKOFF_SECONDS
                    logger.info("Reconnected to Docker")
                except Exception as reconnect_error:
                    self.poll_health.failed(reconnect_error)
                    logger.error(f"Reconnection failed: {reconnect_error}")

    def _queue_alert(self, item: dict[str, Any], kind: str) -> None:
        """Hand an event to the alert queue from the events thread.

        put_nowait has to run on the loop, so QueueFull surfaces there, not
        here; the closure catches it where it actually happens.
        """
        def _put() -> None:
            try:
                self._pending_alerts.put_nowait(item)
            except asyncio.QueueFull:
                logger.warning(f"Alert queue full, dropping {kind} event")

        try:
            self._loop.call_soon_threadsafe(_put)
        except RuntimeError as e:
            logger.error(f"Failed to queue {kind} event: {e}")

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
        self._shared_client.replace(self._client)

        # Fetch container list once and reuse
        containers = self._client.containers.list(all=True)
        current_names = {c.name for c in containers}
        for name in list(self.state_manager.get_all_names()):
            if name not in current_names:
                self.state_manager.remove(name)

        self.load_initial_state(containers=containers)
        self._unhealthy_alerted.clear()
        self.poll_health.ok()
        logger.info("Docker reconnection successful")

    async def _process_alerts(self) -> None:
        """Process alerts from the queue - runs as async task."""
        while self._running:
            try:
                # Wait for an event with timeout to allow checking _running flag
                if time.monotonic() - self._last_reconcile >= self.RECONCILE_SECONDS:
                    try:
                        await self._reconcile_unhealthy()
                        await self._retry_pending_crashes()
                        for hook in self.reconcile_hooks:
                            try:
                                await hook()
                            except Exception as e:
                                logger.error(f"Alert retry pass failed: {e}", exc_info=True)
                    finally:
                        # Stamped after the pass, even a failed one: a pass
                        # slowed by an outage must not come due again after a
                        # single event, and one that raises must not spin.
                        self._last_reconcile = time.monotonic()
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
                self.poll_health.ok()

                action, detail = split_event_action(event.get("Action", ""))
                container_name = event.get("Actor", {}).get("Attributes", {}).get("name", "")

                if container_name in self.ignored_containers:
                    continue

                if action in ("kill", "stop"):
                    self._stopped_at[container_name] = time.monotonic()
                elif action == "oom":
                    self._oom_at[container_name] = time.monotonic()

                oom_killed = False
                if action in ("start", "die", "health_status"):
                    oom_killed = self._handle_event(event)

                # Queue start events for recovery alert processing (thread-safe)
                if action == "start" and self.alert_manager:
                    self._queue_alert({**event, "_alert_type": "recovery"}, "recovery")

                # Queue health_status events for unhealthy alerts
                if action == "health_status" and self.alert_manager:
                    if detail == "unhealthy":
                        self._queue_alert({**event, "_alert_type": "health"}, "unhealthy")
                    elif detail == "healthy":
                        # Clear the alerted flag so we re-alert if it goes unhealthy
                        # again. Plain set ops from the events thread; the GIL makes
                        # each atomic and the loop only reads them per alert.
                        self._unhealthy_alerted.discard(container_name)
                        self._unhealthy_pending.discard(container_name)

                # Queue die events for crash alert processing (thread-safe)
                if action == "die" and self.alert_manager:
                    now = time.monotonic()
                    self._queue_alert({
                        **event,
                        "_oom": oom_killed
                        or now - self._oom_at.get(container_name, float("-inf"))
                        < self.INTENTIONAL_STOP_WINDOW,
                        "_intentional": now - self._stopped_at.get(container_name, float("-inf"))
                        < self.INTENTIONAL_STOP_WINDOW,
                    }, "crash")

        except docker.errors.APIError as e:
            logger.error(f"Docker API error: {e}")
            raise
        except Exception as e:
            if self._running:
                # Re-raise to trigger reconnection in start()
                raise
            logger.debug(f"Suppressed exception during shutdown: {e}")

    def _handle_event(self, event: dict[str, Any]) -> bool:
        """Refresh the container's state. Returns State.OOMKilled for a die."""
        if not self._client:
            return False

        container_name = event.get("Actor", {}).get("Attributes", {}).get("name", "")
        action = event.get("Action", "")

        logger.info(f"Docker event: {action} for {container_name}")

        try:
            container = self._client.containers.get(container_name)
            info = parse_container(container)
            self.state_manager.update(info)
            state = container.attrs.get("State") if isinstance(container.attrs, dict) else None
            return bool(isinstance(state, dict) and state.get("OOMKilled") is True)
        except docker.errors.NotFound:
            logger.warning(f"Container {container_name} not found after event")
        except Exception as e:
            logger.error(f"Error handling event for {container_name}: {e}")
        return False

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

        oom_killed = bool(event.get("_oom"))
        if event.get("_intentional") and not oom_killed:
            logger.info(f"{container_name} exited {exit_code} after a stop/kill request; not a crash")
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
            window_min = CrashTracker.LOOP_WINDOW_SECONDS // 60
            logger.warning(
                f"Restart loop detected: {container_name} crashed {crash_count} times "
                f"in {window_min} minutes"
            )
            await self._deliver_crash(container_name, {
                "exit_code": exit_code,
                "image": self._get_container_image(container_name),
                "uptime_seconds": self._get_container_uptime(container_name),
                "restart_loop_count": crash_count,
                "oom_killed": oom_killed,
            }, escalation=True)
            return

        # Check rate limiter if available. The limiter is shared with log-error
        # alerts, which key on the bare name; a separate key stops an error
        # alert from silencing the crash that usually follows it.
        if self.rate_limiter:
            rate_key = f"{container_name}:crash"
            if not self.rate_limiter.should_alert(rate_key):
                self.rate_limiter.record_suppressed(rate_key)
                logger.debug(f"Rate-limited crash alert for {container_name}")
                return

        logger.info(f"Container {container_name} crashed with exit code {exit_code}")

        await self._deliver_crash(container_name, {
            "exit_code": exit_code,
            "image": self._get_container_image(container_name),
            "uptime_seconds": self._get_container_uptime(container_name),
            "oom_killed": oom_killed,
        }, escalation=False)

    async def _deliver_crash(
        self, container_name: str, kwargs: dict[str, Any], escalation: bool,
    ) -> bool:
        """Send a crash alert; commit cooldown/escalation state only once delivered.

        Events are handled one at a time on the alert task, so there is no
        race between the check and the commit. A send that fails is held for
        the reconcile pass instead of being recorded as sent.
        """
        assert self.alert_manager is not None
        if await self.alert_manager.send_crash_alert(container_name=container_name, **kwargs):
            self._crash_pending.pop(container_name, None)
            if escalation:
                self._crash_tracker.record_escalation(container_name)
            elif self.rate_limiter:
                self.rate_limiter.record_alert(f"{container_name}:crash")
            return True
        logger.warning(f"Crash alert for {container_name} not delivered; will retry")
        self._crash_pending.pop(container_name, None)
        self._crash_pending[container_name] = (kwargs, escalation)
        while len(self._crash_pending) > self.MAX_PENDING_CRASHES:
            dropped = next(iter(self._crash_pending))
            del self._crash_pending[dropped]
            logger.error(f"Dropped undelivered crash alert for {dropped}: too many pending")
        return False

    async def _retry_pending_crashes(self) -> None:
        """Retry held crash alerts, stopping at the first that still fails.

        Each failed send costs the full Telegram retry ladder, and this runs on
        the event task, so one failure per pass keeps an outage from delaying
        auto-heal and health handling by minutes.
        """
        for name, (kwargs, escalation) in list(self._crash_pending.items()):
            if self.mute_manager and self.mute_manager.is_muted(name):
                del self._crash_pending[name]  # muted since: the user asked for quiet
                continue
            if not await self._deliver_crash(name, kwargs, escalation):
                return

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

        # Check mute. Remember it, so the reconcile pass announces it if the
        # container is still unhealthy when the mute ends.
        if self.mute_manager and self.mute_manager.is_muted(container_name):
            self._unhealthy_pending.add(container_name)
            return
        self._unhealthy_pending.discard(container_name)
        self._unhealthy_alerted.add(container_name)

        logger.info(f"Container {container_name} is unhealthy")
        if self._auto_healer is not None and self._auto_healer.is_enabled(container_name):
            outcome = await self._auto_healer.heal(container_name)
            if outcome == "gave_up" and not self._auto_healer.give_up_announced(container_name):
                # The give-up alert was lost: let the reconcile pass re-enter
                # heal(), which resends it while the guard is still exhausted.
                self._unhealthy_alerted.discard(container_name)
                self._unhealthy_pending.add(container_name)
            elif outcome != "gave_up":
                # Let the post-restart unhealthy event re-enter heal(): the
                # storm guard bounds the retry loop, not this dedup set. Once
                # the healer gives up, keep the flag so we stay quiet until a
                # healthy transition clears it.
                self._unhealthy_alerted.discard(container_name)
            return
        sent = await self.alert_manager.send_health_alert(
            container_name=container_name,
            health_status="unhealthy",
        )
        if not sent:
            # Lost to a Telegram outage: let the reconcile pass try again.
            self._unhealthy_alerted.discard(container_name)
            self._unhealthy_pending.add(container_name)

    async def _reconcile_unhealthy(self) -> None:
        """Announce containers still unhealthy whose alert was held back.

        Stops at the first one whose alert still fails, like the other retry
        passes on this task: an outage costs one failed send per pass.
        """
        for name in list(self._unhealthy_pending):
            if self.mute_manager and self.mute_manager.is_muted(name):
                continue
            info = self.state_manager.get(name)
            if info is None or info.health != "unhealthy":
                self._unhealthy_pending.discard(name)
                continue
            await self._handle_health_event({"Actor": {"Attributes": {"name": name}}})
            if name in self._unhealthy_pending:
                return  # not muted, so back in the set means the send failed

    def _get_container_image(self, container_name: str) -> str:
        """Get image name for a container from state manager."""
        info = self.state_manager.get(container_name)
        return info.image if info else "unknown"

    def _get_container_uptime(self, container_name: str) -> int | None:
        """Get uptime seconds for a container from state manager."""
        info = self.state_manager.get(container_name)
        return info.uptime_seconds if info else None
