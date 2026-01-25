import pytest
from unittest.mock import MagicMock, AsyncMock


@pytest.mark.asyncio
async def test_resources_command_summary():
    """Test /resources shows all containers."""
    from src.bot.resources_command import resources_command
    from src.monitors.resource_monitor import ContainerStats

    mock_resource_monitor = MagicMock()
    mock_resource_monitor.get_all_stats = AsyncMock(return_value=[
        ContainerStats("plex", 65.0, 78.0, 4_200_000_000, 8_000_000_000),
        ContainerStats("radarr", 12.0, 45.0, 1_200_000_000, 4_000_000_000),
    ])

    handler = resources_command(mock_resource_monitor)

    message = MagicMock()
    message.text = "/resources"
    message.answer = AsyncMock()

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]

    assert "Container Resources" in response
    assert "plex" in response
    assert "radarr" in response
    assert "65" in response  # CPU
    assert "78" in response  # Memory


@pytest.mark.asyncio
async def test_resources_command_specific_container():
    """Test /resources <name> shows detailed view."""
    from src.bot.resources_command import resources_command
    from src.monitors.resource_monitor import ContainerStats
    from src.config import ResourceConfig

    mock_resource_monitor = MagicMock()
    mock_resource_monitor._config = ResourceConfig()
    mock_resource_monitor.get_container_stats = AsyncMock(return_value=ContainerStats(
        "plex", 65.0, 78.0, 4_200_000_000, 8_000_000_000
    ))

    handler = resources_command(mock_resource_monitor)

    message = MagicMock()
    message.text = "/resources plex"
    message.answer = AsyncMock()

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]

    assert "Resources: plex" in response
    assert "CPU:" in response
    assert "Memory:" in response
    assert "threshold" in response


@pytest.mark.asyncio
async def test_resources_command_container_not_found():
    """Test /resources <name> with unknown container."""
    from src.bot.resources_command import resources_command

    mock_resource_monitor = MagicMock()
    mock_resource_monitor.get_container_stats = AsyncMock(return_value=None)

    handler = resources_command(mock_resource_monitor)

    message = MagicMock()
    message.text = "/resources nonexistent"
    message.answer = AsyncMock()

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]

    assert "not found" in response.lower() or "not running" in response.lower()


@pytest.mark.asyncio
async def test_resources_command_no_containers():
    """Test /resources with no running containers."""
    from src.bot.resources_command import resources_command

    mock_resource_monitor = MagicMock()
    mock_resource_monitor.get_all_stats = AsyncMock(return_value=[])

    handler = resources_command(mock_resource_monitor)

    message = MagicMock()
    message.text = "/resources"
    message.answer = AsyncMock()

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]

    assert "no running containers" in response.lower() or "no containers" in response.lower()


def test_resources_command_in_help():
    """Test that /resources is documented in help text."""
    from src.bot.commands import HELP_TEXT

    assert "/resources" in HELP_TEXT
    assert "CPU" in HELP_TEXT or "resource" in HELP_TEXT.lower()
