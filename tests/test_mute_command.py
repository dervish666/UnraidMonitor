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
