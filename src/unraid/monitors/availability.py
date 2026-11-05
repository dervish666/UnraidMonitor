"""Shared "is the Unraid API answering" ladder for the system and array monitors.

Same shape as UpsMonitor's: alert once after N consecutive failed polls, once
when polls succeed again. A monitor that cannot reach its source and says
nothing is indistinguishable from one watching a healthy server.

Each poller keeps its own streak. A single shared counter let the 30-second
system poll reset it, so an array query failing every time (a GraphQL error
on that query alone) never alerted and /health read fresh.
"""

import logging
from typing import Any, Awaitable, Callable, TYPE_CHECKING

from src.constants import UNRAID_UNAVAILABLE_AFTER_FAILURES
from src.utils.poll_health import PollHealth

if TYPE_CHECKING:
    from src.alerts.server_mute_manager import ServerMuteManager

logger = logging.getLogger(__name__)


class UnraidAvailability:
    """Per-poller failure streaks, one deduplicated outage alert and recovery."""

    def __init__(
        self,
        on_alert: Callable[..., Awaitable[bool]],
        mute_manager: "ServerMuteManager",
        threshold: int = UNRAID_UNAVAILABLE_AFTER_FAILURES,
    ) -> None:
        self._on_alert = on_alert
        self._mute_manager = mute_manager
        self._threshold = threshold
        self.pollers: dict[str, PollHealth] = {}
        # Set only once the outage alert was actually delivered, so a send
        # that failed (or a mute) leaves the next poll to try again.
        self._outage_announced = False

    def poll(self, poller: str) -> PollHealth:
        return self.pollers.setdefault(poller, PollHealth())

    def failing(self) -> list[str]:
        """Pollers whose streak has reached the threshold, in first-seen order."""
        return [
            name for name, health in self.pollers.items()
            if health.consecutive_failures >= self._threshold
        ]

    @property
    def available(self) -> bool:
        return not self.failing()

    async def record_success(self, poller: str) -> None:
        self.poll(poller).ok()
        if self._outage_announced and self.available and not self._mute_manager.is_server_muted():
            if await self._send("Unraid API Restored", "Talking to the Unraid API again."):
                self._outage_announced = False

    async def record_failure(self, poller: str, error: Any) -> None:
        health = self.poll(poller)
        health.failed(error)
        if health.consecutive_failures < self._threshold or self._outage_announced:
            return
        if self._mute_manager.is_server_muted():
            return
        logger.warning(
            f"Unraid {poller} poll unavailable after {health.consecutive_failures} tries: {error}"
        )
        if await self._send(
            "Unraid API Unavailable",
            f"{health.consecutive_failures} {poller} polls in a row failed.\n"
            f"Last error: {error}\n\n"
            f"Array, disk and system state are unknown, not healthy.",
        ):
            self._outage_announced = True

    async def _send(self, title: str, message: str) -> bool:
        try:
            return bool(await self._on_alert(title=title, message=message, alert_type="server"))
        except Exception as e:
            logger.error(f"Failed to send Unraid availability alert: {e}")
            return False
