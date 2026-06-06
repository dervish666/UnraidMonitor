"""Graceful in-place process restart via os.execv.

Used to apply configuration changes that can only take effect at startup
(setup wizard, enabling the image-update monitor). Re-execs ``python -m
src.main`` so the supervisor (Docker) sees the same process.
"""

import logging
import os
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aiogram import Bot
    from src.alerts.manager import ChatIdStore

logger = logging.getLogger(__name__)

# Guards against concurrent restart triggers (e.g. /restart button + wizard
# finish) interleaving duplicate broadcast notices. execv replaces the whole
# process, so this never needs resetting.
_restart_in_progress = False


async def restart_bot(
    bot: "Bot",
    chat_id_store: "ChatIdStore",
    notice: str | None = None,
) -> None:
    """Replace the running process with a fresh ``python -m src.main``.

    Args:
        bot: The aiogram Bot, used to broadcast ``notice`` if provided.
        chat_id_store: Source of chat IDs for the optional broadcast.
        notice: If set, sent to every known chat before restarting. Callers
            that have already messaged the user (e.g. an inline-button handler
            editing its own message) should pass ``None``.

    We deliberately do NOT call ``dp.stop_polling()`` or ``bot.session.close()``
    first: either would unwind ``main()``'s polling loop and run its
    ``finally`` (``bg.shutdown()`` + loop teardown), which cancels this
    coroutine *before* it reaches ``os.execv`` — the restart then silently
    degrades to a plain exit that only Docker's restart policy (if any) would
    recover. ``os.execv`` replaces the whole process image, so polling, tasks
    and sockets all stop regardless. Python marks file descriptors
    close-on-exec by default, so the in-flight long-poll connection drops on
    exec and Telegram frees the getUpdates slot immediately (no 409 conflict
    for the fresh process).

    The broadcast ``await`` below resolves once Telegram has the message, and
    there is intentionally no awaitable between it and ``execv`` so nothing can
    preempt the restart. ``execv`` only returns on failure, in which case the
    bot keeps running normally.
    """
    global _restart_in_progress
    if _restart_in_progress:
        logger.warning("Restart already in progress, ignoring duplicate trigger")
        return
    _restart_in_progress = True
    logger.info("Restarting bot to apply configuration changes")
    if notice:
        for cid in chat_id_store.get_all_chat_ids():
            try:
                await bot.send_message(cid, notice)
            except Exception:
                pass
    try:
        os.execv(sys.executable, [sys.executable, "-m", "src.main"])
    finally:
        # execv only returns (or raises) on failure — the process was NOT
        # replaced, so clear the guard to allow a retry.
        _restart_in_progress = False
