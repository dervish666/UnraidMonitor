from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiogram import Bot

from src.alerts.manager import AlertManager, ChatIdStore
from src.constants import ALERT_QUEUE_MAX, ALERT_SEND_DELAY, ERROR_DISPLAY_MAX_CHARS


logger = logging.getLogger(__name__)


class AlertManagerProxy:
    """Proxy that gets chat_id dynamically from ChatIdStore.

    Queues alerts if no chat ID is available yet, delivering them
    when the first /start command provides the chat ID.
    Includes a simple send lock to prevent Telegram rate limit abuse
    during alert storms.
    """

    MAX_QUEUED = ALERT_QUEUE_MAX
    _SEND_DELAY = ALERT_SEND_DELAY

    def __init__(self, bot: Bot, chat_id_store: ChatIdStore, error_display_max_chars: int = ERROR_DISPLAY_MAX_CHARS) -> None:
        self.bot = bot
        self.chat_id_store = chat_id_store
        self.error_display_max_chars = error_display_max_chars
        self._queued_alerts: list[tuple[str, dict[str, Any]]] = []
        self._managers: dict[int, AlertManager] = {}
        self._send_lock = asyncio.Lock()

    @property
    def queued_count(self) -> int:
        return len(self._queued_alerts)

    def _get_manager(self, chat_id: int) -> AlertManager:
        """Get or create a cached AlertManager for the given chat_id."""
        if chat_id not in self._managers:
            self._managers[chat_id] = AlertManager(
                self.bot, chat_id, error_display_max_chars=self.error_display_max_chars
            )
        return self._managers[chat_id]

    async def _send_alert(self, method_name: str, **kwargs: Any) -> None:
        """Generic alert sender that delegates to AlertManager.

        Sends to all known chat IDs. Uses a lock to serialize sends
        and a small delay between them, preventing Telegram rate limit
        abuse during alert storms.
        """
        chat_ids = self.chat_id_store.get_all_chat_ids()
        if chat_ids:
            async with self._send_lock:
                # Flush any queued alerts first
                if self._queued_alerts:
                    await self._flush_queue(chat_ids)
                for chat_id in chat_ids:
                    try:
                        manager = self._get_manager(chat_id)
                        await getattr(manager, method_name)(**kwargs)
                    except Exception as e:
                        logger.error(f"Failed to send alert to {chat_id}: {e}")
                await asyncio.sleep(self._SEND_DELAY)
        else:
            if len(self._queued_alerts) < self.MAX_QUEUED:
                self._queued_alerts.append((method_name, kwargs))
                logger.info(f"Queued {method_name.replace('_', ' ')} (no chat ID yet, {len(self._queued_alerts)} queued)")
            else:
                logger.warning(f"Alert queue full, dropping {method_name.replace('_', ' ')}")

    async def _flush_queue(self, chat_ids: set[int]) -> None:
        """Deliver all queued alerts to all chat IDs."""
        queued = self._queued_alerts[:]
        self._queued_alerts.clear()
        logger.info(f"Flushing {len(queued)} queued alerts to {len(chat_ids)} users")
        for method_name, kwargs in queued:
            for chat_id in chat_ids:
                try:
                    manager = self._get_manager(chat_id)
                    await getattr(manager, method_name)(**kwargs)
                except Exception as e:
                    logger.error(f"Failed to send queued alert to {chat_id}: {e}")

    async def send_crash_alert(
        self,
        container_name: str,
        exit_code: int,
        image: str,
        uptime_seconds: int | None = None,
        restart_loop_count: int | None = None,
    ) -> None:
        await self._send_alert(
            "send_crash_alert",
            container_name=container_name,
            exit_code=exit_code,
            image=image,
            uptime_seconds=uptime_seconds,
            restart_loop_count=restart_loop_count,
        )

    async def send_log_error_alert(
        self,
        container_name: str,
        error_line: str,
        suppressed_count: int = 0,
    ) -> None:
        await self._send_alert(
            "send_log_error_alert",
            container_name=container_name,
            error_line=error_line,
            suppressed_count=suppressed_count,
        )

    async def send_resource_alert(
        self,
        container_name: str,
        metric: str,
        current_value: float,
        threshold: int,
        duration_seconds: int,
        memory_bytes: int,
        memory_limit: int,
        memory_percent: float,
        cpu_percent: float,
    ) -> None:
        await self._send_alert(
            "send_resource_alert",
            container_name=container_name,
            metric=metric,
            current_value=current_value,
            threshold=threshold,
            duration_seconds=duration_seconds,
            memory_bytes=memory_bytes,
            memory_limit=memory_limit,
            memory_percent=memory_percent,
            cpu_percent=cpu_percent,
        )

    async def send_recovery_alert(self, container_name: str) -> None:
        await self._send_alert("send_recovery_alert", container_name=container_name)

    async def send_health_alert(self, container_name: str, health_status: str) -> None:
        await self._send_alert(
            "send_health_alert",
            container_name=container_name,
            health_status=health_status,
        )
