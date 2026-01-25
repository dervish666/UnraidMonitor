"""
Integration test - verifies all components work together.
Run with: pytest tests/test_integration.py -v
"""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch


@pytest.mark.asyncio
async def test_full_status_flow():
    """Test: Docker data flows through to Telegram response."""
    from src.state import ContainerStateManager
    from src.models import ContainerInfo
    from src.bot.commands import status_command

    # 1. Set up state (simulating Docker monitor)
    state = ContainerStateManager()
    state.update(ContainerInfo("plex", "running", "healthy", "linuxserver/plex", None))
    state.update(ContainerInfo("radarr", "running", None, "linuxserver/radarr", None))
    state.update(ContainerInfo("backup", "exited", None, "backup:latest", None))

    # 2. Create command handler
    handler = status_command(state)

    # 3. Simulate Telegram message
    message = MagicMock()
    message.text = "/status"
    message.answer = AsyncMock()

    await handler(message)

    # 4. Verify response contains expected data
    response = message.answer.call_args[0][0]
    assert "Running: 2" in response
    assert "Stopped: 1" in response
    assert "backup" in response


@pytest.mark.asyncio
async def test_docker_event_updates_state():
    """Test: Docker events update state manager."""
    from src.monitors.docker_events import parse_container
    from src.state import ContainerStateManager

    state = ContainerStateManager()

    # Simulate container
    mock_container = MagicMock()
    mock_container.name = "radarr"
    mock_container.status = "running"
    mock_container.image.tags = ["linuxserver/radarr:latest"]
    mock_container.attrs = {"State": {"Health": {"Status": "healthy"}}}

    # Parse and update state
    info = parse_container(mock_container)
    state.update(info)

    # Verify state updated
    result = state.get("radarr")
    assert result is not None
    assert result.status == "running"
    assert result.health == "healthy"
