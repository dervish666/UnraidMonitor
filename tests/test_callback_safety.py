"""Tests for the safety net around inline-button callbacks.

Covers three failure modes that previously left a Telegram button spinning
forever with no feedback:
  * a callback prefix rendered by an alert but never registered,
  * an exception raised inside a handler,
  * memory kill/restart callbacks gated on the pressure loop being enabled.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import CallbackQuery, ErrorEvent, Message, Update

from src.bot.telegram_bot import (
    _register_memory_commands,
    create_dispatcher,
    on_handler_error,
    unhandled_callback,
)


def _has_handler_for(dp, data: str) -> bool:
    """Whether any registered callback handler's filters accept this data.

    Magic filters (F.data.startswith(...)) don't expose their pattern, so
    resolve them against a stand-in event instead of reading them.
    """
    event = SimpleNamespace(data=data)
    for handler in dp.callback_query.handlers:
        filters = handler.filters or []
        if not filters:
            continue  # the unfiltered catch-all matches everything
        if all(bool(flt.callback(event)) for flt in filters):
            return True
    return False


@pytest.mark.asyncio
async def test_unhandled_callback_answers_the_query():
    callback = MagicMock(spec=CallbackQuery)
    callback.data = "mem_kill:plex"
    callback.answer = AsyncMock()

    await unhandled_callback(callback)

    callback.answer.assert_awaited_once()
    assert "no longer available" in callback.answer.call_args[0][0]


def test_dispatcher_registers_an_error_handler():
    dp = create_dispatcher(allowed_users=[1])

    assert dp.errors.handlers, "no error handler registered"


@pytest.mark.asyncio
async def test_error_handler_replies_to_a_failed_message_command():
    message = MagicMock(spec=Message)
    message.answer = AsyncMock()
    update = MagicMock(spec=Update)
    update.callback_query = None
    update.message = message
    event = MagicMock(spec=ErrorEvent)
    event.update = update
    event.exception = ValueError("boom")

    handled = await on_handler_error(event)

    assert handled is True
    assert "ValueError" in message.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_error_handler_answers_a_failed_button():
    """Answering matters more than the reply -- otherwise the button spins."""
    message = MagicMock(spec=Message)
    message.answer = AsyncMock()
    callback = MagicMock(spec=CallbackQuery)
    callback.answer = AsyncMock()
    callback.message = message
    update = MagicMock(spec=Update)
    update.callback_query = callback
    update.message = None
    event = MagicMock(spec=ErrorEvent)
    event.update = update
    event.exception = KeyError("array_usage")

    handled = await on_handler_error(event)

    assert handled is True
    callback.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_error_handler_survives_a_failing_reply():
    """A send failure while reporting an error must not raise again."""
    message = MagicMock(spec=Message)
    message.answer = AsyncMock(side_effect=RuntimeError("chat not found"))
    update = MagicMock(spec=Update)
    update.callback_query = None
    update.message = message
    event = MagicMock(spec=ErrorEvent)
    event.update = update
    event.exception = ValueError("boom")

    assert await on_handler_error(event) is True


def test_mem_kill_registered_when_pressure_loop_is_disabled():
    """Unraid memory alerts render Stop buttons regardless of this flag."""
    dp = create_dispatcher(allowed_users=[1])
    memory_config = MagicMock()
    memory_config.enabled = False

    _register_memory_commands(dp, MagicMock(), ["plex"], memory_config)

    assert _has_handler_for(dp, "mem_kill:plex")
    assert _has_handler_for(dp, "mem_restart:plex")
    assert _has_handler_for(dp, "mem_restart_yes:plex")


def test_mem_kill_registered_when_pressure_loop_is_enabled():
    dp = create_dispatcher(allowed_users=[1])
    memory_config = MagicMock()
    memory_config.enabled = True

    _register_memory_commands(dp, MagicMock(), ["plex"], memory_config)

    assert _has_handler_for(dp, "mem_kill:plex")


@pytest.mark.asyncio
async def test_cancel_kill_reports_the_feature_is_off():
    dp = create_dispatcher(allowed_users=[1])
    memory_config = MagicMock()
    memory_config.enabled = False

    _register_memory_commands(dp, MagicMock(), [], memory_config)

    handler = dp.message.handlers[0].callback
    message = MagicMock(spec=Message)
    message.answer = AsyncMock()

    await handler(message)

    assert "not enabled" in message.answer.call_args[0][0]


def _full_dispatcher():
    """A dispatcher wired the way start_monitoring wires it (no Docker)."""
    from src.bot.telegram_bot import register_commands
    from src.state import ContainerStateManager

    dp = create_dispatcher(allowed_users=[1])
    register_commands(dp, ContainerStateManager())
    return dp


def test_catch_all_is_the_last_callback_handler():
    """Registered anywhere but last, it swallows every real button."""
    dp = _full_dispatcher()

    assert dp.callback_query.handlers[-1].callback is unhandled_callback


def test_exactly_one_unfiltered_callback_handler_exists():
    dp = _full_dispatcher()

    unfiltered = [h for h in dp.callback_query.handlers if not h.filters]

    assert len(unfiltered) == 1
    assert unfiltered[0].callback is unhandled_callback


def test_real_prefixes_still_reach_their_own_handlers():
    """Guards against the catch-all shadowing anything registered before it."""
    dp = _full_dispatcher()

    def _first_match(data):
        return next(
            h for h in dp.callback_query.handlers
            if not h.filters or all(bool(f.callback(SimpleNamespace(data=data))) for f in h.filters)
        )

    # Registered in this wiring -- must not be swallowed.
    for data in ("help:containers", "help:back"):
        assert _first_match(data).callback is not unhandled_callback, data

    # Not registered in this wiring -- must be, so it can't spin.
    assert _first_match("mem_kill:plex").callback is unhandled_callback


@pytest.mark.asyncio
async def test_error_handler_still_replies_when_answering_fails():
    """An expired query must not take the explanation down with it."""
    message = MagicMock(spec=Message)
    message.answer = AsyncMock()
    callback = MagicMock(spec=CallbackQuery)
    callback.answer = AsyncMock(side_effect=RuntimeError("query is too old"))
    callback.message = message
    update = MagicMock(spec=Update)
    update.callback_query = callback
    update.message = None
    event = MagicMock(spec=ErrorEvent)
    event.update = update
    event.exception = KeyError("array_usage")

    assert await on_handler_error(event) is True
    assert "KeyError" in message.answer.call_args[0][0]
