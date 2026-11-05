"""Shared "is the Unraid API answering" ladder for the system and array monitors.

Same shape as UpsMonitor's: alert once after N consecutive failed polls, once
when a poll succeeds again. A monitor that cannot reach its source and says
nothing is indistinguishable from one watching a healthy server.
"""

import logging
import time
from typing import Any, Awaitable, Callable, TYPE_CHECKING

from src.constants import UNRAID_UNAVAILABLE_AFTER_FAILURES

if TYPE_CHECKING:
    from src.alerts.server_mute_manager import ServerMuteManager

logger = logging.getLogger(__name__)


class UnraidAvailability:
    """Counts consecutive failed Unraid API polls and alerts on the edges."""

    def __init__(
        self,
        on_alert: Callable[..., Awaitable[bool]],
        mute_manager: "ServerMuteManager",
        threshold: int = UNRAID_UNAVAILABLE_AFTER_FAILURES,
    ) -> None:
        self._on_alert = on_alert
        self._mute_manager = mute_manager
        self._threshold = threshold
        self.consecutive_failures = 0
        self.last_error: str | None = None
        self.last_success: float | None = None  # time.time()
        # Set only once the outage alert was actually delivered, so a send
        # that failed (or a mute) leaves the next poll to try again.
        self._outage_announced = False

    @property
    def available(self) -> bool:
        return self.consecutive_failures < self._threshold

    async def record_success(self) -> None:
        self.consecutive_failures = 0
        self.last_error = None
        self.last_success = time.time()
        if self._outage_announced and not self._mute_manager.is_server_muted():
            if await self._send("Unraid API Restored", "Talking to the Unraid API again."):
                self._outage_announced = False

    async def record_failure(self, error: Any) -> None:
        self.consecutive_failures += 1
        self.last_error = str(error)
        if self.consecutive_failures < self._threshold or self._outage_announced:
            return
        if self._mute_manager.is_server_muted():
            return
        logger.warning(f"Unraid API unavailable after {self.consecutive_failures} polls: {error}")
        if await self._send(
            "Unraid API Unavailable",
            f"{self.consecutive_failures} polls in a row failed.\n"
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
