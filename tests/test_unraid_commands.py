import pytest
from unittest.mock import MagicMock, AsyncMock
from datetime import datetime, timezone, timedelta


def test_format_uptime_iso_timestamp():
    """Test format_uptime with ISO timestamp."""
    from src.bot.unraid_commands import format_uptime
    from unittest.mock import patch

    # Mock current time to 24 days, 5 hours after boot
    boot_time = datetime(2026, 1, 2, 18, 14, 24, tzinfo=timezone.utc)
    mock_now = boot_time + timedelta(days=24, hours=5, minutes=30)

    with patch("src.bot.unraid_commands.datetime") as mock_datetime:
        mock_datetime.now.return_value = mock_now
        mock_datetime.fromisoformat = datetime.fromisoformat

        result = format_uptime("2026-01-02T18:14:24.693Z")

        assert "24 day" in result
        assert "5 hour" in result


def test_format_uptime_already_formatted():
    """Test format_uptime with already formatted string."""
    from src.bot.unraid_commands import format_uptime

    result = format_uptime("5 days, 3 hours")
    assert result == "5 days, 3 hours"


def test_format_uptime_empty():
    """Test format_uptime with empty string."""
    from src.bot.unraid_commands import format_uptime

    result = format_uptime("")
    assert result == "Unknown"


def test_format_uptime_none():
    """Test format_uptime with None."""
    from src.bot.unraid_commands import format_uptime

    result = format_uptime(None)
    assert result == "Unknown"


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


def test_unraid_commands_in_help():
    """Test that Unraid commands are in help text."""
    from src.bot.commands import HELP_TEXT

    assert "/server" in HELP_TEXT
    assert "/mute-server" in HELP_TEXT
    assert "/unmute-server" in HELP_TEXT
