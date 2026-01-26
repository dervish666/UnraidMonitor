import pytest
from unittest.mock import MagicMock, AsyncMock


@pytest.mark.asyncio
async def test_server_command_shows_metrics():
    """Test /server shows system metrics."""
    from src.bot.unraid_commands import server_command

    mock_monitor = MagicMock()
    mock_monitor.get_current_metrics = AsyncMock(return_value={
        "cpu_percent": 25.5,
        "cpu_temperature": 45.0,
        "memory_percent": 60.0,
        "memory_used": 1024 * 1024 * 1024 * 32,
        "uptime": "5 days, 3 hours",
    })

    handler = server_command(mock_monitor)

    message = MagicMock()
    message.text = "/server"
    message.answer = AsyncMock()

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]

    assert "25.5" in response or "25.5%" in response  # CPU
    assert "45" in response  # Temp
    assert "60" in response  # Memory
    assert "5 days" in response  # Uptime


@pytest.mark.asyncio
async def test_server_command_detailed():
    """Test /server detailed shows more info."""
    from src.bot.unraid_commands import server_command

    mock_monitor = MagicMock()
    mock_monitor.get_current_metrics = AsyncMock(return_value={
        "cpu_percent": 25.5,
        "cpu_temperature": 45.0,
        "cpu_power": 55.0,
        "memory_percent": 60.0,
        "memory_used": 1024 * 1024 * 1024 * 32,
        "swap_percent": 5.0,
        "uptime": "5 days, 3 hours",
    })

    handler = server_command(mock_monitor)

    message = MagicMock()
    message.text = "/server detailed"
    message.answer = AsyncMock()

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]

    assert "Swap" in response or "swap" in response


@pytest.mark.asyncio
async def test_server_command_not_connected():
    """Test /server when Unraid not connected."""
    from src.bot.unraid_commands import server_command

    mock_monitor = MagicMock()
    mock_monitor.get_current_metrics = AsyncMock(return_value=None)

    handler = server_command(mock_monitor)

    message = MagicMock()
    message.text = "/server"
    message.answer = AsyncMock()

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]

    assert "unavailable" in response.lower() or "error" in response.lower()


@pytest.mark.asyncio
async def test_mute_server_command(tmp_path):
    """Test /mute-server mutes all server alerts."""
    from src.bot.unraid_commands import mute_server_command
    from src.alerts.server_mute_manager import ServerMuteManager

    json_file = tmp_path / "server_mutes.json"
    mute_manager = ServerMuteManager(json_path=str(json_file))

    handler = mute_server_command(mute_manager)

    message = MagicMock()
    message.text = "/mute-server 2h"
    message.answer = AsyncMock()

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]

    assert "Muted" in response
    assert mute_manager.is_server_muted()


@pytest.mark.asyncio
async def test_mute_server_command_no_duration(tmp_path):
    """Test /mute-server without duration shows usage."""
    from src.bot.unraid_commands import mute_server_command
    from src.alerts.server_mute_manager import ServerMuteManager

    json_file = tmp_path / "server_mutes.json"
    mute_manager = ServerMuteManager(json_path=str(json_file))

    handler = mute_server_command(mute_manager)

    message = MagicMock()
    message.text = "/mute-server"
    message.answer = AsyncMock()

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]

    assert "Usage" in response


@pytest.mark.asyncio
async def test_unmute_server_command(tmp_path):
    """Test /unmute-server unmutes all server alerts."""
    from src.bot.unraid_commands import unmute_server_command
    from src.alerts.server_mute_manager import ServerMuteManager
    from datetime import timedelta

    json_file = tmp_path / "server_mutes.json"
    mute_manager = ServerMuteManager(json_path=str(json_file))
    mute_manager.mute_server(timedelta(hours=2))

    handler = unmute_server_command(mute_manager)

    message = MagicMock()
    message.text = "/unmute-server"
    message.answer = AsyncMock()

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]

    assert "Unmuted" in response
    assert not mute_manager.is_server_muted()
