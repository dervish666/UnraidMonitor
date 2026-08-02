"""Tests for the Unraid array/server threshold adjustment buttons.

These callbacks were entirely untested, which is how the array-capacity button
shipped persisting a value and then raising KeyError before confirming.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.bot.alert_callbacks import (
    _METRIC_TO_SET_KEY,
    _SET_KEY_TO_METRIC,
    _UNRAID_METRICS,
    array_set_threshold_callback,
    array_threshold_callback,
    server_set_threshold_callback,
    server_threshold_callback,
)
from src.config import UnraidConfig


def _callback(data: str) -> MagicMock:
    callback = MagicMock()
    callback.data = data
    callback.answer = AsyncMock()
    callback.message = None
    return callback


def _config() -> UnraidConfig:
    config = UnraidConfig()
    config._persist = MagicMock()  # type: ignore[method-assign]
    return config


def test_every_set_key_maps_back_to_display_metadata():
    """The picker and the setter use different keys -- they must round-trip."""
    for metric, set_key in _METRIC_TO_SET_KEY.items():
        assert _SET_KEY_TO_METRIC[set_key] == metric
        assert metric in _UNRAID_METRICS


@pytest.mark.asyncio
async def test_array_capacity_threshold_applies_and_confirms():
    config = _config()
    callback = _callback("arr_set:array_usage:90")

    await array_set_threshold_callback(config)(callback)

    assert config.array_usage_threshold == 90
    callback.answer.assert_awaited_once_with("Set to 90%")


@pytest.mark.asyncio
async def test_array_disk_temp_threshold_applies_and_confirms():
    config = _config()
    callback = _callback("arr_set:disk_temp:55")

    await array_set_threshold_callback(config)(callback)

    assert config.disk_temp_threshold == 55
    callback.answer.assert_awaited_once_with("Set to 55°C")


@pytest.mark.asyncio
async def test_array_capacity_reset_restores_default():
    config = _config()
    config.array_usage_threshold = 99
    callback = _callback("arr_set:array_usage:0")

    await array_set_threshold_callback(config)(callback)

    assert config.array_usage_threshold == UnraidConfig().array_usage_threshold
    assert "Reset" in callback.answer.call_args[0][0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data,attr,expected",
    [
        ("srv_set:cpu_usage:95", "cpu_usage_threshold", 95),
        ("srv_set:cpu_temp:90", "cpu_temp_threshold", 90),
    ],
)
async def test_server_thresholds_apply_and_confirm(data, attr, expected):
    config = _config()
    callback = _callback(data)

    await server_set_threshold_callback(config)(callback)

    assert getattr(config, attr) == expected
    callback.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_array_setter_rejects_a_server_metric():
    config = _config()
    callback = _callback("arr_set:cpu_temp:90")

    await array_set_threshold_callback(config)(callback)

    assert config.cpu_temp_threshold == UnraidConfig().cpu_temp_threshold
    callback.answer.assert_awaited_once_with("Invalid metric")


@pytest.mark.asyncio
async def test_server_setter_rejects_an_array_metric():
    config = _config()
    callback = _callback("srv_set:array_usage:90")

    await server_set_threshold_callback(config)(callback)

    assert config.array_usage_threshold == UnraidConfig().array_usage_threshold
    callback.answer.assert_awaited_once_with("Invalid metric")


@pytest.mark.asyncio
async def test_pickers_emit_set_keys_the_setters_accept():
    """End-to-end: whatever the picker puts on a button must apply cleanly."""
    for factory, data, allowed in [
        (array_threshold_callback, "arr_thresh:capacity:85", array_set_threshold_callback),
        (array_threshold_callback, "arr_thresh:disk_temp:50", array_set_threshold_callback),
        (server_threshold_callback, "srv_thresh:cpu_temp:80", server_set_threshold_callback),
        (server_threshold_callback, "srv_thresh:cpu_usage:90", server_set_threshold_callback),
    ]:
        config = _config()
        picker_callback = _callback(data)
        picker_callback.message = MagicMock()
        picker_callback.message.answer = AsyncMock()

        await factory(config)(picker_callback)

        keyboard = picker_callback.message.answer.call_args.kwargs["reply_markup"]
        buttons = [b for row in keyboard.inline_keyboard for b in row]
        assert buttons

        for button in buttons:
            apply_callback = _callback(button.callback_data)
            await allowed(config)(apply_callback)
            answer = apply_callback.answer.call_args[0][0]
            assert "Invalid" not in answer and "Unknown" not in answer, button.callback_data


@pytest.mark.asyncio
async def test_unknown_metric_is_rejected_before_anything_is_written():
    """The guard is unreachable via the two call sites -- that's the point.

    It exists so a future metric added to _METRIC_TO_SET_KEY but not to
    _UNRAID_METRICS fails loudly and cleanly instead of persisting and then
    raising, which is exactly how the array capacity button broke.
    """
    from src.bot.alert_callbacks import _apply_threshold

    config = _config()
    before = (config.array_usage_threshold, config.disk_temp_threshold)
    callback = _callback("arr_set:bogus:90")

    await _apply_threshold(callback, "bogus", 90, config, allowed_metrics={"bogus"})

    callback.answer.assert_awaited_once_with("Unknown metric")
    assert (config.array_usage_threshold, config.disk_temp_threshold) == before
    config._persist.assert_not_called()
