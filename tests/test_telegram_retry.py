"""Tests for Telegram retry utilities."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from aiogram.exceptions import TelegramRetryAfter, TelegramAPIError

from src.utils.telegram_retry import send_with_retry


class TestSendWithRetry:
    """Tests for the send_with_retry function."""

    @pytest.mark.asyncio
    async def test_success_on_first_try(self):
        """Function succeeds on first try."""
        mock_func = AsyncMock(return_value="success")

        result = await send_with_retry(mock_func, "arg1", kwarg1="value1")

        assert result == "success"
        mock_func.assert_called_once_with("arg1", kwarg1="value1")

    @pytest.mark.asyncio
    async def test_retry_on_rate_limit(self):
        """Retries after rate limit error."""
        # First call raises rate limit, second succeeds
        mock_func = AsyncMock(
            side_effect=[
                TelegramRetryAfter(retry_after=0.01, method=MagicMock(), message="Rate limited"),
                "success",
            ]
        )

        result = await send_with_retry(mock_func, max_retries=3)

        assert result == "success"
        assert mock_func.call_count == 2

    @pytest.mark.asyncio
    async def test_max_retries_exceeded(self):
        """Raises after max retries exceeded."""
        mock_func = AsyncMock(
            side_effect=TelegramRetryAfter(retry_after=0.01, method=MagicMock(), message="Rate limited")
        )

        with pytest.raises(TelegramRetryAfter):
            await send_with_retry(mock_func, max_retries=2)

        # Should try 3 times (initial + 2 retries)
        assert mock_func.call_count == 3

    @pytest.mark.asyncio
    async def test_no_retry_on_other_telegram_error(self):
        """Does not retry on non-rate-limit Telegram errors."""
        mock_func = AsyncMock(
            side_effect=TelegramAPIError(method=MagicMock(), message="Bad request")
        )

        with pytest.raises(TelegramAPIError):
            await send_with_retry(mock_func, max_retries=3)

        # Should only try once
        mock_func.assert_called_once()
