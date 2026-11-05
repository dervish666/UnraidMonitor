import asyncio
import logging
import queue
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Awaitable, Callable, TYPE_CHECKING

import docker

if TYPE_CHECKING:
    from src.alerts.ignore_manager import IgnoreManager
    from src.alerts.recent_errors import RecentErrorsBuffer

logger = logging.getLogger(__name__)

# Pause before re-following a container whose log stream ended cleanly. A
# stopped container ends a follow stream in a few milliseconds, so without
# this the watcher re-queries Docker in a tight loop until it starts again.
LOG_STREAM_RETRY_SECONDS = 10.0

# Per-container buffer between the stream thread and the event loop. Lines
# beyond it are dropped (and counted) rather than queued without bound.
LOG_QUEUE_MAX_LINES = 10000
# Lines handled per wake-up before yielding to the rest of the bot.
LOG_DRAIN_BATCH = 500
# A single log line longer than this is cut. Error matching and alerts need
# the start of a line; a 5 MB base64 blob on one line only costs memory.
LOG_MAX_LINE_CHARS = 4000

_END = object()  # End-of-stream marker in the line buffer


class LineAssembler:
    """Turn a Docker log stream's chunks into whole lines.

    A container started with a TTY streams raw byte chunks: one line can
    arrive in pieces and one chunk can hold several lines. Docker also splits
    log messages longer than 16 KB into several frames. Matching each chunk as
    if it were a line missed errors cut across chunks and merged others.
    """

    def __init__(self, max_chars: int = LOG_MAX_LINE_CHARS) -> None:
        self._max_bytes = max_chars * 4  # worst-case UTF-8 width
        self._max_chars = max_chars
        self._pending = b""

    def _emit(self, raw: bytes) -> str | None:
        text = raw.decode("utf-8", errors="replace").strip()
        if not text:
            return None
        if len(text) > self._max_chars:
            text = text[: self._max_chars] + " …[truncated]"
        return text

    def feed(self, chunk: bytes) -> list[str]:
        """Lines completed by *chunk*; a trailing partial line is kept."""
        data = self._pending + chunk
        parts = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n").split(b"\n")
        self._pending = parts.pop()
        if len(self._pending) > self._max_bytes:
            # No newline in sight: emit what we have rather than grow forever.
            parts.append(self._pending)
            self._pending = b""
        lines = []
        for raw in parts:
            line = self._emit(raw)
            if line is not None:
                lines.append(line)
        return lines

    def flush(self) -> list[str]:
        """The partial line left when the stream ends."""
        raw, self._pending = self._pending, b""
        line = self._emit(raw)
        return [line] if line is not None else []

# Matches the bot's own Python logging output so we never alert on our own logs.
# Format: "2026-02-12 20:39:54,770 - src.some.module - LEVEL - message"
# Also matches __main__ logger (used by src/main.py when run as entry point).
_SELF_LOG_RE = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} - (?:src\.|__main__)")

# The level of one of the bot's own records, and the start of any record in the
# same format (aiogram, asyncio, ...). A record logged with embedded newlines
# arrives as several lines and only the first carries the prefix, so the rest
# would be judged on their own: v0.22.2 logged its startup message that way and
# "9 for log errors" came back as a log-error alert about the bot itself.
_SELF_LOG_LEVEL_RE = re.compile(_SELF_LOG_RE.pattern + r"[\w.]* - (\w+) - ")
_LOG_RECORD_START_RE = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} - ")
_ALERTING_LEVELS = frozenset({"ERROR", "CRITICAL"})


class SelfLogContinuation:
    """Tracks whether the current line continues one of the bot's own quiet records.

    Continuation lines of the bot's own INFO/WARNING/DEBUG records are skipped.
    Continuation lines of its ERROR records, and any traceback, are still
    judged, so a real failure inside the bot keeps alerting.
    """

    def __init__(self) -> None:
        self._quiet = False

    def is_quiet_continuation(self, line: str) -> bool:
        match = _SELF_LOG_LEVEL_RE.match(line)
        if match:
            self._quiet = match.group(1) not in _ALERTING_LEVELS
            return False
        if _LOG_RECORD_START_RE.match(line) or line.startswith("Traceback (most recent call last)"):
            self._quiet = False
            return False
        return self._quiet


_PATTERN_CACHE: dict[tuple[tuple[str, ...], tuple[str, ...]], tuple[list[str], list[str]]] = {}
_PATTERN_CACHE_MAX = 64


def matches_error_pattern(
    line: str,
    error_patterns: list[str],
    ignore_patterns: list[str],
) -> bool:
    """Check if a log line matches any error pattern and no ignore pattern."""
    if _SELF_LOG_RE.match(line):
        return False

    line_lower = line.lower()

    cache_key = (tuple(error_patterns), tuple(ignore_patterns))
    if cache_key not in _PATTERN_CACHE:
        if len(_PATTERN_CACHE) >= _PATTERN_CACHE_MAX:
            _PATTERN_CACHE.clear()
        _PATTERN_CACHE[cache_key] = (
            [p.lower() for p in error_patterns],
            [p.lower() for p in ignore_patterns],
        )
    error_lower, ignore_lower = _PATTERN_CACHE[cache_key]

    # Check ignore patterns first
    for pattern in ignore_lower:
        if pattern in line_lower:
            return False

    # Check error patterns
    for pattern in error_lower:
        if pattern in line_lower:
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
        self._tasks: list[asyncio.Task[None]] = []
        self._total_drops: int = 0
        # Open follow streams, so stop() can close them. A worker thread blocked
        # reading a quiet container only wakes when its socket closes, and the
        # executor's threads are joined at interpreter exit.
        self._streams: set[Any] = set()
        # Wall-clock time each container's last stream ended. The re-follow
        # asks Docker for lines since then, so errors logged during the retry
        # sleep (usually the restart that explains a crash) are not skipped.
        self._stream_ended_at: dict[str, float] = {}
        self._thread_pool = ThreadPoolExecutor(
            max_workers=max(len(containers), 4),
            thread_name_prefix="logwatch",
        )

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def total_drops(self) -> int:
        return self._total_drops

    def connect(self) -> None:
        """Connect to Docker socket."""
        self._client = docker.DockerClient(base_url=self._docker_socket_path)
        logger.info("LogWatcher connected to Docker socket")

    async def start(self) -> None:
        """Start watching logs for all configured containers.

        Waits (with backoff) for Docker if it was unreachable at startup.
        """
        self._running = True
        delay = 5.0
        while self._running and not self._client:
            try:
                await asyncio.to_thread(self.connect)
            except Exception as e:
                logger.warning(f"LogWatcher: Docker unreachable ({e}); retrying in {delay:.0f}s")
                await asyncio.sleep(delay)
                delay = min(delay * 2, 300.0)
        if not self._running:
            return

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
        for stream in list(self._streams):
            try:
                stream.close()
            except Exception as e:
                logger.debug(f"Error closing log stream: {e}")
        self._thread_pool.shutdown(wait=False)
        for task in self._tasks:
            task.cancel()
        logger.info("LogWatcher stopped")

    async def _watch_container(self, container_name: str) -> None:
        """Watch logs for a single container."""
        while self._running:
            try:
                await self._stream_logs(container_name)
                if self._running:
                    await asyncio.sleep(LOG_STREAM_RETRY_SECONDS)
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

        container = await asyncio.to_thread(self._client.containers.get, container_name)

        # The stream thread fills a bounded, thread-safe buffer; the event loop
        # is woken at most once per drain, not once per line. A log storm used
        # to schedule one loop callback per line (call_soon_threadsafe), and
        # those callbacks, not the queue, were the unbounded part.
        buf: queue.Queue[Any] = queue.Queue(maxsize=LOG_QUEUE_MAX_LINES)
        wake = asyncio.Event()
        wake_pending = threading.Event()
        drop_lock = threading.Lock()
        log_stream = None

        loop = asyncio.get_event_loop()

        drops = {"count": 0, "last_warn": 0.0}
        continuation = SelfLogContinuation()

        def _wake_consumer() -> None:
            if not wake_pending.is_set():
                wake_pending.set()
                try:
                    loop.call_soon_threadsafe(wake.set)
                except RuntimeError:
                    pass  # Event loop closed during shutdown

        def _push(item: Any) -> None:
            """Buffer one line from the stream thread, dropping when full."""
            try:
                buf.put_nowait(item)
            except queue.Full:
                with drop_lock:
                    drops["count"] += 1
                    self._total_drops += 1
                    now = time.monotonic()
                    if now - drops["last_warn"] >= 60.0:
                        logger.warning(
                            f"Log queue full for {container_name}: "
                            f"dropped {drops['count']} lines (log storm protection)"
                        )
                        drops["count"] = 0
                        drops["last_warn"] = now
            _wake_consumer()

        def stream_to_queue() -> None:
            """Blocking function that streams logs and puts them in the queue."""
            nonlocal log_stream
            assembler = LineAssembler()
            try:
                resume_from = self._stream_ended_at.get(container_name)
                if resume_from is not None:
                    log_stream = container.logs(stream=True, follow=True, since=int(resume_from))
                else:
                    log_stream = container.logs(stream=True, follow=True, tail=0)
                self._streams.add(log_stream)
                for chunk in log_stream:
                    if not self._running:
                        break
                    for line in assembler.feed(chunk):
                        _push(line)
                for line in assembler.flush():
                    _push(line)
            except Exception as e:
                if self._running:
                    logger.error(f"Error streaming logs from {container_name}: {e}")
            finally:
                # Whole seconds: Docker's float `since` is not portable across
                # API versions. Rounding down re-reads at most a second of lines.
                self._stream_ended_at[container_name] = time.time()
                # Close the log stream to release resources
                if log_stream is not None:
                    self._streams.discard(log_stream)
                    try:
                        log_stream.close()
                    except Exception as e:
                        logger.debug(f"Error closing log stream for {container_name}: {e}")
                # Signal end of stream. Dropped if the buffer is full; the
                # consumer also checks stream_task.done() so a dropped
                # sentinel cannot strand it.
                _push(_END)

        stream_future = loop.run_in_executor(self._thread_pool, stream_to_queue)
        stream_task = asyncio.ensure_future(stream_future)

        try:
            ended = False
            while self._running and not ended:
                try:
                    await asyncio.wait_for(wake.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    if stream_task.done() and buf.empty():
                        break  # Stream ended and its sentinel was dropped
                    continue
                # Clear before draining: a line pushed after this point
                # schedules a fresh wake, so none is left behind.
                wake.clear()
                wake_pending.clear()
                batch: list[Any] = []
                while len(batch) < LOG_DRAIN_BATCH:
                    try:
                        batch.append(buf.get_nowait())
                    except queue.Empty:
                        break
                if len(batch) == LOG_DRAIN_BATCH:
                    wake.set()  # More waiting; take it on the next pass

                for line in batch:
                    if line is _END:
                        ended = True
                        break
                    await self._handle_line(container_name, line, continuation)
                # Let other containers' watchers and the bot run between batches.
                await asyncio.sleep(0)
        finally:
            stream_task.cancel()
            try:
                await stream_task
            except asyncio.CancelledError:
                pass

    async def _handle_line(
        self, container_name: str, line: str, continuation: "SelfLogContinuation",
    ) -> None:
        """Run one complete log line through the ignore and alert checks."""
        if continuation.is_quiet_continuation(line):
            return

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
