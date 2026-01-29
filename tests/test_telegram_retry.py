"""Tests for Telegram retry utilities."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from aiogram.exceptions import TelegramRetryAfter, TelegramAPIError

from src.utils.telegram_retry import send_with_retry, with_telegram_retry


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


class TestWithTelegramRetryDecorator:
    """Tests for the with_telegram_retry decorator."""

    @pytest.mark.asyncio
    async def test_decorator_success(self):
        """Decorated function succeeds normally."""
        @with_telegram_retry(max_retries=3)
        async def my_func(value):
            return value * 2

        result = await my_func(5)
        assert result == 10

    @pytest.mark.asyncio
    async def test_decorator_retries_rate_limit(self):
        """Decorated function retries on rate limit."""
        call_count = 0

        @with_telegram_retry(max_retries=3)
        async def my_func():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise TelegramRetryAfter(retry_after=0.01, method=MagicMock(), message="Rate limited")
            return "success"

        result = await my_func()
        assert result == "success"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_decorator_raises_after_max_retries(self):
        """Decorated function raises after max retries."""
        @with_telegram_retry(max_retries=2)
        async def my_func():
            raise TelegramRetryAfter(retry_after=0.01, method=MagicMock(), message="Rate limited")

        with pytest.raises(TelegramRetryAfter):
            await my_func()

    @pytest.mark.asyncio
    async def test_decorator_preserves_function_name(self):
        """Decorator preserves function metadata."""
        @with_telegram_retry(max_retries=3)
        async def my_special_func():
            """My docstring."""
            return "result"

        assert my_special_func.__name__ == "my_special_func"
        assert "My docstring" in (my_special_func.__doc__ or "")
