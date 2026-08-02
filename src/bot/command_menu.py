"""Telegram command menu published via setMyCommands.

The menu is derived from what is actually registered on the dispatcher rather
than from a hand-maintained list, so a command that is unavailable (no Docker,
no Unraid key, no LLM provider) never shows up in the "/" autocomplete offering
something the bot cannot do.
"""

import logging
import re
from typing import TYPE_CHECKING

from aiogram.filters import Command
from aiogram.types import BotCommand

if TYPE_CHECKING:
    from aiogram import Bot, Dispatcher

logger = logging.getLogger(__name__)

# Telegram rejects the whole setMyCommands call if any name is invalid:
# lowercase letters, digits and underscores only, 1-32 chars.
_VALID_COMMAND = re.compile(r"^[a-z0-9_]{1,32}$")

# Menu order (not registration order) plus the one-line descriptions Telegram
# shows next to each command. Commands missing here are skipped.
COMMAND_DESCRIPTIONS: dict[str, str] = {
    "status": "Container status summary or details",
    "resources": "CPU & memory usage per container",
    "logs": "View recent container logs",
    "diagnose": "AI-powered log analysis",
    "restart": "Restart a container",
    "stop": "Stop a container",
    "start": "Start a container",
    "pull": "Pull latest image & recreate",
    "server": "Unraid server overview",
    "array": "Array status and disk health",
    "disks": "Individual disk usage details",
    "mute": "Mute alerts for a container",
    "unmute": "Unmute a container",
    "mutes": "List active mutes",
    "ignore": "Ignore recent error patterns",
    "ignores": "List active ignore patterns",
    "manage": "Dashboard for status, alerts & features",
    "health": "Bot version, uptime & monitor status",
    "model": "Switch LLM provider and model",
    "setup": "Re-run the setup wizard",
    "help": "Show the command reference",
}

# Registered but deliberately absent from the menu, so the "missing description"
# warning stays meaningful instead of firing on every boot. /cancel only means
# anything mid-wizard.
_INTENTIONALLY_HIDDEN = {"cancel"}


def collect_registered_commands(dp: "Dispatcher") -> list[BotCommand]:
    """Build the menu from the Command filters registered on the dispatcher.

    Args:
        dp: Dispatcher whose message handlers have already been registered.

    Returns:
        BotCommand entries in COMMAND_DESCRIPTIONS order, limited to commands
        that are both registered and valid as a Telegram menu entry.
    """
    registered: set[str] = set()
    skipped: set[str] = set()

    for handler in dp.message.handlers:
        for flt in handler.filters or []:
            callback = flt.callback
            if not isinstance(callback, Command):
                continue
            for command in callback.commands:
                if not isinstance(command, str):
                    continue
                if _VALID_COMMAND.match(command):
                    registered.add(command)
                else:
                    # e.g. /mute-server -- works when typed, but Telegram will
                    # not accept a hyphen in the menu.
                    skipped.add(command)

    if skipped:
        logger.info(
            f"Commands not eligible for the Telegram menu (invalid name): {sorted(skipped)}"
        )

    undescribed = registered - COMMAND_DESCRIPTIONS.keys() - _INTENTIONALLY_HIDDEN
    if undescribed:
        logger.warning(
            f"Registered commands missing a menu description: {sorted(undescribed)}"
        )

    described_but_unregistered = COMMAND_DESCRIPTIONS.keys() - registered - skipped
    if described_but_unregistered:
        # Usually a feature that is simply off; but it also catches publishing
        # before a command is registered, which is otherwise invisible.
        logger.info(
            f"Commands described but not registered (feature off, or published too early): "
            f"{sorted(described_but_unregistered)}"
        )

    return [
        BotCommand(command=name, description=description)
        for name, description in COMMAND_DESCRIPTIONS.items()
        if name in registered
    ]


async def publish_command_menu(bot: "Bot", dp: "Dispatcher") -> None:
    """Push the command menu to Telegram so "/" autocompletes.

    Never raises: a failed menu update must not stop the bot from starting.
    """
    commands = collect_registered_commands(dp)
    if not commands:
        logger.warning("No commands eligible for the Telegram menu; skipping")
        return

    try:
        await bot.set_my_commands(commands)
        logger.info(f"Published {len(commands)} commands to the Telegram menu")
    except Exception as e:
        logger.warning(f"Failed to publish command menu: {e}")
