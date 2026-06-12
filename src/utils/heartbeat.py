"""Liveness heartbeat backing the Docker HEALTHCHECK.

The bot exposes no HTTP port, so liveness is signalled by touching a file
from inside the event loop: a fresh mtime proves the process exists *and*
the loop is still scheduling work. The Dockerfile HEALTHCHECK fails once
the file is older than HEARTBEAT_MAX_AGE_SECONDS (or missing).
"""

import asyncio
import logging
import os

from src.constants import HEARTBEAT_INTERVAL_SECONDS, HEARTBEAT_PATH

logger = logging.getLogger(__name__)


async def heartbeat_loop(
    path: str = HEARTBEAT_PATH,
    interval: float = HEARTBEAT_INTERVAL_SECONDS,
) -> None:
    """Touch the heartbeat file every ``interval`` seconds, forever."""
    while True:
        try:
            with open(path, "w") as f:
                f.write(str(os.getpid()))
        except OSError as e:
            # Health reporting must never take the bot down with it.
            logger.warning(f"Failed to write heartbeat file {path}: {e}")
        await asyncio.sleep(interval)
