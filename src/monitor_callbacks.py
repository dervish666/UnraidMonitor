from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any, Awaitable, Callable

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from src.constants import MUTE_MAINTENANCE_INTERVAL_SECONDS

from src.alerts.manager import ChatIdStore
from src.alerts.mute_manager import MuteManager
from src.alerts.rate_limiter import RateLimiter
from src.alert_proxy import AlertManagerProxy
from src.monitors.resource_monitor import ResourceMonitor
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
    resource_monitor: ResourceMonitor | None,
    escape_markdown_fn: Callable[[str], str],
) -> Callable[[str, str, str, list[str]], Awaitable[None]]:
    async def on_memory_alert(
        title: str, message: str, alert_type: str, killable_names: list[str]
    ) -> None:
        chat_ids = chat_id_store.get_all_chat_ids()
        if not chat_ids:
            return

        emoji = "\U0001f534" if "Critical" in title else "⚠️"
        alert_text = f"{emoji} *{escape_markdown_fn(title)}*\n\n{message}"
        keyboard = None

        if alert_type in ("warning", "critical") and killable_names:
            # Try to get per-container memory stats for button labels
            stats_by_name: dict[str, str] = {}
            if resource_monitor is not None:
                try:
                    all_stats = await asyncio.wait_for(
                        resource_monitor.get_all_stats(), timeout=5.0
                    )
                    stats_by_name = {s.name: s.memory_display for s in all_stats}
                except Exception:
                    pass  # Graceful degradation -- buttons without memory info

            if alert_type == "warning":
                buttons = []
                for name in killable_names:
                    mem = stats_by_name.get(name, "")
                    label = f"⏹ Stop {name}" + (f" ({mem})" if mem else "")
                    buttons.append(
                        [InlineKeyboardButton(text=label, callback_data=f"mem_kill:{name}")]
                    )
                if buttons:
                    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

            elif alert_type == "critical":
                target = killable_names[0]
                mem = stats_by_name.get(target, "")
                kill_label = f"⏹ Kill {target} Now" + (f" ({mem})" if mem else "")
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text=kill_label, callback_data=f"mem_kill:{target}")],
                    [InlineKeyboardButton(text="❌ Cancel Auto-Kill", callback_data="mem_cancel_kill")],
                ])

        for cid in chat_ids:
            try:
                await send_with_retry(
                    bot.send_message,
                    chat_id=cid, text=alert_text, parse_mode="Markdown", reply_markup=keyboard,
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
                            for cid in chat_id_store.get_all_chat_ids():
                                try:
                                    await send_with_retry(
                                        bot.send_message,
                                        chat_id=cid,
                                        text=f"\U0001f514 Mute expired for *{escape_markdown_fn(key)}*",
                                        parse_mode="Markdown",
                                    )
                                except Exception as e:
                                    logger.error(f"Failed to send mute expiry notification: {e}")
                except Exception as e:
                    logger.error(f"Mute maintenance error: {e}")
    return _mute_maintenance_loop
