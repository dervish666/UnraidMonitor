"""Tests for make_memory_alert_handler button rendering."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.monitor_callbacks import make_memory_alert_handler

GB = 1024**3


def _flatten(keyboard):
    return [b for row in keyboard.inline_keyboard for b in row]


def _handler_and_bot():
    chat_id_store = MagicMock()
    chat_id_store.get_all_chat_ids.return_value = [111]
    bot = MagicMock()
    bot.send_message = AsyncMock()
    handler = make_memory_alert_handler(chat_id_store, bot, lambda s: s)
    return handler, bot


async def _sent_keyboard(bot):
    kwargs = bot.send_message.call_args.kwargs
    return kwargs["reply_markup"]


@pytest.mark.asyncio
async def test_warning_restart_buttons_before_stop_buttons():
    handler, bot = _handler_and_bot()

    with patch("src.monitor_callbacks.send_with_retry", new=AsyncMock()) as retry:
        await handler(
            "Memory Warning", "Memory at 91%.", "warning",
            [("bitmagnet", 2 * GB), ("sab", 1 * GB)],
            [("plex", 8 * GB)],
        )
        keyboard = retry.call_args.kwargs["reply_markup"]

    data = [b.callback_data for b in _flatten(keyboard)]
    assert data == ["mem_restart:plex", "mem_kill:bitmagnet", "mem_kill:sab"]
    labels = [b.text for b in _flatten(keyboard)]
    assert labels[0] == "🔄 Restart plex (8.0GB)"
    assert labels[1] == "⏹ Stop bitmagnet (2.0GB)"


@pytest.mark.asyncio
async def test_warning_restartable_only_still_gets_keyboard():
    handler, bot = _handler_and_bot()

    with patch("src.monitor_callbacks.send_with_retry", new=AsyncMock()) as retry:
        await handler("Memory Warning", "msg", "warning", [], [("plex", None)])
        keyboard = retry.call_args.kwargs["reply_markup"]

    data = [b.callback_data for b in _flatten(keyboard)]
    assert data == ["mem_restart:plex"]
    # No memory figure -> plain label
    assert _flatten(keyboard)[0].text == "🔄 Restart plex"


@pytest.mark.asyncio
async def test_critical_kill_and_cancel_then_restart_rows():
    handler, bot = _handler_and_bot()

    with patch("src.monitor_callbacks.send_with_retry", new=AsyncMock()) as retry:
        await handler(
            "Memory Critical", "msg", "critical",
            [("sab", 1 * GB)],
            [("plex", 8 * GB)],
        )
        keyboard = retry.call_args.kwargs["reply_markup"]

    data = [b.callback_data for b in _flatten(keyboard)]
    assert data == ["mem_kill:sab", "mem_cancel_kill", "mem_restart:plex"]


@pytest.mark.asyncio
async def test_critical_no_killable_offers_restarts():
    """The 'no killable containers' critical still offers the gentle way out."""
    handler, bot = _handler_and_bot()

    with patch("src.monitor_callbacks.send_with_retry", new=AsyncMock()) as retry:
        await handler(
            "Memory Critical - No Action Available", "msg", "critical",
            [], [("plex", 8 * GB)],
        )
        keyboard = retry.call_args.kwargs["reply_markup"]

    data = [b.callback_data for b in _flatten(keyboard)]
    assert data == ["mem_restart:plex"]


@pytest.mark.asyncio
async def test_info_alert_has_no_keyboard():
    handler, bot = _handler_and_bot()

    with patch("src.monitor_callbacks.send_with_retry", new=AsyncMock()) as retry:
        await handler("Container Stopped", "msg", "info", [], [])
        assert retry.call_args.kwargs["reply_markup"] is None


@pytest.mark.asyncio
async def test_markdown_failure_falls_back_to_plain_text():
    """A container name that breaks Markdown must not eat the alert."""
    from aiogram.exceptions import TelegramBadRequest

    handler, bot = _handler_and_bot()

    send = AsyncMock(
        side_effect=[TelegramBadRequest(method=MagicMock(), message="can't parse entities"), None]
    )
    with patch("src.monitor_callbacks.send_with_retry", new=send):
        await handler(
            "Memory Warning", "Memory at 91%.\n\n1. my_odd_name — 2.0GB", "warning",
            [("my_odd_name", 2 * GB)], [],
        )

    assert send.call_count == 2
    fallback_kwargs = send.call_args_list[1].kwargs
    assert "parse_mode" not in fallback_kwargs
    assert fallback_kwargs["reply_markup"] is not None  # buttons survive the fallback
