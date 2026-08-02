"""Tests for the re-mute buttons attached to mute-expiry notifications.

The container branch used to emit seconds (3600/86400) into mute_callback's
minutes API, which clamps rather than rejects -- so "Re-mute 1h" silently meant
60 hours.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.alerts.array_mute_manager import ArrayMuteManager
from src.alerts.mute_manager import MuteManager
from src.alerts.server_mute_manager import ServerMuteManager
from src.bot.alert_callbacks import mute_callback
from src.models import ContainerInfo
from src.monitor_callbacks import _remute_keyboard
from src.state import ContainerStateManager

_HOUR_LABEL = "🔇 Re-mute 1h"
_DAY_LABEL = "🔇 Re-mute 24h"


def _values(manager, key: str) -> dict[str, int]:
    keyboard = _remute_keyboard(manager, key)
    buttons = [b for row in keyboard.inline_keyboard for b in row]
    return {b.text: int(b.callback_data.rsplit(":", 1)[1]) for b in buttons}


def test_container_remute_buttons_are_minutes(tmp_path):
    manager = MuteManager(json_path=str(tmp_path / "mutes.json"))

    values = _values(manager, "plex")

    assert values[_HOUR_LABEL] == 60
    assert values[_DAY_LABEL] == 1440


def test_server_remute_buttons_are_minutes(tmp_path):
    manager = ServerMuteManager(json_path=str(tmp_path / "server.json"))

    values = _values(manager, "server")

    assert values[_HOUR_LABEL] == 60
    assert values[_DAY_LABEL] == 1440


def test_array_remute_buttons_are_minutes(tmp_path):
    manager = ArrayMuteManager(json_path=str(tmp_path / "array.json"))

    values = _values(manager, "array")

    assert values[_HOUR_LABEL] == 60
    assert values[_DAY_LABEL] == 1440


def test_all_manager_types_agree_on_duration(tmp_path):
    """The three branches build separate keyboards -- they must not drift."""
    managers = [
        MuteManager(json_path=str(tmp_path / "m.json")),
        ServerMuteManager(json_path=str(tmp_path / "s.json")),
        ArrayMuteManager(json_path=str(tmp_path / "a.json")),
    ]

    durations = {tuple(sorted(_values(m, "key").values())) for m in managers}

    assert len(durations) == 1


@pytest.mark.asyncio
async def test_container_remute_button_mutes_for_one_hour(tmp_path):
    """End-to-end: feed the button's own payload back through the handler."""
    manager = MuteManager(json_path=str(tmp_path / "mutes.json"))
    state = ContainerStateManager()
    state.update(
        ContainerInfo(
            name="plex", status="running", health=None, image="plex:latest", started_at=None,
        )
    )

    keyboard = _remute_keyboard(manager, "plex")
    hour_button = next(
        b for row in keyboard.inline_keyboard for b in row if b.text == _HOUR_LABEL
    )

    callback = MagicMock()
    callback.data = hour_button.callback_data
    callback.answer = AsyncMock()
    callback.message = None

    await mute_callback(state, manager)(callback)

    assert "1 hour" in callback.answer.call_args[0][0]
