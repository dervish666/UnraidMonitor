"""Tests for memory management bot commands."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from aiogram.types import Message


@pytest.fixture
def mock_message():
    message = MagicMock(spec=Message)
    message.answer = AsyncMock()
    message.from_user = MagicMock()
    message.from_user.id = 12345
    return message


@pytest.fixture
def mock_memory_monitor():
    monitor = MagicMock()
    monitor.cancel_pending_kill = MagicMock(return_value=True)
    monitor.get_pending_kill = MagicMock(return_value="bitmagnet")
    return monitor


class TestCancelKillCommand:
    @pytest.mark.asyncio
    async def test_cancel_kill_success(self, mock_message, mock_memory_monitor):
        from src.bot.memory_commands import cancel_kill_command

        handler = cancel_kill_command(mock_memory_monitor)
        await handler(mock_message)

        mock_memory_monitor.cancel_pending_kill.assert_called_once()
        mock_message.answer.assert_called_once()
        assert "cancelled" in mock_message.answer.call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_cancel_kill_nothing_pending(self, mock_message):
        from src.bot.memory_commands import cancel_kill_command

        monitor = MagicMock()
        monitor.cancel_pending_kill = MagicMock(return_value=False)
        monitor.get_pending_kill = MagicMock(return_value=None)

        handler = cancel_kill_command(monitor)
        await handler(mock_message)

        assert "no pending" in mock_message.answer.call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_cancel_kill_monitor_disabled(self, mock_message):
        from src.bot.memory_commands import cancel_kill_command

        handler = cancel_kill_command(None)
        await handler(mock_message)

        assert "not enabled" in mock_message.answer.call_args[0][0].lower()
