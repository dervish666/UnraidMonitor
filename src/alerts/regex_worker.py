"""Regex ignores run in a child process with a hard time limit.

Python's re has no timeout and holds the GIL while it searches, so a
backtracking pattern on a long log line used to stall the whole bot, and
static analysis of the pattern kept missing shapes (six review rounds, a new
bypass each time, and each tighter rule refused ordinary filters). A search
that overruns here is abandoned by killing the child: the caller waits at
most REGEX_MATCH_TIMEOUT_SECONDS per search whatever the pattern (plus about
0.1s to start a fresh child after an overrun).

The child is a bare ``python -I -c`` running the stdlib loop below, not a
multiprocessing spawn: spawn re-imports the bot's entry module (aiogram, the
LLM SDKs), which cost about 2s and 180 MiB per start, and a restart after an
overrun happens on the event loop.
"""

import json
import logging
import os
import select
import subprocess
import sys
import threading
import time

logger = logging.getLogger(__name__)

# Per search, one log line. A pattern that overruns pays this once and is then
# disabled, so the limit is generous: the slowest ordinary filter measured
# (\\w+\\s+\\w+x) takes about 40ms on a 3000-character line on an M1, and an
# Unraid CPU may be several times slower.
REGEX_MATCH_TIMEOUT_SECONDS = 0.5
_START_TIMEOUT_SECONDS = 10.0
# After the worker fails twice in a row, regex ignores are skipped (fail open)
# for this long instead of paying a start attempt on every log line.
_BROKEN_BACKOFF_SECONDS = 300.0

# One request per line: [pattern, [texts...]]. Every text is searched (so a
# validation probe that matches early cannot hide a later one that hangs);
# the reply is "1" if any matched, "0" otherwise or if the pattern is invalid.
_CHILD = r"""
import json, re, sys
compiled = {}
out = sys.stdout.buffer
out.write(b"ready\n"); out.flush()
for line in iter(sys.stdin.buffer.readline, b""):
    pattern, texts = json.loads(line)
    try:
        regex = compiled.get(pattern)
        if regex is None:
            regex = compiled[pattern] = re.compile(pattern, re.IGNORECASE)
        found = False
        for text in texts:
            if regex.search(text):
                found = True
    except re.error:
        found = False
    out.write(b"1\n" if found else b"0\n"); out.flush()
"""


class RegexWorkerError(RuntimeError):
    """The worker could not be started or kept alive; says nothing about the pattern."""


class RegexWorker:
    """One child process that searches with a deadline; killed and replaced on overrun."""

    def __init__(self, timeout: float = REGEX_MATCH_TIMEOUT_SECONDS) -> None:
        self._timeout = timeout
        self._lock = threading.Lock()
        self._proc: subprocess.Popen[bytes] | None = None
        self._broken_until = 0.0

    def _read_line(self, proc: subprocess.Popen[bytes], timeout: float) -> bytes | None:
        """One reply line, or None if it did not arrive in time."""
        assert proc.stdout is not None
        fd = proc.stdout.fileno()
        deadline = time.monotonic() + timeout
        data = b""
        while not data.endswith(b"\n"):
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([fd], [], [], remaining)[0]:
                return None
            chunk = os.read(fd, 64)
            if not chunk:
                raise EOFError("regex worker exited")
            data += chunk
        return data.strip()

    def _start(self) -> subprocess.Popen[bytes]:
        # Held in self._proc before the handshake, so a failed start is reaped by _stop().
        self._proc = proc = subprocess.Popen(
            [sys.executable, "-I", "-c", _CHILD],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0,
        )
        assert proc.stdin is not None
        # Requests go out with os.write against the same deadline as the
        # reply: a child that stopped reading cannot block the caller.
        os.set_blocking(proc.stdin.fileno(), False)
        if self._read_line(proc, _START_TIMEOUT_SECONDS) != b"ready":
            raise RegexWorkerError("regex worker did not start")
        return proc

    def _write_all(self, proc: subprocess.Popen[bytes], data: bytes, timeout: float) -> bool:
        """Send one request; False if the child did not take it in time."""
        assert proc.stdin is not None
        fd = proc.stdin.fileno()
        deadline = time.monotonic() + timeout
        view = memoryview(data)
        while view:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([], [fd], [], remaining)[1]:
                return False
            try:
                view = view[os.write(fd, view):]
            except BlockingIOError:
                continue
        return True

    def _stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            logger.error(f"Regex worker {proc.pid} did not exit after kill")
        for pipe in (proc.stdin, proc.stdout):
            if pipe is None:
                continue
            try:
                pipe.close()  # unbuffered, so nothing is left to flush into a dead pipe
            except OSError as e:
                logger.debug(f"Closing a regex worker pipe: {e!r}")

    def search(self, pattern: str, texts: tuple[str, ...]) -> bool | None:
        """Whether the pattern matches any text; None when the search ran out of time."""
        request = (json.dumps([pattern, list(texts)]) + "\n").encode()
        with self._lock:
            if time.monotonic() < self._broken_until:
                raise RegexWorkerError("regex worker unavailable")
            for attempt in (1, 2):  # a child that died between calls gets one restart
                try:
                    proc = self._proc or self._start()
                    started = time.monotonic()
                    sent = self._write_all(proc, request, self._timeout)
                    reply = (
                        self._read_line(proc, self._timeout - (time.monotonic() - started))
                        if sent else None
                    )
                except (OSError, EOFError, RegexWorkerError) as e:
                    logger.error(f"Regex worker failed ({e!r}); restarting it")
                    self._stop()
                    if attempt == 2:
                        self._broken_until = time.monotonic() + _BROKEN_BACKOFF_SECONDS
                        logger.error(
                            f"Regex ignores skipped for {_BROKEN_BACKOFF_SECONDS:.0f}s: worker unavailable"
                        )
                        raise RegexWorkerError(str(e)) from e
                    continue
                if reply is None:
                    self._stop()  # still searching: kill it; the next call starts a fresh one
                    return None
                return reply == b"1"
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
