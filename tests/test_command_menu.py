"""Tests for the Telegram command menu published via setMyCommands."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Dispatcher
from aiogram.filters import Command

from src.bot.command_menu import (
    COMMAND_DESCRIPTIONS,
    collect_registered_commands,
    publish_command_menu,
)


async def _noop(message):  # pragma: no cover - never invoked
    return None


def _dispatcher_with(*commands: str) -> Dispatcher:
    dp = Dispatcher()
    for command in commands:
        dp.message.register(_noop, Command(command))
    return dp


def test_collects_only_registered_commands():
    dp = _dispatcher_with("status", "health")

    names = [c.command for c in collect_registered_commands(dp)]

    assert names == ["status", "health"]


def test_menu_follows_description_order_not_registration_order():
    dp = _dispatcher_with("help", "status")

    names = [c.command for c in collect_registered_commands(dp)]

    # "status" is listed first in COMMAND_DESCRIPTIONS, "help" last.
    assert names == ["status", "help"]


def test_hyphenated_commands_are_skipped():
    """Telegram rejects the whole call if any menu name contains a hyphen."""
    dp = _dispatcher_with("status", "mute-server", "cancel-kill")

    names = [c.command for c in collect_registered_commands(dp)]

    assert names == ["status"]


def test_unregistered_commands_are_absent():
    dp = _dispatcher_with("status")

    names = [c.command for c in collect_registered_commands(dp)]

    assert "diagnose" not in names
    assert "server" not in names


def test_every_description_is_a_valid_telegram_entry():
    for name, description in COMMAND_DESCRIPTIONS.items():
        assert name.islower()
        assert name.replace("_", "").isalnum(), name
        assert 1 <= len(name) <= 32, name
        assert 1 <= len(description) <= 256, name


@pytest.mark.asyncio
async def test_publish_sends_commands_to_telegram():
    dp = _dispatcher_with("status", "health")
    bot = MagicMock()
    bot.set_my_commands = AsyncMock()

    await publish_command_menu(bot, dp)

    sent = bot.set_my_commands.call_args[0][0]
    assert [c.command for c in sent] == ["status", "health"]


@pytest.mark.asyncio
async def test_publish_skips_call_when_nothing_eligible():
    dp = _dispatcher_with("cancel-kill")
    bot = MagicMock()
    bot.set_my_commands = AsyncMock()

    await publish_command_menu(bot, dp)

    bot.set_my_commands.assert_not_called()


@pytest.mark.asyncio
async def test_publish_swallows_api_failure():
    """A failed menu update must not stop the bot from starting."""
    dp = _dispatcher_with("status")
    bot = MagicMock()
    bot.set_my_commands = AsyncMock(side_effect=RuntimeError("flood wait"))

    await publish_command_menu(bot, dp)


def test_real_registration_produces_a_menu():
    """Pins the wiring: a synthetic dispatcher can't catch a drift in register_commands."""
    from src.bot.telegram_bot import register_commands
    from src.state import ContainerStateManager

    dp = Dispatcher()
    register_commands(dp, ContainerStateManager())

    names = [c.command for c in collect_registered_commands(dp)]

    assert "status" in names
    assert "help" in names


def test_wizard_cancel_does_not_warn_about_a_missing_description(caplog):
    """/cancel is registered on every normal boot; the warning must stay signal."""
    dp = _dispatcher_with("status", "cancel")

    with caplog.at_level("WARNING", logger="src.bot.command_menu"):
        collect_registered_commands(dp)

    assert not caplog.records


def test_genuinely_undescribed_command_still_warns(caplog):
    dp = _dispatcher_with("status", "brandnew")

    with caplog.at_level("WARNING", logger="src.bot.command_menu"):
        collect_registered_commands(dp)

    assert any("brandnew" in r.message for r in caplog.records)
