"""Liveness heartbeat backing the Docker HEALTHCHECK.

The bot exposes no HTTP port, so liveness is signalled by touching a file
from inside the event loop: a fresh mtime proves the process exists *and*
the loop is still scheduling work. The Dockerfile HEALTHCHECK fails once
the file is older than HEARTBEAT_MAX_AGE_SECONDS (or missing).

A failing HEALTHCHECK only marks the container unhealthy; Docker Engine
does not restart it. The restart, when every monitor has died, is the
bot's own (``on_dead``).
"""

import asyncio
import logging
import os
import time
from typing import Awaitable, Callable

from src.constants import (
    DEAD_MONITORS_RESTART_GRACE_SECONDS,
    HEARTBEAT_INTERVAL_SECONDS,
    HEARTBEAT_PATH,
)

logger = logging.getLogger(__name__)


async def heartbeat_loop(
    path: str = HEARTBEAT_PATH,
    interval: float = HEARTBEAT_INTERVAL_SECONDS,
    alive: Callable[[], bool] | None = None,
    on_dead: Callable[[], Awaitable[object]] | None = None,
    grace: float = DEAD_MONITORS_RESTART_GRACE_SECONDS,
) -> None:
    """Touch the heartbeat file every ``interval`` seconds.

    With *alive*, the beat stops while it returns False (every monitor has
    died), so Docker shows the container unhealthy: a live event loop with
    nothing watching is not healthy. Once that has lasted *grace* seconds,
    *on_dead* runs, once (it restarts the process, rate-limited).
    """
    dead_since: float | None = None
    asked = False
    while True:
        if alive is not None and not alive():
            if dead_since is None:
                logger.critical("No monitor is running; stopping the heartbeat")
                dead_since = time.monotonic()
            if on_dead is not None and not asked and time.monotonic() - dead_since >= grace:
                asked = True
                try:
                    await on_dead()
                except Exception as e:
                    logger.error(f"Restart after every monitor died failed: {e}", exc_info=True)
            await asyncio.sleep(interval)
            continue
        dead_since = None
        asked = False
        try:
            with open(path, "w") as f:
                f.write(str(os.getpid()))
        except OSError as e:
            # Health reporting must never take the bot down with it.
            logger.warning(f"Failed to write heartbeat file {path}: {e}")
        await asyncio.sleep(interval)
