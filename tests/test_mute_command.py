import pytest
from unittest.mock import MagicMock, AsyncMock


@pytest.mark.asyncio
async def test_mute_command_with_args(tmp_path):
    """Test /mute plex 2h."""
    from src.bot.mute_command import mute_command
    from src.alerts.mute_manager import MuteManager
    from src.state import ContainerStateManager
    from src.models import ContainerInfo
    from datetime import datetime

    json_file = tmp_path / "mutes.json"
    manager = MuteManager(json_path=str(json_file))
    state = ContainerStateManager()
    state.update(ContainerInfo(
        name="plex",
        status="running",
        health="healthy",
        image="linuxserver/plex",
        started_at=datetime.now()
    ))

    handler = mute_command(state, manager)

    message = MagicMock()
    message.text = "/mute plex 2h"
    message.reply_to_message = None
    message.answer = AsyncMock()
    message.from_user = MagicMock()
    message.from_user.id = 123

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]
    assert "Muted" in response
    assert "plex" in response
    assert manager.is_muted("plex")


@pytest.mark.asyncio
async def test_mute_command_reply_to_alert(tmp_path):
    """Test /mute 30m replying to alert."""
    from src.bot.mute_command import mute_command
    from src.alerts.mute_manager import MuteManager
    from src.state import ContainerStateManager

    json_file = tmp_path / "mutes.json"
    manager = MuteManager(json_path=str(json_file))
    state = ContainerStateManager()

    handler = mute_command(state, manager)

    reply_message = MagicMock()
    reply_message.text = "⚠️ ERRORS IN: plex\n\nSome errors"

    message = MagicMock()
    message.text = "/mute 30m"
    message.reply_to_message = reply_message
    message.answer = AsyncMock()
    message.from_user = MagicMock()
    message.from_user.id = 123

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]
    assert "Muted" in response
    assert "plex" in response


@pytest.mark.asyncio
async def test_mute_command_invalid_duration(tmp_path):
    """Test /mute with invalid duration."""
    from src.bot.mute_command import mute_command
    from src.alerts.mute_manager import MuteManager
    from src.state import ContainerStateManager
    from src.models import ContainerInfo
    from datetime import datetime

    json_file = tmp_path / "mutes.json"
    manager = MuteManager(json_path=str(json_file))
    state = ContainerStateManager()
    state.update(ContainerInfo(
        name="plex",
        status="running",
        health="healthy",
        image="linuxserver/plex",
        started_at=datetime.now()
    ))

    handler = mute_command(state, manager)

    message = MagicMock()
    message.text = "/mute plex forever"
    message.reply_to_message = None
    message.answer = AsyncMock()
    message.from_user = MagicMock()
    message.from_user.id = 123

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]
    assert "Invalid duration" in response


@pytest.mark.asyncio
async def test_mute_command_no_args(tmp_path):
    """Test /mute with no arguments."""
    from src.bot.mute_command import mute_command
    from src.alerts.mute_manager import MuteManager
    from src.state import ContainerStateManager

    json_file = tmp_path / "mutes.json"
    manager = MuteManager(json_path=str(json_file))
    state = ContainerStateManager()

    handler = mute_command(state, manager)

    message = MagicMock()
    message.text = "/mute"
    message.reply_to_message = None
    message.answer = AsyncMock()
    message.from_user = MagicMock()
    message.from_user.id = 123

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]
    assert "Usage" in response


@pytest.mark.asyncio
async def test_mutes_command_lists_active(tmp_path):
    """Test /mutes lists active mutes."""
    from src.bot.mute_command import mutes_command
    from src.alerts.mute_manager import MuteManager
    from datetime import timedelta

    json_file = tmp_path / "mutes.json"
    manager = MuteManager(json_path=str(json_file))
    manager.add_mute("plex", timedelta(hours=2))
    manager.add_mute("radarr", timedelta(minutes=30))

    handler = mutes_command(manager)

    message = MagicMock()
    message.text = "/mutes"
    message.answer = AsyncMock()

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]
    assert "plex" in response
    assert "radarr" in response


@pytest.mark.asyncio
async def test_mutes_command_empty(tmp_path):
    """Test /mutes with no active mutes."""
    from src.bot.mute_command import mutes_command
    from src.alerts.mute_manager import MuteManager

    json_file = tmp_path / "mutes.json"
    manager = MuteManager(json_path=str(json_file))

    handler = mutes_command(manager)

    message = MagicMock()
    message.text = "/mutes"
    message.answer = AsyncMock()

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]
    assert "No active mutes" in response or "no active" in response.lower()


@pytest.mark.asyncio
async def test_unmute_command(tmp_path):
    """Test /unmute removes mute."""
    from src.bot.mute_command import unmute_command
    from src.alerts.mute_manager import MuteManager
    from src.state import ContainerStateManager
    from src.models import ContainerInfo
    from datetime import timedelta, datetime

    json_file = tmp_path / "mutes.json"
    manager = MuteManager(json_path=str(json_file))
    manager.add_mute("plex", timedelta(hours=2))

    state = ContainerStateManager()
    state.update(ContainerInfo(
        name="plex",
        status="running",
        health="healthy",
        image="linuxserver/plex",
        started_at=datetime.now()
    ))

    handler = unmute_command(state, manager)

    message = MagicMock()
    message.text = "/unmute plex"
    message.answer = AsyncMock()

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]
    assert "Unmuted" in response
    assert not manager.is_muted("plex")


@pytest.mark.asyncio
async def test_unmute_command_not_muted(tmp_path):
    """Test /unmute when not muted."""
    from src.bot.mute_command import unmute_command
    from src.alerts.mute_manager import MuteManager
    from src.state import ContainerStateManager
    from src.models import ContainerInfo
    from datetime import datetime

    json_file = tmp_path / "mutes.json"
    manager = MuteManager(json_path=str(json_file))

    state = ContainerStateManager()
    state.update(ContainerInfo(
        name="plex",
        status="running",
        health="healthy",
        image="linuxserver/plex",
        started_at=datetime.now()
    ))

    handler = unmute_command(state, manager)

    message = MagicMock()
    message.text = "/unmute plex"
    message.answer = AsyncMock()

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]
    assert "not muted" in response.lower()


def test_mute_commands_in_help():
    """Test that /mute, /mutes, /unmute are in help text."""
    from src.bot.commands import HELP_TEXT

    assert "/mute" in HELP_TEXT
    assert "/mutes" in HELP_TEXT
    assert "/unmute" in HELP_TEXT
