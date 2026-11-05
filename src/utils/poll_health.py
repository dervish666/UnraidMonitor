"""What a monitor last saw, for /health.

"Loop running" says nothing about whether the loop is getting answers: a
monitor whose every poll fails is running too. Each monitor keeps one of these
and /health renders it.
"""

import time
from dataclasses import dataclass


def _ago(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def describe_poll(
    last_success: float | None,
    consecutive_failures: int,
    last_error: str | None,
    noun: str = "poll",
    now: float | None = None,
) -> str:
    """'last poll 2m ago', or '3 failed polls, last ok 1h ago: <error>'."""
    now = time.time() if now is None else now
    ok = f"last {noun} {_ago(now - last_success)}" if last_success else f"no {noun} yet"
    if not consecutive_failures:
        return ok
    plural = "" if consecutive_failures == 1 else "s"
    error = (last_error or "unknown error")[:80]
    if last_success:
        ok = f"last ok {_ago(now - last_success)}"
    return f"{consecutive_failures} failed {noun}{plural}, {ok}: {error}"


@dataclass
class PollHealth:
    """Last success (wall clock), failure streak and last error of one monitor."""

    noun: str = "poll"
    last_success: float | None = None
    consecutive_failures: int = 0
    last_error: str | None = None

    def ok(self) -> None:
        self.last_success = time.time()
        self.consecutive_failures = 0
        self.last_error = None

    def failed(self, error: object) -> None:
        self.consecutive_failures += 1
        self.last_error = str(error)

    def describe(self, now: float | None = None) -> str:
        return describe_poll(
            self.last_success, self.consecutive_failures, self.last_error, self.noun, now,
        )
