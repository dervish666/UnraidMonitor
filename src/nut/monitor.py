"""UPS monitoring on top of a NUT server.

Follows the same shape as the Unraid monitors: a long-lived poll loop, an
edge-triggered alert on each status change, and a cache the /ups command reads
so a button press does not open its own socket.

The design rule that matters here: a poll that could not reach upsd is never
reported as "UPS fine". It is tracked separately and surfaced as unavailable,
because a silent monitor is indistinguishable from a healthy one otherwise.
"""

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable, TYPE_CHECKING

from src.constants import (
    NUT_ALERT_FLAGS,
    NUT_UNAVAILABLE_AFTER_FAILURES,
)
from src.nut.client import (
    STATUS_MEANINGS,
    NutAuthError,
    NutClient,
    NutError,
)

if TYPE_CHECKING:
    from src.config import NutConfig
    from src.alerts.server_mute_manager import ServerMuteManager

logger = logging.getLogger(__name__)

# How long cached readings stay fresh for /ups before a new fetch.
_CACHE_TTL = 20  # seconds

# Replace-battery is a standing condition, not an event. Nag daily.
_REPLACE_BATTERY_COOLDOWN = 86400
# Floor between repeats of the same flag alert, so a flapping mains supply
# cannot turn into a message every poll.
_FLAG_COOLDOWN = 60
# Threshold alerts (load, battery charge) repeat no faster than this.
_THRESHOLD_COOLDOWN = 300


def format_runtime(seconds: str | float | int | None) -> str:
    """Render battery.runtime as a human span.

    NUT hands every variable over as a string, so this takes one.
    """
    if seconds is None:
        return "unknown"
    try:
        total = int(float(seconds))
    except (TypeError, ValueError):
        return "unknown"
    if total < 0:
        return "unknown"
    hours, remainder = divmod(total, 3600)
    minutes = remainder // 60
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    if minutes:
        return f"{minutes}m"
    return f"{total}s"


def format_duration(seconds: float) -> str:
    """Render an elapsed span for recovery messages."""
    total = int(seconds)
    if total < 60:
        return f"{total}s"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m {secs}s" if secs else f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m" if minutes else f"{hours}h"


def parse_status(raw: str | None) -> set[str]:
    """Split ups.status into its flags.

    It is an opaque string of space-separated tokens, and several are normally
    set at once ("OL TRIM CHRG" is a healthy UPS trimming voltage).
    """
    if not raw:
        return set()
    return {token for token in raw.split() if token}


def _as_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class UpsMonitor:
    """Polls a NUT server and alerts on UPS status changes."""

    def __init__(
        self,
        client: NutClient,
        config: "NutConfig",
        on_alert: Callable[..., Awaitable[None]],
        mute_manager: "ServerMuteManager",
    ) -> None:
        self._client = client
        self._config = config
        self._on_alert = on_alert
        self._mute_manager = mute_manager

        self._running = False
        # None until the first successful poll: "never reached it" and "lost
        # it" want different handling, so they are different states.
        self._available: bool | None = None
        self._consecutive_failures = 0
        self._last_error: str | None = None

        self._ups_name: str | None = None
        self._variables: dict[str, str] = {}
        self._fetched_at: float = 0.0

        self._known_flags: set[str] = set()
        self._on_battery_since: float | None = None
        self._last_alert_times: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_available(self) -> bool:
        """True only once a poll has actually succeeded and not since failed."""
        return self._available is True

    @property
    def last_error(self) -> str | None:
        return self._last_error

    @property
    def ups_name(self) -> str | None:
        return self._ups_name

    async def start(self) -> None:
        """Run the poll loop. Wrap in asyncio.create_task() from startup."""
        if self._running:
            return
        self._running = True
        logger.info(f"UPS monitor started against {self._client.target}")

        while self._running:
            try:
                await self.check_once()
            except Exception as e:
                logger.error(f"Error in UPS monitor: {e}")
            await asyncio.sleep(self._config.poll_seconds)

    async def stop(self) -> None:
        self._running = False
        logger.info("UPS monitor stopped")

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    async def check_once(self) -> dict[str, str] | None:
        """Poll once and alert on anything that changed.

        Returns:
            The variable map, or None when the server could not be read.
        """
        try:
            name, variables = await self._client.fetch(self._config.ups_name or None)
        except NutError as e:
            await self._handle_failure(e)
            return None

        self._consecutive_failures = 0
        self._last_error = None
        self._ups_name = name
        self._variables = variables
        self._fetched_at = time.monotonic()

        was_available = self._available
        self._available = True

        if self._mute_manager.is_ups_muted():
            logger.debug("UPS alerts muted, skipping checks")
            self._known_flags = parse_status(variables.get("ups.status"))
            return variables

        if was_available is False:
            await self._alert(
                "UPS Monitoring Restored",
                f"Talking to {self._client.target} again.\n"
                f"{self._describe_now(variables)}",
            )

        await self._check_status(variables)
        await self._check_thresholds(variables)
        self._prune_cooldowns()
        return variables

    async def _handle_failure(self, error: NutError) -> None:
        """Record a failed poll, and alert only if we had a working link."""
        self._consecutive_failures += 1
        self._last_error = str(error)

        if isinstance(error, NutAuthError):
            # Credentials will not fix themselves. Say so once, loudly, then
            # stop retrying so the log does not fill with the same rejection.
            logger.error(f"NUT authentication failed: {error}")
            if self._available is not False:
                self._available = False
                if not self._mute_manager.is_ups_muted():
                    await self._alert(
                        "UPS Monitoring Failed",
                        f"{self._client.target} rejected the credentials.\n"
                        f"Check NUT_USERNAME and NUT_PASSWORD.",
                    )
            self._running = False
            return

        if self._available is None:
            # Never connected. Most installs have no NUT server, so this is a
            # log line and nothing more.
            if self._consecutive_failures == 1:
                logger.info(
                    f"No NUT server at {self._client.target} ({error}). "
                    f"UPS monitoring stays idle; set nut.host or turn it off "
                    f"in /manage -> Features."
                )
            else:
                logger.debug(f"NUT still unreachable at {self._client.target}: {error}")
            return

        if (
            self._available is True
            and self._consecutive_failures >= NUT_UNAVAILABLE_AFTER_FAILURES
        ):
            self._available = False
            logger.warning(f"Lost contact with NUT server {self._client.target}: {error}")
            if not self._mute_manager.is_ups_muted():
                await self._alert(
                    "UPS Monitoring Unavailable",
                    f"Lost contact with {self._client.target} after "
                    f"{self._consecutive_failures} attempts.\n"
                    f"Last error: {error}\n\n"
                    f"UPS status is unknown, not healthy.",
                )
        else:
            logger.debug(
                f"NUT poll failed ({self._consecutive_failures}/"
                f"{NUT_UNAVAILABLE_AFTER_FAILURES}): {error}"
            )

    # ------------------------------------------------------------------
    # Checks
    # ------------------------------------------------------------------

    async def _check_status(self, variables: dict[str, str]) -> None:
        """Alert on flags that have just appeared, and on the return to mains."""
        flags = parse_status(variables.get("ups.status"))
        previous = self._known_flags
        appeared = flags - previous

        # Back on mains after a run on battery.
        if "OB" in previous and "OB" not in flags:
            if self._on_battery_since is not None:
                elapsed = format_duration(time.monotonic() - self._on_battery_since)
                body = f"Mains is back after {elapsed}."
            else:
                body = "Mains is back."
            self._on_battery_since = None
            await self._alert(
                "UPS Back On Mains",
                f"{body}\n{self._describe_now(variables)}",
            )

        if "OB" in appeared:
            self._on_battery_since = time.monotonic()

        for flag in NUT_ALERT_FLAGS:
            if flag not in appeared:
                continue
            cooldown = (
                _REPLACE_BATTERY_COOLDOWN if flag == "RB" else _FLAG_COOLDOWN
            )
            await self._rate_limited(
                f"flag:{flag}",
                cooldown,
                self._flag_title(flag),
                f"{STATUS_MEANINGS.get(flag, flag)}.\n{self._describe_now(variables)}",
            )

        self._known_flags = flags

    async def _check_thresholds(self, variables: dict[str, str]) -> None:
        """Warn on a draining battery and on a UPS carrying too much load."""
        flags = self._known_flags

        charge = _as_float(variables.get("battery.charge"))
        if (
            charge is not None
            and "OB" in flags
            and charge < self._config.battery_charge_threshold
        ):
            await self._rate_limited(
                "battery_charge",
                _THRESHOLD_COOLDOWN,
                "UPS Battery Low",
                f"Battery at {charge:.0f}% "
                f"(threshold: {self._config.battery_charge_threshold}%) while on battery.\n"
                f"Runtime left: {format_runtime(variables.get('battery.runtime'))}",
            )

        load = _as_float(variables.get("ups.load"))
        if load is not None and load > self._config.load_threshold:
            nominal = variables.get("ups.realpower.nominal")
            watts = ""
            nominal_value = _as_float(nominal)
            if nominal_value:
                watts = f" (about {nominal_value * load / 100:.0f}W of {nominal_value:.0f}W)"
            await self._rate_limited(
                "load",
                _THRESHOLD_COOLDOWN,
                "UPS Load High",
                f"Load at {load:.0f}%{watts} "
                f"(threshold: {self._config.load_threshold}%).\n"
                f"Runtime on battery would be shorter than usual.",
            )

    # ------------------------------------------------------------------
    # Reading for /ups and the alert bodies
    # ------------------------------------------------------------------

    async def get_snapshot(self, force: bool = False) -> dict[str, Any]:
        """Current UPS reading for display.

        Always returns a dict. When the server cannot be read it says so
        explicitly rather than returning an empty set of values that would
        read as a healthy UPS.
        """
        fresh = (time.monotonic() - self._fetched_at) < _CACHE_TTL
        if force or not fresh or not self._variables:
            try:
                name, variables = await self._client.fetch(self._config.ups_name or None)
                self._ups_name = name
                self._variables = variables
                self._fetched_at = time.monotonic()
                self._available = True
                self._last_error = None
                self._consecutive_failures = 0
            except NutError as e:
                self._last_error = str(e)
                if self._available is True:
                    self._available = False

        return {
            "available": self._available is True,
            "target": self._client.target,
            "ups": self._ups_name,
            "variables": dict(self._variables),
            "error": self._last_error,
            "age_seconds": (
                time.monotonic() - self._fetched_at if self._fetched_at else None
            ),
            "on_battery_since": self._on_battery_since,
        }

    def _describe_now(self, variables: dict[str, str]) -> str:
        """One-line summary appended to every UPS alert."""
        parts: list[str] = []
        status = variables.get("ups.status")
        if status:
            parts.append(f"Status: {status}")
        charge = _as_float(variables.get("battery.charge"))
        if charge is not None:
            parts.append(f"Battery: {charge:.0f}%")
        runtime = variables.get("battery.runtime")
        if runtime is not None:
            parts.append(f"Runtime: {format_runtime(runtime)}")
        load = _as_float(variables.get("ups.load"))
        if load is not None:
            parts.append(f"Load: {load:.0f}%")
        return " • ".join(parts) if parts else "No readings available."

    @staticmethod
    def _flag_title(flag: str) -> str:
        titles = {
            "OB": "UPS On Battery",
            "LB": "UPS Battery Critical",
            "RB": "UPS Battery Needs Replacing",
            "OVER": "UPS Overloaded",
            "BYPASS": "UPS On Bypass",
            "OFF": "UPS Output Off",
            "FSD": "UPS Forced Shutdown",
            "ALARM": "UPS Alarm",
        }
        return titles.get(flag, f"UPS Status {flag}")

    # ------------------------------------------------------------------
    # Alert plumbing
    # ------------------------------------------------------------------

    async def _alert(self, title: str, message: str) -> None:
        await self._on_alert(title=title, message=message, alert_type="ups")

    async def _rate_limited(
        self, key: str, cooldown: float, title: str, message: str
    ) -> None:
        now = time.monotonic()
        # -inf, not 0: on a freshly booted host time.monotonic() can be below
        # the cooldown, and 0 would swallow the very first alert.
        last = self._last_alert_times.get(key, float("-inf"))
        if now - last < cooldown:
            logger.debug(f"Suppressing duplicate UPS alert {key} (cooldown)")
            return
        self._last_alert_times[key] = now
        await self._alert(title, message)

    def _prune_cooldowns(self) -> None:
        cutoff = time.monotonic() - (_REPLACE_BATTERY_COOLDOWN * 2)
        for key in [k for k, t in self._last_alert_times.items() if t < cutoff]:
            del self._last_alert_times[key]

    def clear_alert_state(self) -> None:
        """Forget cooldowns and known flags, so an unmute re-reports reality."""
        self._last_alert_times.clear()
        self._known_flags = set()
