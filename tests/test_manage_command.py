"""Tests for /manage command."""

import pytest
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

from src.bot.manage_command import (
    manage_command,
    manage_status_callback,
    manage_resources_callback,
    manage_server_callback,
    manage_disks_callback,
    manage_ignores_callback,
    manage_ignores_container_callback,
    manage_mutes_callback,
    manage_selection_handler,
    ManageSelectionState,
)
from src.alerts.ignore_manager import IgnoreManager
from src.alerts.mute_manager import MuteManager
from src.state import ContainerStateManager


@pytest.fixture
def ignore_manager(tmp_path):
    """Create ignore manager with temp file."""
    return IgnoreManager({}, str(tmp_path / "ignores.json"))


@pytest.fixture
def mute_manager(tmp_path):
    """Create mute manager with temp file."""
    return MuteManager(str(tmp_path / "mutes.json"))


@pytest.fixture
def manage_state():
    """Create manage selection state."""
    return ManageSelectionState()


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
async def test_manage_ignores_shows_containers(ignore_manager):
    """Test manage ignores shows containers with ignores."""
    # Add some ignores
    ignore_manager.add_ignore("plex", "test error")
    ignore_manager.add_ignore("radarr", "another error")

    handler = manage_ignores_callback(ignore_manager)
    callback = AsyncMock()
    callback.data = "manage:ignores"
    callback.message = AsyncMock()

    await handler(callback)

    callback.answer.assert_called_once()
    callback.message.answer.assert_called_once()
    # Check that container buttons are shown
    call_args = callback.message.answer.call_args
    assert "reply_markup" in call_args.kwargs


@pytest.mark.asyncio
async def test_manage_ignores_container_shows_list(ignore_manager, manage_state):
    """Test selecting a container shows numbered list."""
    ignore_manager.add_ignore("plex", "test error 1")
    ignore_manager.add_ignore("plex", "test error 2")

    handler = manage_ignores_container_callback(ignore_manager, manage_state)
    callback = AsyncMock()
    callback.data = "manage:ignores:plex"
    callback.from_user = MagicMock()
    callback.from_user.id = 123
    callback.message = AsyncMock()

    await handler(callback)

    callback.answer.assert_called_once()
    # Check numbered list is shown
    call_args = callback.message.answer.call_args
    assert "1." in call_args.args[0]
    assert "plex" in call_args.args[0]

    # Check pending state is set
    assert manage_state.has_pending(123)


@pytest.mark.asyncio
async def test_manage_mutes_no_mutes(mute_manager, manage_state):
    """Test manage mutes with no active mutes."""
    handler = manage_mutes_callback(mute_manager, None, None, manage_state)
    callback = AsyncMock()
    callback.data = "manage:mutes"
    callback.from_user = MagicMock()
    callback.from_user.id = 123
    callback.message = AsyncMock()

    await handler(callback)

    callback.answer.assert_called_with("No active mutes")


@pytest.mark.asyncio
async def test_manage_mutes_shows_list(mute_manager, manage_state):
    """Test manage mutes shows numbered list."""
    mute_manager.add_mute("plex", timedelta(hours=1))

    handler = manage_mutes_callback(mute_manager, None, None, manage_state)
    callback = AsyncMock()
    callback.data = "manage:mutes"
    callback.from_user = MagicMock()
    callback.from_user.id = 123
    callback.message = AsyncMock()

    await handler(callback)

    callback.answer.assert_called_once()
    call_args = callback.message.answer.call_args
    assert "1." in call_args.args[0]
    assert "plex" in call_args.args[0]


@pytest.mark.asyncio
async def test_manage_selection_removes_ignore(ignore_manager, mute_manager, manage_state):
    """Test selecting a number removes the ignore."""
    ignore_manager.add_ignore("plex", "test error")

    # Set up pending state
    ignores = ignore_manager.get_runtime_ignores("plex")
    manage_state.set_pending_ignore(123, "plex", ignores)

    handler = manage_selection_handler(ignore_manager, mute_manager, None, None, manage_state)
    message = AsyncMock()
    message.text = "1"
    message.from_user = MagicMock()
    message.from_user.id = 123

    await handler(message)

    message.answer.assert_called_once()
    assert "Removed" in message.answer.call_args.args[0]

    # Verify ignore was removed
    assert len(ignore_manager.get_runtime_ignores("plex")) == 0


@pytest.mark.asyncio
async def test_manage_selection_removes_mute(ignore_manager, mute_manager, manage_state):
    """Test selecting a number removes the mute."""
    mute_manager.add_mute("plex", timedelta(hours=1))

    # Set up pending state
    manage_state.set_pending_mute(123, [("container", "plex")])

    handler = manage_selection_handler(ignore_manager, mute_manager, None, None, manage_state)
    message = AsyncMock()
    message.text = "1"
    message.from_user = MagicMock()
    message.from_user.id = 123

    await handler(message)

    message.answer.assert_called_once()
    assert "Unmuted" in message.answer.call_args.args[0]

    # Verify mute was removed
    assert len(mute_manager.get_active_mutes()) == 0


@pytest.mark.asyncio
async def test_manage_selection_cancel(ignore_manager, mute_manager, manage_state):
    """Test cancel clears pending state."""
    manage_state.set_pending_ignore(123, "plex", [(0, "test", None)])

    handler = manage_selection_handler(ignore_manager, mute_manager, None, None, manage_state)
    message = AsyncMock()
    message.text = "cancel"
    message.from_user = MagicMock()
    message.from_user.id = 123

    await handler(message)

    message.answer.assert_called_with("Cancelled.")
    assert not manage_state.has_pending(123)


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
    callback.message.answer.assert_called_once()
    # Check status summary is shown
    call_args = callback.message.answer.call_args
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
    callback.message.answer.assert_called_with("Resource monitoring not enabled.")


@pytest.mark.asyncio
async def test_manage_server_callback_no_monitor():
    """Test server button with no system monitor."""
    handler = manage_server_callback(None)
    callback = AsyncMock()
    callback.data = "manage:server"
    callback.message = AsyncMock()

    await handler(callback)

    callback.answer.assert_called_once()
    callback.message.answer.assert_called_with("🖥️ Unraid monitoring not configured.")


@pytest.mark.asyncio
async def test_manage_disks_callback_no_monitor():
    """Test disks button with no system monitor."""
    handler = manage_disks_callback(None)
    callback = AsyncMock()
    callback.data = "manage:disks"
    callback.message = AsyncMock()

    await handler(callback)

    callback.answer.assert_called_once()
    callback.message.answer.assert_called_with("💾 Unraid monitoring not configured.")


def test_manage_command_in_help():
    """Test /manage is listed in help."""
    from src.bot.commands import HELP_TEXT
    assert "/manage" in HELP_TEXT
