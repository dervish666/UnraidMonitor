"""Utilities for handling Telegram API errors with retry logic."""

import asyncio
import logging
from functools import wraps
from typing import TypeVar, Callable, Awaitable, ParamSpec

from aiogram.exceptions import TelegramRetryAfter, TelegramAPIError

logger = logging.getLogger(__name__)

P = ParamSpec("P")
T = TypeVar("T")


async def send_with_retry(
    coro_func: Callable[P, Awaitable[T]],
    *args: P.args,
    max_retries: int = 3,
    **kwargs: P.kwargs,
) -> T | None:
    """Execute a Telegram API call with retry logic for rate limits.

    Args:
        coro_func: The async function to call (e.g., bot.send_message).
        *args: Positional arguments for the function.
        max_retries: Maximum number of retry attempts.
        **kwargs: Keyword arguments for the function.

    Returns:
        The result of the function call, or None if all retries failed.
    """
    last_error: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            return await coro_func(*args, **kwargs)
        except TelegramRetryAfter as e:
            retry_after = e.retry_after
            if attempt < max_retries:
                logger.warning(
                    f"Telegram rate limit hit, retrying after {retry_after}s "
                    f"(attempt {attempt + 1}/{max_retries + 1})"
                )
                await asyncio.sleep(retry_after)
                last_error = e
            else:
                logger.error(
                    f"Telegram rate limit exceeded, max retries reached: {e}"
                )
                raise
        except TelegramAPIError as e:
            # For other Telegram errors, don't retry
            logger.error(f"Telegram API error: {e}")
            raise

    return None


def with_telegram_retry(max_retries: int = 3):
    """Decorator for methods that send Telegram messages with retry logic.

    Args:
        max_retries: Maximum number of retry attempts for rate limits.

    Returns:
        Decorator function.
    """
    def decorator(func: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T | None]]:
        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T | None:
            last_error: Exception | None = None

            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except TelegramRetryAfter as e:
                    retry_after = e.retry_after
                    if attempt < max_retries:
                        logger.warning(
                            f"Telegram rate limit in {func.__name__}, "
                            f"retrying after {retry_after}s "
                            f"(attempt {attempt + 1}/{max_retries + 1})"
                        )
                        await asyncio.sleep(retry_after)
                        last_error = e
                    else:
                        logger.error(
                            f"Telegram rate limit in {func.__name__}, "
                            f"max retries reached: {e}"
                        )
                        raise
                except TelegramAPIError as e:
                    # For other Telegram errors, don't retry
                    logger.error(f"Telegram API error in {func.__name__}: {e}")
                    raise

            return None

        return wrapper

    return decorator
