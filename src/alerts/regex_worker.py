"""Regex ignores run in a child process with a hard time limit.

Python's re has no timeout and holds the GIL while it searches, so a
backtracking pattern on a long log line used to stall the whole bot, and
static analysis of the pattern kept missing shapes (six review rounds, a new
bypass each time, and each tighter rule refused ordinary filters). A search
that overruns here is abandoned by killing the child: the caller waits at
most REGEX_MATCH_TIMEOUT_SECONDS whatever the pattern.
"""

import logging
import multiprocessing
import re
import threading
from multiprocessing.connection import Connection
from typing import Any

logger = logging.getLogger(__name__)

REGEX_MATCH_TIMEOUT_SECONDS = 0.25
# A fresh child imports the bot's entry module under spawn; allow for that.
_START_TIMEOUT_SECONDS = 30.0


def _serve(conn: Connection) -> None:
    """Child loop: compile on first use, answer each (pattern, texts) with a bool."""
    compiled: dict[str, re.Pattern[str]] = {}
    conn.send("ready")
    while True:
        try:
            pattern, texts = conn.recv()
        except EOFError:
            return
        try:
            regex = compiled.get(pattern)
            if regex is None:
                regex = compiled[pattern] = re.compile(pattern, re.IGNORECASE)
            conn.send(any(regex.search(text) for text in texts))
        except re.error:
            conn.send(False)


class RegexWorkerError(RuntimeError):
    """The worker could not be started or kept alive; says nothing about the pattern."""


class RegexWorker:
    """One child process that searches with a deadline; killed and replaced on overrun."""

    def __init__(self, timeout: float = REGEX_MATCH_TIMEOUT_SECONDS) -> None:
        self._timeout = timeout
        self._lock = threading.Lock()
        # spawn, not fork: forking a process that runs Docker stream threads
        # can copy a held malloc or logging lock into the child.
        self._ctx = multiprocessing.get_context("spawn")
        self._process: Any = None
        self._conn: Connection | None = None

    def _start(self) -> Connection:
        parent, child = self._ctx.Pipe()
        process = self._ctx.Process(target=_serve, args=(child,), daemon=True, name="regex-worker")
        process.start()
        child.close()
        if not parent.poll(_START_TIMEOUT_SECONDS):
            process.kill()
            raise RuntimeError("regex worker did not start")
        parent.recv()
        self._process, self._conn = process, parent
        return parent

    def _stop(self) -> None:
        if self._process is not None:
            self._process.kill()
            self._process.join(timeout=5)
        if self._conn is not None:
            self._conn.close()
        self._process = self._conn = None

    def search(self, pattern: str, texts: tuple[str, ...]) -> bool | None:
        """Whether the pattern matches any text; None when the search ran out of time."""
        with self._lock:
            for attempt in (1, 2):  # a child that died between calls gets one restart
                try:
                    conn = self._conn or self._start()
                    conn.send((pattern, texts))
                    if conn.poll(self._timeout):
                        return bool(conn.recv())
                except (OSError, EOFError, RuntimeError) as e:
                    logger.error(f"Regex worker failed ({e}); restarting it")
                    self._stop()
                    if attempt == 2:
                        raise RegexWorkerError(str(e)) from e
                    continue
                self._stop()  # the child is still searching: kill it, start fresh next time
                return None
            raise AssertionError("unreachable")

    def close(self) -> None:
        with self._lock:
            self._stop()


_worker: RegexWorker | None = None
_worker_lock = threading.Lock()


def regex_worker() -> RegexWorker:
    """The process-wide worker, created on first use."""
    global _worker
    with _worker_lock:
        if _worker is None:
            _worker = RegexWorker()
        return _worker
