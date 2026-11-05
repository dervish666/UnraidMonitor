"""Relay of Unraid's own notification feed into Telegram.

Unraid already computes every warning it knows about -- SMART failures, disk
errors, share-full, parity results, plugin updates -- and shows them behind the
bell icon in the web UI. This forwards the ones at or above a configured
importance so they land on your phone with everything else.

Opt-in and floored at WARNING by default: the feed carries a lot of routine INFO
chatter (backup finished, parity-check tuning pausing and resuming) that would
be noise in a chat window.
"""

import asyncio
import logging
import os
from typing import Any, Awaitable, Callable, TYPE_CHECKING

from src.constants import (
    NOTIFICATION_DEDUP_HISTORY,
    NOTIFICATION_IMPORTANCE_LEVELS,
    NOTIFICATION_MAX_PER_POLL,
)
from src.utils.json_store import atomic_write_json, load_json, log_rejected

if TYPE_CHECKING:
    from src.alerts.server_mute_manager import ServerMuteManager
    from src.config import UnraidConfig
    from src.unraid.client import UnraidClientWrapper

logger = logging.getLogger(__name__)

_IMPORTANCE_EMOJI = {"ALERT": "🔴", "WARNING": "⚠️", "INFO": "ℹ️"}


def _importance_rank(importance: str) -> int:
    """Rank an importance, treating anything unrecognised as most urgent.

    An unknown value from a future Unraid release should reach the user rather
    than be silently dropped by a filter that has never heard of it.
    """
    try:
        return NOTIFICATION_IMPORTANCE_LEVELS.index(importance.upper())
    except ValueError:
        logger.warning(f"Unrecognised notification importance {importance!r}; treating as urgent")
        return len(NOTIFICATION_IMPORTANCE_LEVELS)


def _load_seen(path: str) -> list[str]:
    """Load previously-relayed notification ids; a missing file yields [].

    A file that is not a JSON list is set aside, not overwritten.
    """
    data = load_json(path, list, "relayed notifications")
    if data is None:
        return []
    rejected = [repr(x)[:20] for x in data if not isinstance(x, (str, int)) or isinstance(x, bool)]
    log_rejected(path, rejected)
    return [str(x) for x in data if isinstance(x, (str, int)) and not isinstance(x, bool)]

def _save_seen(path: str, seen: list[str]) -> None:
    """Persist relayed ids atomically; failures are logged, never raised."""
    try:
        atomic_write_json(path, seen)
    except (OSError, TypeError) as e:
        logger.error(f"Could not persist relayed notifications to {path}: {e}")



class UnraidNotificationMonitor:
    """Polls Unraid's notification feed and forwards new ones above a floor."""

    def __init__(
        self,
        client: "UnraidClientWrapper",
        config: "UnraidConfig",
        on_alert: Callable[..., Awaitable[bool]],
        mute_manager: "ServerMuteManager | None" = None,
        state_path: str | None = None,
    ) -> None:
        """Initialize the notification relay.

        Args:
            client: Connected UnraidClientWrapper.
            config: Unraid config; ``notifications_min_importance`` is read on
                every poll so /manage changes apply without a restart.
            on_alert: Async callback (title, message, alert_type).
            mute_manager: Server mute manager; relayed alerts respect it.
            state_path: JSON file for the relayed-id dedup list.
        """
        self._client = client
        self._config = config
        self._on_alert = on_alert
        self._mute_manager = mute_manager
        self._running = False
        self._state_path = state_path
        self._seen: list[str] = _load_seen(state_path) if state_path else []
        self._seen_set: set[str] = set(self._seen)
        # Primed once a state file exists, even an empty one. Keying on a
        # non-empty list re-primed on every restart when the feed was empty,
        # silently marking anything that arrived meanwhile as seen.
        self._primed = bool(self._seen) or bool(state_path and os.path.exists(state_path))

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        """Run the polling loop until stop() is called."""
        if self._running:
            return
        self._running = True
        logger.info(
            f"Unraid notification relay started "
            f"(>= {self._config.notifications_min_importance}, "
            f"every {self._config.poll_notifications_seconds}s)"
        )
        while self._running:
            try:
                await self.check_once()
            except Exception as e:
                logger.error(f"Notification relay check failed: {e}")
            await asyncio.sleep(self._config.poll_notifications_seconds)

    def stop(self) -> None:
        self._running = False
        logger.info("Unraid notification relay stopped")

    async def check_once(self) -> int:
        """Poll once and relay anything new above the floor.

        Returns:
            The number of notifications relayed.
        """
        try:
            notifications = await self._client.get_notifications()
        except Exception as e:
            logger.error(f"Failed to fetch Unraid notifications: {e}")
            return 0

        # First run on a fresh install: record what's already there rather than
        # replaying a backlog of old notifications into the chat.
        if not self._primed:
            self._primed = True
            self._remember([n.get("id", "") for n in notifications if n.get("id")])
            logger.info(f"Primed notification relay with {len(notifications)} existing notification(s)")
            if self._state_path:
                await asyncio.to_thread(_save_seen, self._state_path, list(self._seen))
            return 0

        floor = _importance_rank(self._config.notifications_min_importance)
        fresh = [
            n for n in notifications
            if n.get("id")
            and n["id"] not in self._seen_set
            and _importance_rank(str(n.get("importance") or "")) >= floor
        ]
        if not fresh:
            return 0

        # Oldest first, so a burst reads in chronological order.
        fresh.reverse()

        if self._mute_manager is not None and self._mute_manager.is_server_muted():
            # Leave them unseen: marking them here lost a SMART alert that
            # arrived during an unrelated 24h CPU mute. They relay once the
            # mute ends.
            logger.debug(f"Server alerts muted, holding {len(fresh)} notification(s)")
            return 0

        overflow = len(fresh) - NOTIFICATION_MAX_PER_POLL
        to_send = fresh[:NOTIFICATION_MAX_PER_POLL]

        relayed = 0
        for notification in to_send:
            try:
                sent = await self._on_alert(
                    title=self._format_title(notification),
                    message=self._format_message(notification),
                    alert_type="server",
                )
            except Exception as e:
                logger.error(f"Failed to relay notification {notification.get('id')}: {e}")
                sent = False
            if not sent:
                # Don't mark as seen -- a failed send is retried next poll.
                continue
            self._remember([notification["id"]])
            relayed += 1

        if overflow > 0:
            # Never drop silently. The held-back ones stay unseen and go out
            # on later polls.
            logger.warning(f"{overflow} further notification(s) held back this poll")
            try:
                await self._on_alert(
                    title="🔔 More Unraid Notifications",
                    message=(
                        f"{overflow} further notification(s) are queued and will follow "
                        f"over the next few polls. The Unraid web UI has them all now."
                    ),
                    alert_type="server",
                )
            except Exception as e:
                logger.error(f"Failed to send notification overflow notice: {e}")

        if self._state_path:
            await asyncio.to_thread(_save_seen, self._state_path, list(self._seen))
        return relayed

    def _remember(self, ids: list[str]) -> None:
        """Record ids as relayed, keeping the history bounded."""
        for notification_id in ids:
            if notification_id in self._seen_set:
                continue
            self._seen.append(notification_id)
            self._seen_set.add(notification_id)
        if len(self._seen) > NOTIFICATION_DEDUP_HISTORY:
            dropped = self._seen[:-NOTIFICATION_DEDUP_HISTORY]
            self._seen = self._seen[-NOTIFICATION_DEDUP_HISTORY:]
            self._seen_set = set(self._seen)
            logger.debug(f"Trimmed {len(dropped)} old notification id(s) from dedup history")

    @staticmethod
    def _format_title(notification: dict[str, Any]) -> str:
        importance = str(notification.get("importance") or "").upper()
        emoji = _IMPORTANCE_EMOJI.get(importance, "🔔")
        title = notification.get("title") or "Unraid Notification"
        return f"{emoji} {title}"

    @staticmethod
    def _format_message(notification: dict[str, Any]) -> str:
        lines = []
        subject = (notification.get("subject") or "").strip()
        description = (notification.get("description") or "").strip()
        if subject:
            lines.append(subject)
        if description and description != subject:
            lines.append(description)
        when = notification.get("formattedTimestamp") or notification.get("timestamp")
        if when:
            lines.append(f"_{when}_")
        return "\n".join(lines) or "(no detail provided)"
