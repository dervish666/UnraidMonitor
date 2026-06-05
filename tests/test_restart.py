"""Tests for the graceful restart helper."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.restart import restart_bot


@pytest.mark.asyncio
async def test_restart_broadcasts_notice_and_execs():
    bot = MagicMock()
    bot.send_message = AsyncMock()
    store = MagicMock()
    store.get_all_chat_ids.return_value = [1, 2]

    with patch("src.restart.os.execv") as execv:
        await restart_bot(bot, store, notice="restarting")

    assert bot.send_message.await_count == 2
    execv.assert_called_once()


@pytest.mark.asyncio
async def test_restart_without_notice_skips_broadcast():
    bot = MagicMock()
    bot.send_message = AsyncMock()
    store = MagicMock()
    store.get_all_chat_ids.return_value = [1, 2]

    with patch("src.restart.os.execv") as execv:
        await restart_bot(bot, store)

    bot.send_message.assert_not_awaited()
    execv.assert_called_once()


@pytest.mark.asyncio
async def test_restart_does_not_stop_polling_or_close_session():
    """Regression: stopping polling/closing the session unwinds main()'s loop
    and cancels this coroutine before execv runs (the restart silently becomes
    a clean exit). restart_bot must touch neither."""
    bot = MagicMock()
    bot.send_message = AsyncMock()
    bot.session = MagicMock()
    bot.session.close = AsyncMock()
    store = MagicMock()
    store.get_all_chat_ids.return_value = [1]

    with patch("src.restart.os.execv") as execv:
        await restart_bot(bot, store, notice="hi")

    bot.session.close.assert_not_awaited()
    execv.assert_called_once()


@pytest.mark.asyncio
async def test_restart_swallows_send_failures():
    bot = MagicMock()
    bot.send_message = AsyncMock(side_effect=Exception("network"))
    store = MagicMock()
    store.get_all_chat_ids.return_value = [1]

    with patch("src.restart.os.execv") as execv:
        await restart_bot(bot, store, notice="hi")

    # A failed broadcast must not prevent the restart
    execv.assert_called_once()


@pytest.mark.asyncio
async def test_restart_execs_current_module():
    bot = MagicMock()
    store = MagicMock()
    store.get_all_chat_ids.return_value = []

    with patch("src.restart.os.execv") as execv:
        await restart_bot(bot, store)

    args = execv.call_args.args
    # os.execv(python, [python, "-m", "src.main"])
    assert args[1][-2:] == ["-m", "src.main"]
