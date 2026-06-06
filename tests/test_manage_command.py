"""Tests for /manage command."""

import pytest
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

from aiogram.types import Message

from src.bot.manage_command import (
    manage_command,
    manage_back_callback,
    manage_status_callback,
    manage_resources_callback,
    manage_server_callback,
    manage_disks_callback,
    manage_ignores_callback,
    manage_ignores_container_callback,
    manage_mutes_callback,
    manage_delete_ignore_callback,
    manage_delete_mute_callback,
    manage_features_callback,
    feat_image_toggle_callback,
    feat_heal_open_callback,
    feat_heal_toggle_callback,
    feat_heal_save_callback,
    AutoHealSelectionState,
    _build_manage_keyboard,
)
from src.alerts.ignore_manager import IgnoreManager
from src.alerts.mute_manager import MuteManager
from src.models import ContainerInfo
from src.state import ContainerStateManager


def _container(name: str, *, health: str | None = None) -> ContainerInfo:
    return ContainerInfo(name=name, status="running", health=health, image="img", started_at=None)


def _state_with(*names: str) -> ContainerStateManager:
    state = ContainerStateManager()
    for n in names:
        state.update(_container(n))
    return state


def _flatten(keyboard):
    return [btn for row in keyboard.inline_keyboard for btn in row]


@pytest.fixture
def ignore_manager(tmp_path):
    """Create ignore manager with temp file."""
    return IgnoreManager({}, str(tmp_path / "ignores.json"))


@pytest.fixture
def mute_manager(tmp_path):
    """Create mute manager with temp file."""
    return MuteManager(str(tmp_path / "mutes.json"))


@pytest.mark.asyncio
async def test_manage_command_shows_buttons():
    """Test /manage shows all 6 buttons in 3 rows."""
    handler = manage_command()
    message = AsyncMock()

    await handler(message)

    message.answer.assert_called_once()
    call_args = message.answer.call_args
    keyboard = call_args.kwargs.get("reply_markup")
    # First row: Status and Resources
    assert keyboard.inline_keyboard[0][0].callback_data == "manage:status"
    assert keyboard.inline_keyboard[0][1].callback_data == "manage:resources"
    # Second row: Server and Disks
    assert keyboard.inline_keyboard[1][0].callback_data == "manage:server"
    assert keyboard.inline_keyboard[1][1].callback_data == "manage:disks"
    # Third row: Ignores and Mutes
    assert keyboard.inline_keyboard[2][0].callback_data == "manage:ignores"
    assert keyboard.inline_keyboard[2][1].callback_data == "manage:mutes"


def test_build_manage_keyboard():
    """Test _build_manage_keyboard helper returns correct structure."""
    keyboard = _build_manage_keyboard()
    assert len(keyboard.inline_keyboard) == 4
    assert keyboard.inline_keyboard[0][0].callback_data == "manage:status"
    assert keyboard.inline_keyboard[2][1].callback_data == "manage:mutes"
    assert keyboard.inline_keyboard[3][0].callback_data == "manage:features"


@pytest.mark.asyncio
async def test_manage_back_callback():
    """Test manage:back re-renders dashboard."""
    handler = manage_back_callback()
    callback = AsyncMock()
    callback.data = "manage:back"
    callback.message = AsyncMock()

    await handler(callback)

    callback.answer.assert_called_once()
    callback.message.edit_text.assert_called_once()
    call_args = callback.message.edit_text.call_args
    keyboard = call_args.kwargs.get("reply_markup")
    assert keyboard is not None
    assert keyboard.inline_keyboard[0][0].callback_data == "manage:status"


@pytest.mark.asyncio
async def test_manage_ignores_no_ignores(ignore_manager):
    """Test manage ignores with no runtime ignores."""
    handler = manage_ignores_callback(ignore_manager)
    callback = AsyncMock()
    callback.data = "manage:ignores"
    callback.message = AsyncMock()

    await handler(callback)

    callback.answer.assert_called_with("No runtime ignores to manage")


@pytest.mark.asyncio
async def test_manage_ignores_shows_containers_with_back(ignore_manager):
    """Test manage ignores shows containers with ignores and a Back button."""
    # Add some ignores
    ignore_manager.add_ignore("plex", "test error")
    ignore_manager.add_ignore("radarr", "another error")

    handler = manage_ignores_callback(ignore_manager)
    callback = AsyncMock()
    callback.data = "manage:ignores"
    callback.message = AsyncMock()

    await handler(callback)

    callback.answer.assert_called_once()
    callback.message.edit_text.assert_called_once()
    # Check that container buttons are shown
    call_args = callback.message.edit_text.call_args
    keyboard = call_args.kwargs.get("reply_markup")
    assert keyboard is not None

    # Last row should be Back button
    last_row = keyboard.inline_keyboard[-1]
    assert last_row[0].callback_data == "manage:back"
    assert "Back" in last_row[0].text


@pytest.mark.asyncio
async def test_manage_ignores_container_shows_delete_buttons(ignore_manager):
    """Test selecting a container shows delete buttons per ignore."""
    ignore_manager.add_ignore("plex", "test error 1")
    ignore_manager.add_ignore("plex", "test error 2")

    handler = manage_ignores_container_callback(ignore_manager)
    callback = AsyncMock()
    callback.data = "manage:ignores:plex"
    callback.from_user = MagicMock()
    callback.from_user.id = 123
    callback.message = AsyncMock()

    await handler(callback)

    callback.answer.assert_called_once()
    call_args = callback.message.edit_text.call_args
    text = call_args.args[0]
    assert "1." in text
    assert "plex" in text

    # Check delete buttons
    keyboard = call_args.kwargs.get("reply_markup")
    assert keyboard is not None
    # Should have delete buttons for each ignore + back button
    all_callbacks = [
        btn.callback_data
        for row in keyboard.inline_keyboard
        for btn in row
    ]
    assert any(cb.startswith("mdi:plex:") for cb in all_callbacks)
    assert "manage:back" in all_callbacks


@pytest.mark.asyncio
async def test_manage_delete_ignore(ignore_manager):
    """Test delete ignore button removes the ignore."""
    ignore_manager.add_ignore("plex", "test error 1")
    ignore_manager.add_ignore("plex", "test error 2")

    handler = manage_delete_ignore_callback(ignore_manager)
    callback = AsyncMock()
    callback.data = "mdi:plex:0"  # Delete first ignore (index 0)
    callback.message = AsyncMock()
    callback.message.edit_text = AsyncMock()

    await handler(callback)

    callback.answer.assert_called_with("Ignore removed")
    # First ignore should be removed
    ignores = ignore_manager.get_runtime_ignores("plex")
    assert len(ignores) == 1
    assert ignores[0][1] == "test error 2"


@pytest.mark.asyncio
async def test_manage_delete_ignore_clears_all(ignore_manager):
    """Test deleting last ignore shows 'all cleared'."""
    ignore_manager.add_ignore("plex", "test error")

    handler = manage_delete_ignore_callback(ignore_manager)
    callback = AsyncMock()
    callback.data = "mdi:plex:0"
    callback.message = AsyncMock()
    callback.message.edit_text = AsyncMock()

    await handler(callback)

    callback.answer.assert_called_with("Ignore removed")
    assert len(ignore_manager.get_runtime_ignores("plex")) == 0


@pytest.mark.asyncio
async def test_manage_delete_ignore_container_with_colon(ignore_manager):
    """Test delete ignore with container name containing colons."""
    ignore_manager.add_ignore("my:container:name", "some error")

    handler = manage_delete_ignore_callback(ignore_manager)
    callback = AsyncMock()
    callback.data = "mdi:my:container:name:0"
    callback.message = AsyncMock()
    callback.message.edit_text = AsyncMock()

    await handler(callback)

    callback.answer.assert_called_with("Ignore removed")
    assert len(ignore_manager.get_runtime_ignores("my:container:name")) == 0


@pytest.mark.asyncio
async def test_manage_mutes_no_mutes(mute_manager):
    """Test manage mutes with no active mutes."""
    handler = manage_mutes_callback(mute_manager, None, None)
    callback = AsyncMock()
    callback.data = "manage:mutes"
    callback.from_user = MagicMock()
    callback.from_user.id = 123
    callback.message = AsyncMock()

    await handler(callback)

    callback.answer.assert_called_with("No active mutes")


@pytest.mark.asyncio
async def test_manage_mutes_shows_delete_buttons(mute_manager):
    """Test manage mutes shows delete buttons and back button."""
    mute_manager.add_mute("plex", timedelta(hours=1))

    handler = manage_mutes_callback(mute_manager, None, None)
    callback = AsyncMock()
    callback.data = "manage:mutes"
    callback.from_user = MagicMock()
    callback.from_user.id = 123
    callback.message = AsyncMock()

    await handler(callback)

    callback.answer.assert_called_once()
    call_args = callback.message.edit_text.call_args
    text = call_args.args[0]
    assert "1." in text
    assert "plex" in text

    keyboard = call_args.kwargs.get("reply_markup")
    assert keyboard is not None
    all_callbacks = [
        btn.callback_data
        for row in keyboard.inline_keyboard
        for btn in row
    ]
    assert any(cb.startswith("mdm:container:") for cb in all_callbacks)
    assert "manage:back" in all_callbacks


@pytest.mark.asyncio
async def test_manage_delete_mute(mute_manager):
    """Test delete mute button removes the mute."""
    mute_manager.add_mute("plex", timedelta(hours=1))

    handler = manage_delete_mute_callback(mute_manager, None, None)
    callback = AsyncMock()
    callback.data = "mdm:container:plex"
    callback.message = AsyncMock()
    callback.message.edit_text = AsyncMock()

    await handler(callback)

    callback.answer.assert_called_with("Unmuted plex")
    assert len(mute_manager.get_active_mutes()) == 0


@pytest.mark.asyncio
async def test_manage_delete_server_mute():
    """Test delete server mute button."""
    mute_manager = MagicMock()
    server_mute_manager = MagicMock()
    server_mute_manager.unmute_server.return_value = True
    server_mute_manager.get_active_mutes.return_value = []

    handler = manage_delete_mute_callback(mute_manager, server_mute_manager, None)
    callback = AsyncMock()
    callback.data = "mdm:server:server"
    callback.message = AsyncMock()
    callback.message.edit_text = AsyncMock()

    # Mock _collect_mutes to return empty list after unmute
    mute_manager.get_active_mutes.return_value = []

    await handler(callback)

    server_mute_manager.unmute_server.assert_called_once()
    callback.answer.assert_called_with("Unmuted Server alerts")


@pytest.mark.asyncio
async def test_manage_delete_array_mute():
    """Test delete array mute button."""
    mute_manager = MagicMock()
    array_mute_manager = MagicMock()
    array_mute_manager.unmute_array.return_value = True
    array_mute_manager.get_mute_expiry.return_value = None

    handler = manage_delete_mute_callback(mute_manager, None, array_mute_manager)
    callback = AsyncMock()
    callback.data = "mdm:array:array"
    callback.message = AsyncMock()
    callback.message.edit_text = AsyncMock()

    mute_manager.get_active_mutes.return_value = []

    await handler(callback)

    array_mute_manager.unmute_array.assert_called_once()
    callback.answer.assert_called_with("Unmuted Array alerts")


@pytest.mark.asyncio
async def test_manage_status_callback():
    """Test status button shows container status."""
    state = ContainerStateManager()

    handler = manage_status_callback(state)
    callback = AsyncMock()
    callback.data = "manage:status"
    callback.message = AsyncMock()

    await handler(callback)

    callback.answer.assert_called_once()
    callback.message.edit_text.assert_called_once()
    # Check status summary is shown
    call_args = callback.message.edit_text.call_args
    assert "Container Status" in call_args.args[0]


@pytest.mark.asyncio
async def test_manage_resources_callback_no_monitor():
    """Test resources button with no resource monitor."""
    handler = manage_resources_callback(None)
    callback = AsyncMock()
    callback.data = "manage:resources"
    callback.message = AsyncMock()

    await handler(callback)

    callback.answer.assert_called_once()
    callback.message.edit_text.assert_called_once()
    call_args = callback.message.edit_text.call_args
    assert call_args.args[0] == "Resource monitoring not enabled."


@pytest.mark.asyncio
async def test_manage_server_callback_no_monitor():
    """Test server button with no system monitor."""
    handler = manage_server_callback(None)
    callback = AsyncMock()
    callback.data = "manage:server"
    callback.message = AsyncMock()

    await handler(callback)

    callback.answer.assert_called_once()
    callback.message.edit_text.assert_called_once()
    call_args = callback.message.edit_text.call_args
    assert call_args.args[0] == "\U0001f5a5\ufe0f Unraid monitoring not configured."


@pytest.mark.asyncio
async def test_manage_disks_callback_no_monitor():
    """Test disks button with no system monitor."""
    handler = manage_disks_callback(None)
    callback = AsyncMock()
    callback.data = "manage:disks"
    callback.message = AsyncMock()

    await handler(callback)

    callback.answer.assert_called_once()
    callback.message.edit_text.assert_called_once()
    call_args = callback.message.edit_text.call_args
    assert call_args.args[0] == "\U0001f4be Unraid monitoring not configured."


def test_manage_command_in_help():
    """Test /manage is listed in help section content."""
    from src.bot.commands import _HELP_SECTIONS
    setup_content = _HELP_SECTIONS["setup"][2]
    assert "/manage" in setup_content


# ---------------------------------------------------------------------------
# Features panel + auto-heal picker
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_features_panel_shows_enable_when_off():
    """Image updates off -> Enable button; auto-heal off -> shows Off."""
    auto_heal = MagicMock(enabled=False, containers=[])
    handler = manage_features_callback(image_update_monitor=None, auto_heal_config=auto_heal)
    callback = AsyncMock()
    callback.message = AsyncMock()

    await handler(callback)

    callback.message.edit_text.assert_called_once()
    call_args = callback.message.edit_text.call_args
    text = call_args.args[0]
    assert "Image updates" in text and "Auto-heal" in text
    callbacks = [b.callback_data for b in _flatten(call_args.kwargs["reply_markup"])]
    assert "feat:img:on" in callbacks
    assert "feat:heal" in callbacks
    assert "manage:back" in callbacks


@pytest.mark.asyncio
async def test_features_panel_shows_disable_when_on():
    """Image updates running -> Disable button; auto-heal shows count."""
    monitor = MagicMock(is_running=True)
    auto_heal = MagicMock(enabled=True, containers=["plex", "sonarr"])
    handler = manage_features_callback(image_update_monitor=monitor, auto_heal_config=auto_heal)
    callback = AsyncMock()
    callback.message = AsyncMock()

    await handler(callback)

    call_args = callback.message.edit_text.call_args
    text = call_args.args[0]
    assert "2 container(s)" in text
    callbacks = [b.callback_data for b in _flatten(call_args.kwargs["reply_markup"])]
    assert "feat:img:off" in callbacks


@pytest.mark.asyncio
async def test_feat_image_toggle_enable_persists_and_restarts():
    image_cfg = MagicMock()
    restart_cb = AsyncMock()
    handler = feat_image_toggle_callback(image_cfg, restart_cb)
    callback = AsyncMock()
    callback.data = "feat:img:on"
    callback.message = AsyncMock()

    await handler(callback)

    image_cfg.set_enabled.assert_called_once_with(True)
    restart_cb.assert_awaited_once()


@pytest.mark.asyncio
async def test_feat_image_toggle_disable():
    image_cfg = MagicMock()
    restart_cb = AsyncMock()
    handler = feat_image_toggle_callback(image_cfg, restart_cb)
    callback = AsyncMock()
    callback.data = "feat:img:off"
    callback.message = AsyncMock()

    await handler(callback)

    image_cfg.set_enabled.assert_called_once_with(False)
    restart_cb.assert_awaited_once()


@pytest.mark.asyncio
async def test_feat_image_toggle_without_restart_cb():
    """No restart capability -> persists and tells the user to restart."""
    image_cfg = MagicMock()
    handler = feat_image_toggle_callback(image_cfg, None)
    callback = AsyncMock()
    callback.data = "feat:img:on"
    callback.message = AsyncMock()

    await handler(callback)

    image_cfg.set_enabled.assert_called_once_with(True)
    text = callback.message.edit_text.call_args.args[0]
    assert "Restart the bot" in text


@pytest.mark.asyncio
async def test_feat_heal_open_seeds_selection_and_excludes_protected():
    state = _state_with("plex", "sonarr", "traefik")
    auto_heal = MagicMock(containers=["plex"])
    selection = AutoHealSelectionState()
    handler = feat_heal_open_callback(
        state, auto_heal, selection, protected_containers=["traefik"],
    )
    callback = AsyncMock()
    callback.from_user.id = 123
    callback.message = AsyncMock()

    await handler(callback)

    # Seeded from config
    assert selection.get(123) == {"plex"}
    # Keyboard excludes protected, marks selected with a check
    keyboard = callback.message.edit_text.call_args.kwargs["reply_markup"]
    toggle_btns = [b for b in _flatten(keyboard) if b.callback_data.startswith("fh_tog:")]
    names = {b.callback_data.split(":", 1)[1] for b in toggle_btns}
    assert names == {"plex", "sonarr"}
    plex_btn = next(b for b in toggle_btns if b.callback_data == "fh_tog:plex")
    sonarr_btn = next(b for b in toggle_btns if b.callback_data == "fh_tog:sonarr")
    assert plex_btn.text.startswith("✅")
    assert sonarr_btn.text.startswith("❌")
    # Save + Back present
    cbs = [b.callback_data for b in _flatten(keyboard)]
    assert "fh_save" in cbs and "manage:features" in cbs


@pytest.mark.asyncio
async def test_feat_heal_toggle_updates_selection():
    state = _state_with("plex", "sonarr")
    selection = AutoHealSelectionState()
    selection.init(123, ["plex"])
    handler = feat_heal_toggle_callback(state, selection)
    callback = AsyncMock()
    callback.data = "fh_tog:sonarr"
    callback.from_user.id = 123
    callback.message = AsyncMock(spec=Message)

    await handler(callback)

    assert selection.get(123) == {"plex", "sonarr"}
    callback.message.edit_reply_markup.assert_called_once()


@pytest.mark.asyncio
async def test_feat_heal_toggle_rejects_unknown_container():
    """Spoofed or stale callback data must not enter the selection."""
    state = _state_with("plex", "sonarr")
    selection = AutoHealSelectionState()
    selection.init(123, ["plex"])
    handler = feat_heal_toggle_callback(state, selection)
    callback = AsyncMock()
    callback.data = "fh_tog:../../etc/passwd"
    callback.from_user.id = 123
    callback.message = AsyncMock(spec=Message)

    await handler(callback)

    assert selection.get(123) == {"plex"}
    callback.answer.assert_awaited_once_with("Unknown container")
    callback.message.edit_reply_markup.assert_not_called()


@pytest.mark.asyncio
async def test_feat_heal_toggle_rejects_protected_container():
    """Protected containers are excluded from the picker and from spoofed toggles."""
    state = _state_with("plex", "traefik")
    selection = AutoHealSelectionState()
    selection.init(123, [])
    handler = feat_heal_toggle_callback(state, selection, protected_containers=["traefik"])
    callback = AsyncMock()
    callback.data = "fh_tog:traefik"
    callback.from_user.id = 123
    callback.message = AsyncMock(spec=Message)

    await handler(callback)

    assert selection.get(123) == set()
    callback.answer.assert_awaited_once_with("Unknown container")


@pytest.mark.asyncio
async def test_feat_heal_save_applies_and_rerenders():
    auto_heal = MagicMock(enabled=True, containers=["plex", "sonarr"])
    selection = AutoHealSelectionState()
    selection.init(123, ["sonarr", "plex"])
    handler = feat_heal_save_callback(auto_heal, selection, image_update_monitor=None)
    callback = AsyncMock()
    callback.from_user.id = 123
    callback.message = AsyncMock()

    await handler(callback)

    # Persisted as a sorted list (live-applied, no restart)
    auto_heal.set_containers.assert_called_once_with(["plex", "sonarr"])
    # Selection cleared and features panel re-rendered
    assert selection.get(123) == set()
    callback.message.edit_text.assert_called_once()


@pytest.mark.asyncio
async def test_feat_heal_save_empty_disables():
    auto_heal = MagicMock(enabled=False, containers=[])
    selection = AutoHealSelectionState()
    selection.init(123, [])
    handler = feat_heal_save_callback(auto_heal, selection)
    callback = AsyncMock()
    callback.from_user.id = 123
    callback.message = AsyncMock()

    await handler(callback)

    auto_heal.set_containers.assert_called_once_with([])
    callback.answer.assert_called_with("Auto-heal disabled")
