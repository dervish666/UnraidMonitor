"""Graceful in-place process restart via os.execv.

Used to apply configuration changes that can only take effect at startup
(setup wizard, enabling the image-update monitor). Re-execs ``python -m
src.main`` so the supervisor (Docker) sees the same process.
"""

import logging
import os
import sys
import time
from typing import TYPE_CHECKING

from src.constants import (
    DEAD_MONITORS_RESTART_MIN_INTERVAL_SECONDS,
    DEAD_MONITORS_RESTART_STATE,
)
from src.utils.json_store import atomic_write_json, load_json

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
            except Exception as e:
                logger.warning(f"Could not send restart notice to {cid}: {e}")
    try:
        os.execv(sys.executable, [sys.executable, "-m", "src.main"])
    finally:
        # execv only returns (or raises) on failure — the process was NOT
        # replaced, so clear the guard to allow a retry.
        _restart_in_progress = False


async def restart_after_monitors_died(
    bot: "Bot",
    chat_id_store: "ChatIdStore",
    state_path: str = DEAD_MONITORS_RESTART_STATE,
    now: float | None = None,
) -> bool:
    """Restart the process because every monitor task died; False if refused.

    Docker Engine only marks a container unhealthy when its HEALTHCHECK
    fails, it does not restart it, so the bot restarts itself. The time of
    the last such restart is written to disk first, and a second one inside
    DEAD_MONITORS_RESTART_MIN_INTERVAL_SECONDS is refused, so a monitor that
    dies on every start cannot turn this into an exec loop.
    """
    now = time.time() if now is None else now
    last = load_json(state_path, dict, "dead-monitor restart")
    last_at = last.get("at") if isinstance(last, dict) else None
    if isinstance(last_at, (int, float)) and now - last_at < DEAD_MONITORS_RESTART_MIN_INTERVAL_SECONDS:
        logger.critical(
            "Every monitor has stopped again within "
            f"{DEAD_MONITORS_RESTART_MIN_INTERVAL_SECONDS // 60} minutes of the last automatic "
            "restart. Not restarting again; check the log and restart the container."
        )
        return False
    try:
        atomic_write_json(state_path, {"at": now})
    except OSError as e:
        logger.critical(f"Every monitor has stopped, but the restart guard cannot be saved ({e}); not restarting")
        return False
    logger.critical("Every monitor has stopped; restarting the bot")
    await restart_bot(
        bot, chat_id_store,
        notice="⚠️ Every monitor in the bot stopped. Restarting it now; check the container log.",
    )
    return True
