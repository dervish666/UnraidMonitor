from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any, Awaitable, Callable

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from src.constants import MUTE_MAINTENANCE_INTERVAL_SECONDS

from src.alerts.manager import ChatIdStore
from src.alerts.mute_manager import MuteManager
from src.alerts.rate_limiter import RateLimiter
from src.alert_proxy import AlertManagerProxy
from src.monitors.resource_monitor import ResourceMonitor
from src.utils.formatting import fit_callback, format_bytes
from src.utils.telegram_retry import send_with_retry


logger = logging.getLogger(__name__)


class LogErrorAlerts:
    """Log-error alerts with rate limiting; one lost to an outage is retried.

    The cooldown is reserved before the send so two lines racing in cannot
    both alert, and released again when the send fails. The failed alert is
    kept (one per container, at most MAX_PENDING) for ``retry_pending()``,
    which the Docker event monitor runs on its reconcile cadence.
    """

    MAX_PENDING = 20

    def __init__(
        self, mute_manager: MuteManager, rate_limiter: RateLimiter, alert_manager: AlertManagerProxy,
    ) -> None:
        self._mute_manager = mute_manager
        self._rate_limiter = rate_limiter
        self._alert_manager = alert_manager
        self._pending: dict[str, tuple[str, int]] = {}

    async def __call__(self, container_name: str, error_line: str) -> None:
        if self._mute_manager.is_muted(container_name):
            logger.debug(f"Suppressed log error alert for muted container: {container_name}")
            return
        if container_name in self._pending:
            # Already waiting on Telegram: the retry pass sends the newest
            # line, so an outage costs one failed send per pass, not one per
            # error line.
            self._pending[container_name] = (error_line, self._pending[container_name][1])
            self._rate_limiter.record_suppressed(container_name)
            return
        if not self._rate_limiter.should_alert(container_name):
            self._rate_limiter.record_suppressed(container_name)
            return
        await self._send(container_name, error_line)

    async def _send(self, container_name: str, error_line: str) -> bool:
        suppressed = self._rate_limiter.get_suppressed_count(container_name)
        self._rate_limiter.record_alert(container_name)
        if await self._alert_manager.send_log_error_alert(
            container_name=container_name, error_line=error_line, suppressed_count=suppressed,
        ):
            self._pending.pop(container_name, None)
            return True
        self._rate_limiter.cancel_alert(container_name, suppressed)
        # A newer line may have replaced the held one while this send waited.
        held = self._pending.pop(container_name, None)
        self._pending[container_name] = (held[0] if held else error_line, suppressed)
        while len(self._pending) > self.MAX_PENDING:
            dropped = next(iter(self._pending))
            del self._pending[dropped]
            logger.error(f"Dropped undelivered log error alert for {dropped}: too many pending")
        return False

    async def retry_pending(self) -> bool:
        """Retry held alerts, stopping (False) at the first that still fails.

        This runs on the Docker event task; a failed send costs the whole
        Telegram retry ladder, so one per pass keeps an outage from stalling
        auto-heal and health handling behind it.
        """
        for name, (error_line, _) in list(self._pending.items()):
            if self._mute_manager.is_muted(name) or not self._rate_limiter.should_alert(name):
                # Muted since, or a newer alert for it already went out.
                self._pending.pop(name, None)
                continue
            if not await self._send(name, error_line):
                return False
        return True


def make_log_error_handler(
    mute_manager: MuteManager,
    rate_limiter: RateLimiter,
    alert_manager: AlertManagerProxy,
) -> LogErrorAlerts:
    return LogErrorAlerts(mute_manager, rate_limiter, alert_manager)


def make_server_alert_handler(
    chat_id_store: ChatIdStore,
    bot: Bot,
    config: Any,
    escape_markdown_fn: Callable[[str], str],
    resource_monitor_ref: list[ResourceMonitor | None],
    array_mute_manager: Any = None,
    server_mute_manager: Any = None,
    unraid_config: Any = None,
) -> Callable[[str, str, str], Awaitable[bool]]:
    """Returns True when at least one chat received the alert.

    Monitors commit their dedup/cooldown state only on True, so an alert that
    hit a Telegram outage is retried on the next poll instead of lost.
    """
    async def on_server_alert(title: str, message: str, alert_type: str) -> bool:
        chat_ids = chat_id_store.get_all_chat_ids()
        if not chat_ids:
            logger.warning("No chat ID yet, cannot send server alert")
            return False

        safe_title = escape_markdown_fn(title)
        if alert_type == "recovery":
            prefix = "✅ *RECOVERED:*"
        elif alert_type == "ups":
            prefix = "\U0001f50c *UPS ALERT:*"
        else:
            prefix = "\U0001f5a5️ *SERVER ALERT:*"
        alert_text = f"{prefix} {safe_title}\n\n{message}"
        keyboard = None

        # Enhance memory alerts with per-container stats and kill buttons
        if title == "Memory Critical" and resource_monitor_ref[0] is not None:
            try:
                all_stats = await asyncio.wait_for(
                    resource_monitor_ref[0].get_all_stats(), timeout=5.0
                )
                # Sort by memory usage descending, take top 5
                all_stats.sort(key=lambda s: s.memory_bytes, reverse=True)
                top = all_stats[:5]

                if top:
                    top_text = ", ".join(f"{s.name} ({s.memory_display})" for s in top)
                    alert_text += f"\n\nTop memory: {top_text}"

                    protected = set(config.protected_containers or [])
                    buttons = []
                    for s in top:
                        if s.name not in protected:
                            label = f"⏹ Stop {s.name} ({s.memory_display})"
                            buttons.append(
                                [InlineKeyboardButton(
                                    text=label, callback_data=fit_callback("mem_kill:", s.name)
                                )]
                            )
                    if buttons:
                        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
            except Exception as e:
                logger.warning(f"Failed to get container stats for server alert: {e}")

        # UPS alerts get their own mute category, so silencing a noisy UPS
        # does not also silence CPU and array alerts.
        if alert_type == "ups" and server_mute_manager is not None:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🔇 Mute 1h", callback_data="ups_mute:60"),
                InlineKeyboardButton(text="🔇 Mute 24h", callback_data="ups_mute:1440"),
            ]])

        # Add mute and threshold buttons to array alerts
        if alert_type == "array" and array_mute_manager is not None:
            arr_buttons: list[list[InlineKeyboardButton]] = [
                [
                    InlineKeyboardButton(text="🔇 Mute 1h", callback_data="arr_mute:60"),
                    InlineKeyboardButton(text="🔇 Mute 24h", callback_data="arr_mute:1440"),
                ],
            ]
            if unraid_config is not None:
                if "Capacity Warning" in title:
                    current = unraid_config.array_usage_threshold
                    arr_buttons.append([InlineKeyboardButton(
                        text=f"⚙️ Adjust Threshold ({current}%)",
                        callback_data=f"arr_thresh:capacity:{current}",
                    )])
                elif "High Temperature" in title:
                    current = unraid_config.disk_temp_threshold
                    arr_buttons.append([InlineKeyboardButton(
                        text=f"⚙️ Adjust Threshold ({current}°C)",
                        callback_data=f"arr_thresh:disk_temp:{current}",
                    )])
            keyboard = InlineKeyboardMarkup(inline_keyboard=arr_buttons)

        # Add mute and threshold buttons to CPU server alerts
        if alert_type == "server" and title != "Memory Critical" and keyboard is None:
            srv_buttons: list[list[InlineKeyboardButton]] = [
                [
                    InlineKeyboardButton(text="🔇 Mute 1h", callback_data="srv_mute:60"),
                    InlineKeyboardButton(text="🔇 Mute 24h", callback_data="srv_mute:1440"),
                ],
            ]
            if unraid_config is not None:
                if "Temperature" in title:
                    current = unraid_config.cpu_temp_threshold
                    srv_buttons.append([InlineKeyboardButton(
                        text=f"⚙️ Adjust Threshold ({current}°C)",
                        callback_data=f"srv_thresh:cpu_temp:{current}",
                    )])
                elif "CPU Usage" in title:
                    current = unraid_config.cpu_usage_threshold
                    srv_buttons.append([InlineKeyboardButton(
                        text=f"⚙️ Adjust Threshold ({current}%)",
                        callback_data=f"srv_thresh:cpu_usage:{current}",
                    )])
            keyboard = InlineKeyboardMarkup(inline_keyboard=srv_buttons)

        delivered = 0
        for cid in chat_ids:
            try:
                await send_with_retry(
                    bot.send_message, chat_id=cid, text=alert_text, parse_mode="Markdown", reply_markup=keyboard
                )
                delivered += 1
            except Exception as e:
                logger.error(f"Failed to send server alert to {cid}: {e}")
        return delivered > 0
    return on_server_alert


def make_memory_alert_handler(
    chat_id_store: ChatIdStore,
    bot: Bot,
    escape_markdown_fn: Callable[[str], str],
) -> Callable[[str, str, str, list[tuple[str, int | None]], list[tuple[str, int | None]]], Awaitable[None]]:
    async def on_memory_alert(
        title: str,
        message: str,
        alert_type: str,
        killable: list[tuple[str, int | None]],
        restartable: list[tuple[str, int | None]],
    ) -> None:
        chat_ids = chat_id_store.get_all_chat_ids()
        if not chat_ids:
            return

        emoji = "\U0001f534" if "Critical" in title else "⚠️"
        alert_text = f"{emoji} *{escape_markdown_fn(title)}*\n\n{message}"
        keyboard = None

        # Memory usage per container is supplied by the MemoryMonitor, so this
        # is robust even when the resource monitor is disabled or Docker is
        # slow under pressure. Both lists arrive sorted largest-first.
        restart_rows = [
            [InlineKeyboardButton(
                text="🔄 Restart " + name + (f" ({format_bytes(mem)})" if mem else ""),
                callback_data=fit_callback("mem_restart:", name),
            )]
            for name, mem in restartable
        ]

        if alert_type == "warning" and (killable or restartable):
            # Restart first: it's the gentler action and usually the fix.
            buttons = list(restart_rows)
            for name, mem in killable:
                label = f"⏹ Stop {name}" + (f" ({format_bytes(mem)})" if mem else "")
                buttons.append(
                    [InlineKeyboardButton(text=label, callback_data=fit_callback("mem_kill:", name))]
                )
            keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

        elif alert_type == "critical" and (killable or restartable):
            buttons = []
            if killable:
                target, mem = killable[0]
                kill_label = f"⏹ Kill {target} Now" + (f" ({format_bytes(mem)})" if mem else "")
                buttons.append([InlineKeyboardButton(text=kill_label, callback_data=fit_callback("mem_kill:", target))])
                buttons.append([InlineKeyboardButton(text="❌ Cancel Auto-Kill", callback_data="mem_cancel_kill")])
            buttons.extend(restart_rows)
            keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

        for cid in chat_ids:
            try:
                try:
                    await send_with_retry(
                        bot.send_message,
                        chat_id=cid, text=alert_text, parse_mode="Markdown", reply_markup=keyboard,
                    )
                except TelegramBadRequest:
                    # Container names can contain Markdown special characters
                    # (underscores are common) -- never let a parse failure
                    # eat a memory alert. Resend as plain text.
                    await send_with_retry(
                        bot.send_message,
                        chat_id=cid, text=alert_text.replace("*", ""), reply_markup=keyboard,
                    )
            except Exception as e:
                logger.error(f"Failed to send memory alert to {cid}: {e}")
    return on_memory_alert


def make_ask_restart_handler(
    chat_id_store: ChatIdStore,
    bot: Bot,
    escape_markdown_fn: Callable[[str], str],
) -> Callable[[str], Awaitable[None]]:
    async def on_ask_restart(container: str) -> None:
        safe_name = escape_markdown_fn(container)
        for cid in chat_id_store.get_all_chat_ids():
            text = f"\U0001f4be Memory now at safe levels. Restart *{safe_name}*?"
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Yes", callback_data=fit_callback("mem_restart_yes:", container)),
                    InlineKeyboardButton(text="❌ No", callback_data=fit_callback("mem_restart_no:", container)),
                ]
            ])
            try:
                await send_with_retry(
                    bot.send_message, chat_id=cid, text=text, parse_mode="Markdown", reply_markup=keyboard
                )
            except Exception as e:
                logger.error(f"Failed to send restart prompt to {cid}: {e}")
    return on_ask_restart


_SERVER_MUTE_PREFIX = {"server": "srv_mute", "array": "arr_mute", "ups": "ups_mute"}
_SERVER_MUTE_LABEL = {"server": "system", "array": "array", "ups": "UPS"}


def _remute_keyboard(
    mgr: Any, key: str,
) -> InlineKeyboardMarkup | None:
    """Build re-mute buttons appropriate to the manager type."""
    from src.alerts.server_mute_manager import ServerMuteManager

    if isinstance(mgr, ServerMuteManager):
        prefix = _SERVER_MUTE_PREFIX.get(key)
        if prefix is None:
            return None
    else:
        # Minutes, not seconds -- mute_callback clamps rather than rejects, so
        # 3600 here silently meant a 60-hour mute.
        prefix = f"mute:{key}"

    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔇 Re-mute 1h", callback_data=f"{prefix}:60"),
        InlineKeyboardButton(text="🔇 Re-mute 24h", callback_data=f"{prefix}:1440"),
    ]])


def _expiry_notices(mgr: Any, expired: list[str]) -> list[tuple[str, InlineKeyboardMarkup | None]]:
    """One notice per expired mute, except server categories expiring together.

    /mute-server sets all three categories at once, and three "Mute expired"
    messages for one command read as a glitch.
    """
    from src.alerts.server_mute_manager import ServerMuteManager

    if isinstance(mgr, ServerMuteManager) and len(expired) > 1:
        names = ", ".join(_SERVER_MUTE_LABEL.get(k, k) for k in expired)
        return [(f"Mute expired for {names} alerts", None)]
    if isinstance(mgr, ServerMuteManager):
        return [
            (f"Mute expired for {_SERVER_MUTE_LABEL.get(k, k)} alerts", _remute_keyboard(mgr, k))
            for k in expired
        ]
    return [(f"Mute expired for {k}", _remute_keyboard(mgr, k)) for k in expired]


def make_mute_maintenance_loop(
    mute_managers: list[Any],
    alert_manager: AlertManagerProxy,
    chat_id_store: ChatIdStore,
    bot: Bot,
    escape_markdown_fn: Callable[[str], str],
) -> Callable[[], Coroutine[Any, Any, None]]:
    async def _mute_maintenance_loop() -> None:
        while True:
            await asyncio.sleep(MUTE_MAINTENANCE_INTERVAL_SECONDS)
            for mgr in mute_managers:
                try:
                    mgr.flush()
                    expired = mgr.drain_expired()
                    if expired and alert_manager:
                        for notice, keyboard in _expiry_notices(mgr, expired):
                            for cid in chat_id_store.get_all_chat_ids():
                                try:
                                    await send_with_retry(
                                        bot.send_message,
                                        chat_id=cid,
                                        text=f"\U0001f514 {escape_markdown_fn(notice)}",
                                        parse_mode="Markdown",
                                        reply_markup=keyboard,
                                    )
                                except Exception as e:
                                    logger.error(f"Failed to send mute expiry notification: {e}")
                except Exception as e:
                    logger.error(f"Mute maintenance error: {e}")
    return _mute_maintenance_loop
