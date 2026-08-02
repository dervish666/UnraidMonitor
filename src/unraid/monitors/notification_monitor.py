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
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Awaitable, Callable, TYPE_CHECKING

from src.constants import (
    NOTIFICATION_DEDUP_HISTORY,
    NOTIFICATION_IMPORTANCE_LEVELS,
    NOTIFICATION_MAX_PER_POLL,
)

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
    """Load previously-relayed notification ids; missing/corrupt files yield []."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [str(x) for x in data]
    except FileNotFoundError:
        pass
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Could not read relayed notifications from {path}: {e}")
    return []


def _save_seen(path: str, seen: list[str]) -> None:
    """Persist relayed ids atomically; failures are logged, never raised."""
    try:
        parent = Path(path).parent
        parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(parent), prefix=".tmp_notifs_", suffix=".json")
        try:
            os.fchmod(fd, 0o644)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(seen, f)
            os.replace(tmp, path)
        except Exception:
            os.unlink(tmp)
            raise
    except Exception as e:
        logger.error(f"Could not persist relayed notifications to {path}: {e}")


class UnraidNotificationMonitor:
    """Polls Unraid's notification feed and forwards new ones above a floor."""

    def __init__(
        self,
        client: "UnraidClientWrapper",
        config: "UnraidConfig",
        on_alert: Callable[..., Awaitable[None]],
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
        self._primed = bool(self._seen)

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

        muted = self._mute_manager is not None and self._mute_manager.is_server_muted()
        overflow = len(fresh) - NOTIFICATION_MAX_PER_POLL
        to_send = fresh[:NOTIFICATION_MAX_PER_POLL]

        relayed = 0
        for notification in to_send:
            if not muted:
                try:
                    await self._on_alert(
                        title=self._format_title(notification),
                        message=self._format_message(notification),
                        alert_type="server",
                    )
                except Exception as e:
                    # Don't mark as seen -- a failed send should be retried.
                    logger.error(f"Failed to relay notification {notification.get('id')}: {e}")
                    continue
            self._remember([notification["id"]])
            relayed += 1

        if overflow > 0:
            # Never drop silently.
            logger.warning(f"{overflow} further notification(s) held back this poll")
            if not muted:
                await self._on_alert(
                    title="🔔 More Unraid Notifications",
                    message=(
                        f"{overflow} further notification(s) this cycle were not sent "
                        f"to avoid flooding the chat. Check the Unraid web UI."
                    ),
                    alert_type="server",
                )

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
