"""Tests for the graceful restart helper."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.restart import restart_bot


@pytest.mark.asyncio
async def test_restart_broadcasts_notice_stops_polling_and_execs():
    bot = MagicMock()
    bot.send_message = AsyncMock()
    dp = MagicMock()
    dp.stop_polling = AsyncMock()
    store = MagicMock()
    store.get_all_chat_ids.return_value = [1, 2]

    with patch("src.restart.os.execv") as execv, \
            patch("src.restart.asyncio.sleep", new=AsyncMock()):
        await restart_bot(bot, dp, store, notice="restarting")

    assert bot.send_message.await_count == 2
    dp.stop_polling.assert_awaited_once()
    execv.assert_called_once()


@pytest.mark.asyncio
async def test_restart_without_notice_skips_broadcast():
    bot = MagicMock()
    bot.send_message = AsyncMock()
    dp = MagicMock()
    dp.stop_polling = AsyncMock()
    store = MagicMock()
    store.get_all_chat_ids.return_value = [1, 2]

    with patch("src.restart.os.execv") as execv, \
            patch("src.restart.asyncio.sleep", new=AsyncMock()):
        await restart_bot(bot, dp, store)

    bot.send_message.assert_not_awaited()
    dp.stop_polling.assert_awaited_once()
    execv.assert_called_once()


@pytest.mark.asyncio
async def test_restart_swallows_send_failures():
    bot = MagicMock()
    bot.send_message = AsyncMock(side_effect=Exception("network"))
    dp = MagicMock()
    dp.stop_polling = AsyncMock()
    store = MagicMock()
    store.get_all_chat_ids.return_value = [1]

    with patch("src.restart.os.execv") as execv, \
            patch("src.restart.asyncio.sleep", new=AsyncMock()):
        await restart_bot(bot, dp, store, notice="hi")

    # A failed broadcast must not prevent the restart
    dp.stop_polling.assert_awaited_once()
    execv.assert_called_once()
