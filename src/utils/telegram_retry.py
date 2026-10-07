"""Utilities for handling Telegram API errors with retry logic."""

import asyncio
import logging
import re
from typing import TypeVar, Callable, Awaitable, Any

from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramRetryAfter

from src.utils.telegram_format import strip_html_tags

logger = logging.getLogger(__name__)

T = TypeVar("T")

# A Markdown V1 escape ("\\_") or a bold/code marker ("*", "`").
_MD_V1_TOKEN = re.compile(r"\\(.)|[*`]")


def markdown_to_plain(text: str) -> str:
    """Plain-text version of a Markdown V1 message, for the parse-failure resend.

    Drops bold and code markers and unescapes ``\\_`` and friends, but keeps
    bare underscores and brackets: in an alert they are usually part of a
    name (``Unraid_Disk_4``, a disk serial), not formatting.
    """
    return _MD_V1_TOKEN.sub(lambda m: m.group(1) or "", text)


async def send_with_retry(
    coro_func: Callable[..., Awaitable[T]],
    *args: Any,
    max_retries: int = 3,
    **kwargs: Any,
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
            else:
                logger.error(
                    f"Telegram rate limit exceeded, max retries reached: {e}"
                )
                raise
        except TelegramBadRequest as e:
            parse_mode = kwargs.get("parse_mode")
            text = kwargs.get("text")
            if "can't parse entities" in str(e) and parse_mode and isinstance(text, str):
                # Dynamic text (a notification subject, a container name) broke
                # the markup. Every alert path comes through here, so resend it
                # plain once rather than lose the alert.
                logger.warning(f"Telegram rejected the message markup ({e}); resending as plain text")
                plain = {k: v for k, v in kwargs.items() if k != "parse_mode"}
                plain["text"] = (
                    strip_html_tags(text) if parse_mode == "HTML" else markdown_to_plain(text)
                )
                return await send_with_retry(coro_func, *args, max_retries=max_retries, **plain)
            logger.error(f"Telegram API error: {e}")
            raise
        except TelegramAPIError as e:
            # For other Telegram errors, don't retry
            logger.error(f"Telegram API error: {e}")
            raise

    return None

