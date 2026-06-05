"""Graceful in-place process restart via os.execv.

Used to apply configuration changes that can only take effect at startup
(setup wizard, enabling the image-update monitor). Re-execs ``python -m
src.main`` so the supervisor (Docker) sees the same process.
"""

import asyncio
import logging
import os
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aiogram import Bot, Dispatcher
    from src.alerts.manager import ChatIdStore

logger = logging.getLogger(__name__)


async def restart_bot(
    bot: "Bot",
    dp: "Dispatcher",
    chat_id_store: "ChatIdStore",
    notice: str | None = None,
) -> None:
    """Stop polling and re-exec the process so new config takes effect.

    Args:
        bot: The aiogram Bot, used to broadcast ``notice`` if provided.
        dp: The dispatcher to stop polling on.
        chat_id_store: Source of chat IDs for the optional broadcast.
        notice: If set, sent to every known chat before restarting. Callers
            that have already messaged the user (e.g. an inline-button handler
            editing its own message) should pass ``None``.
    """
    logger.info("Restarting bot to apply configuration changes")
    if notice:
        for cid in chat_id_store.get_all_chat_ids():
            try:
                await bot.send_message(cid, notice)
            except Exception:
                pass
    await dp.stop_polling()
    await asyncio.sleep(1)
    os.execv(sys.executable, [sys.executable, "-m", "src.main"])
