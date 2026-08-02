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
from src.utils.formatting import format_bytes
from src.utils.telegram_retry import send_with_retry


logger = logging.getLogger(__name__)


def make_log_error_handler(
    mute_manager: MuteManager,
    rate_limiter: RateLimiter,
    alert_manager: AlertManagerProxy,
) -> Callable[[str, str], Awaitable[None]]:
    async def on_log_error(container_name: str, error_line: str) -> None:
        """Handle log errors with rate limiting."""
        # Check if muted
        if mute_manager.is_muted(container_name):
            logger.debug(f"Suppressed log error alert for muted container: {container_name}")
            return

        if rate_limiter.should_alert(container_name):
            suppressed = rate_limiter.get_suppressed_count(container_name)
            # Record alert BEFORE the await to prevent TOCTOU duplicate alerts
            rate_limiter.record_alert(container_name)
            await alert_manager.send_log_error_alert(
                container_name=container_name,
                error_line=error_line,
                suppressed_count=suppressed,
            )
        else:
            rate_limiter.record_suppressed(container_name)
    return on_log_error


def make_server_alert_handler(
    chat_id_store: ChatIdStore,
    bot: Bot,
    config: Any,
    escape_markdown_fn: Callable[[str], str],
    resource_monitor_ref: list[ResourceMonitor | None],
    array_mute_manager: Any = None,
    server_mute_manager: Any = None,
    unraid_config: Any = None,
) -> Callable[[str, str, str], Awaitable[None]]:
    async def on_server_alert(title: str, message: str, alert_type: str) -> None:
        chat_ids = chat_id_store.get_all_chat_ids()
        if not chat_ids:
            logger.warning("No chat ID yet, cannot send server alert")
            return

        safe_title = escape_markdown_fn(title)
        alert_text = f"\U0001f5a5️ *SERVER ALERT:* {safe_title}\n\n{message}"
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
                                    text=label, callback_data=f"mem_kill:{s.name}"
                                )]
                            )
                    if buttons:
                        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
            except Exception as e:
                logger.warning(f"Failed to get container stats for server alert: {e}")

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

        for cid in chat_ids:
            try:
                await send_with_retry(
                    bot.send_message, chat_id=cid, text=alert_text, parse_mode="Markdown", reply_markup=keyboard
                )
            except Exception as e:
                logger.error(f"Failed to send server alert to {cid}: {e}")
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
                callback_data=f"mem_restart:{name}",
            )]
            for name, mem in restartable
        ]

        if alert_type == "warning" and (killable or restartable):
            # Restart first: it's the gentler action and usually the fix.
            buttons = list(restart_rows)
            for name, mem in killable:
                label = f"⏹ Stop {name}" + (f" ({format_bytes(mem)})" if mem else "")
                buttons.append(
                    [InlineKeyboardButton(text=label, callback_data=f"mem_kill:{name}")]
                )
            keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

        elif alert_type == "critical" and (killable or restartable):
            buttons = []
            if killable:
                target, mem = killable[0]
                kill_label = f"⏹ Kill {target} Now" + (f" ({format_bytes(mem)})" if mem else "")
                buttons.append([InlineKeyboardButton(text=kill_label, callback_data=f"mem_kill:{target}")])
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
                    InlineKeyboardButton(text="✅ Yes", callback_data=f"mem_restart_yes:{container}"),
                    InlineKeyboardButton(text="❌ No", callback_data=f"mem_restart_no:{container}"),
                ]
            ])
            try:
                await send_with_retry(
                    bot.send_message, chat_id=cid, text=text, parse_mode="Markdown", reply_markup=keyboard
                )
            except Exception as e:
                logger.error(f"Failed to send restart prompt to {cid}: {e}")
    return on_ask_restart


def _remute_keyboard(
    mgr: Any, key: str,
) -> InlineKeyboardMarkup | None:
    """Build re-mute buttons appropriate to the manager type."""
    from src.alerts.array_mute_manager import ArrayMuteManager
    from src.alerts.server_mute_manager import ServerMuteManager

    if isinstance(mgr, ArrayMuteManager):
        prefix = "arr_mute"
    elif isinstance(mgr, ServerMuteManager):
        prefix = "srv_mute"
    else:
        prefix = f"mute:{key}"
        # Minutes, not seconds -- mute_callback clamps rather than rejects, so
        # 3600 here silently meant a 60-hour mute.
        return InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🔇 Re-mute 1h", callback_data=f"{prefix}:60"),
            InlineKeyboardButton(text="🔇 Re-mute 24h", callback_data=f"{prefix}:1440"),
        ]])

    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔇 Re-mute 1h", callback_data=f"{prefix}:60"),
        InlineKeyboardButton(text="🔇 Re-mute 24h", callback_data=f"{prefix}:1440"),
    ]])


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
                        for key in expired:
                            keyboard = _remute_keyboard(mgr, key)
                            for cid in chat_id_store.get_all_chat_ids():
                                try:
                                    await send_with_retry(
                                        bot.send_message,
                                        chat_id=cid,
                                        text=f"\U0001f514 Mute expired for *{escape_markdown_fn(key)}*",
                                        parse_mode="Markdown",
                                        reply_markup=keyboard,
                                    )
                                except Exception as e:
                                    logger.error(f"Failed to send mute expiry notification: {e}")
                except Exception as e:
                    logger.error(f"Mute maintenance error: {e}")
    return _mute_maintenance_loop
