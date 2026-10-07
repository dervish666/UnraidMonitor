"""Unraid notification relay vs Telegram's Markdown parser.

Live on 2026-10-07 two disk-utilization notifications were lost with "can't
parse entities": their text carried bare underscores (Unraid_Disk_4_...), which
Markdown V1 reads as an unclosed italic. The relay sends through the shared
server-alert handler and send_with_retry, which every alert type uses, so the
fallback lives in send_with_retry.
"""

import re
from unittest.mock import AsyncMock, MagicMock

from aiogram.exceptions import TelegramBadRequest

from src.monitor_callbacks import make_server_alert_handler
from src.unraid.monitors.notification_monitor import UnraidNotificationMonitor
from src.utils.formatting import escape_markdown
from src.utils.telegram_retry import markdown_to_plain, send_with_retry


def _telegram_like_send() -> AsyncMock:
    """bot.send_message that rejects Markdown V1 with an unclosed underscore, as Telegram does."""

    async def send(**kwargs):
        if kwargs.get("parse_mode") == "Markdown":
            bare = re.sub(r"\\.", "", kwargs["text"])
            if bare.count("_") % 2 or bare.count("*") % 2:
                raise TelegramBadRequest(
                    method=MagicMock(),
                    message="Bad Request: can't parse entities: Can't find end of the entity",
                )
        return MagicMock()

    return AsyncMock(side_effect=send)


def _notification():
    return {
        "id": "45e4ab81:Unraid_Disk_4_disk_utilization_1790478543.notify",
        "importance": "WARNING",
        "title": "Unraid Disk 4 disk utilization",
        "subject": "Warning [TOWER] - Disk 4 is high on usage (91%)",
        "description": "WDC_WD80EFAX_68KNBN0_VAJ4 (sdf)",
        "formattedTimestamp": "7 Oct 2026 22:00",
    }


async def test_relayed_notification_with_underscores_is_delivered():
    bot = MagicMock()
    bot.send_message = _telegram_like_send()
    chat_store = MagicMock()
    chat_store.get_all_chat_ids.return_value = [9903160]
    handler = make_server_alert_handler(
        chat_store, bot, MagicMock(), escape_markdown, [None], raise_on_failure=True,
    )
    n = _notification()

    # raise_on_failure=True: the relay's "reached no chat" path must not fire
    await handler(
        UnraidNotificationMonitor._format_title(n),
        UnraidNotificationMonitor._format_message(n),
        "server",
    )

    assert bot.send_message.call_count == 2
    delivered = bot.send_message.call_args_list[-1].kwargs
    assert "parse_mode" not in delivered
    assert "WDC_WD80EFAX_68KNBN0_VAJ4" in delivered["text"]
    assert "*" not in delivered["text"]
    assert delivered["reply_markup"] is not None  # mute buttons survive


async def test_fallback_keeps_names_and_unescapes():
    text = "🖥️ *SERVER ALERT:* Unraid\\_Disk\\_4\\_disk\\_utilization\n\nDisk `sdf` WDC_WD80EFAX"
    assert markdown_to_plain(text) == (
        "🖥️ SERVER ALERT: Unraid_Disk_4_disk_utilization\n\nDisk sdf WDC_WD80EFAX"
    )


async def test_other_bad_requests_still_raise():
    send = AsyncMock(side_effect=TelegramBadRequest(method=MagicMock(), message="chat not found"))
    try:
        await send_with_retry(send, chat_id=1, text="x", parse_mode="Markdown")
    except TelegramBadRequest:
        pass
    else:
        raise AssertionError("expected TelegramBadRequest")
    assert send.call_count == 1


async def test_plain_text_parse_failure_is_not_retried_forever():
    send = AsyncMock(side_effect=TelegramBadRequest(method=MagicMock(), message="can't parse entities"))
    try:
        await send_with_retry(send, chat_id=1, text="x")
    except TelegramBadRequest:
        pass
    assert send.call_count == 1
