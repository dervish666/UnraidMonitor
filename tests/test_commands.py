import pytest
from unittest.mock import MagicMock, AsyncMock


@pytest.mark.asyncio
async def test_help_command_returns_help_text():
    from src.bot.commands import help_command
    from src.state import ContainerStateManager

    state = ContainerStateManager()
    handler = help_command(state)

    message = MagicMock()
    message.answer = AsyncMock()

    await handler(message)

    message.answer.assert_called_once()
    call_args = message.answer.call_args[0][0]
    assert "/status" in call_args
    assert "/help" in call_args


@pytest.mark.asyncio
async def test_status_command_shows_summary():
    from src.bot.commands import status_command
    from src.state import ContainerStateManager
    from src.models import ContainerInfo

    state = ContainerStateManager()
    state.update(ContainerInfo("plex", "running", "healthy", "img", None))
    state.update(ContainerInfo("radarr", "running", "unhealthy", "img", None))
    state.update(ContainerInfo("backup", "exited", None, "img", None))

    handler = status_command(state)

    message = MagicMock()
    message.text = "/status"
    message.answer = AsyncMock()

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]
    assert "Running: 2" in response
    assert "Stopped: 1" in response
    assert "Unhealthy: 1" in response
    assert "backup" in response  # stopped container listed
    assert "radarr" in response  # unhealthy container listed


@pytest.mark.asyncio
async def test_status_command_shows_container_details():
    from src.bot.commands import status_command
    from src.state import ContainerStateManager
    from src.models import ContainerInfo
    from datetime import datetime

    state = ContainerStateManager()
    state.update(ContainerInfo(
        "radarr", "running", "healthy",
        "linuxserver/radarr:latest",
        datetime(2025, 1, 25, 10, 0, 0),
    ))

    handler = status_command(state)

    message = MagicMock()
    message.text = "/status radarr"
    message.answer = AsyncMock()

    await handler(message)

    response = message.answer.call_args[0][0]
    assert "radarr" in response
    assert "running" in response.lower()
    assert "healthy" in response.lower()


@pytest.mark.asyncio
async def test_status_command_partial_match():
    from src.bot.commands import status_command
    from src.state import ContainerStateManager
    from src.models import ContainerInfo

    state = ContainerStateManager()
    state.update(ContainerInfo("radarr", "running", None, "img", None))

    handler = status_command(state)

    message = MagicMock()
    message.text = "/status rad"
    message.answer = AsyncMock()

    await handler(message)

    response = message.answer.call_args[0][0]
    assert "radarr" in response


@pytest.mark.asyncio
async def test_status_command_multiple_matches():
    from src.bot.commands import status_command
    from src.state import ContainerStateManager
    from src.models import ContainerInfo

    state = ContainerStateManager()
    state.update(ContainerInfo("radarr", "running", None, "img", None))
    state.update(ContainerInfo("radarr-test", "running", None, "img", None))

    handler = status_command(state)

    message = MagicMock()
    message.text = "/status radar"
    message.answer = AsyncMock()

    await handler(message)

    response = message.answer.call_args[0][0]
    assert "radarr" in response
    assert "radarr-test" in response
    assert "multiple" in response.lower() or "matches" in response.lower()


@pytest.mark.asyncio
async def test_status_command_no_match():
    from src.bot.commands import status_command
    from src.state import ContainerStateManager

    state = ContainerStateManager()
    handler = status_command(state)

    message = MagicMock()
    message.text = "/status nonexistent"
    message.answer = AsyncMock()

    await handler(message)

    response = message.answer.call_args[0][0]
    assert "not found" in response.lower() or "no container" in response.lower()


@pytest.mark.asyncio
async def test_logs_command_returns_container_logs():
    """Test that /logs radarr returns logs with proper formatting."""
    from src.bot.commands import logs_command
    from src.state import ContainerStateManager
    from src.models import ContainerInfo
    import docker

    state = ContainerStateManager()
    state.update(ContainerInfo("radarr", "running", "healthy", "img", None))

    # Mock Docker client and container
    docker_client = MagicMock()
    docker_container = MagicMock()
    docker_container.logs.return_value = b"Log line 1\nLog line 2\nLog line 3\n"
    docker_client.containers.get.return_value = docker_container

    handler = logs_command(state, docker_client)

    message = MagicMock()
    message.text = "/logs radarr"
    message.answer = AsyncMock()

    await handler(message)

    # Verify Docker was called correctly
    docker_client.containers.get.assert_called_once_with("radarr")
    docker_container.logs.assert_called_once_with(tail=20, timestamps=False)

    # Verify response
    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]
    assert "radarr" in response
    assert "Log line 1" in response
    assert "Log line 2" in response
    assert "Log line 3" in response


@pytest.mark.asyncio
async def test_logs_command_with_line_count():
    """Test that /logs radarr 50 calls logs(tail=50)."""
    from src.bot.commands import logs_command
    from src.state import ContainerStateManager
    from src.models import ContainerInfo

    state = ContainerStateManager()
    state.update(ContainerInfo("radarr", "running", "healthy", "img", None))

    # Mock Docker client
    docker_client = MagicMock()
    docker_container = MagicMock()
    docker_container.logs.return_value = b"Test logs\n"
    docker_client.containers.get.return_value = docker_container

    handler = logs_command(state, docker_client)

    message = MagicMock()
    message.text = "/logs radarr 50"
    message.answer = AsyncMock()

    await handler(message)

    # Verify tail=50 was used
    docker_container.logs.assert_called_once_with(tail=50, timestamps=False)


@pytest.mark.asyncio
async def test_logs_command_caps_at_100_lines():
    """Test that line count is capped at 100."""
    from src.bot.commands import logs_command
    from src.state import ContainerStateManager
    from src.models import ContainerInfo

    state = ContainerStateManager()
    state.update(ContainerInfo("radarr", "running", "healthy", "img", None))

    # Mock Docker client
    docker_client = MagicMock()
    docker_container = MagicMock()
    docker_container.logs.return_value = b"Test logs\n"
    docker_client.containers.get.return_value = docker_container

    handler = logs_command(state, docker_client)

    message = MagicMock()
    message.text = "/logs radarr 500"
    message.answer = AsyncMock()

    await handler(message)

    # Verify capped at 100
    docker_container.logs.assert_called_once_with(tail=100, timestamps=False)


@pytest.mark.asyncio
async def test_logs_command_container_not_found():
    """Test error message when container doesn't exist."""
    from src.bot.commands import logs_command
    from src.state import ContainerStateManager

    state = ContainerStateManager()

    docker_client = MagicMock()

    handler = logs_command(state, docker_client)

    message = MagicMock()
    message.text = "/logs nonexistent"
    message.answer = AsyncMock()

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]
    assert "not found" in response.lower() or "no container" in response.lower()


@pytest.mark.asyncio
async def test_logs_command_no_arguments():
    """Test usage message when no container specified."""
    from src.bot.commands import logs_command
    from src.state import ContainerStateManager

    state = ContainerStateManager()
    docker_client = MagicMock()

    handler = logs_command(state, docker_client)

    message = MagicMock()
    message.text = "/logs"
    message.answer = AsyncMock()

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]
    assert "usage" in response.lower()


@pytest.mark.asyncio
async def test_logs_command_truncates_long_output():
    """Test that output over 4000 chars is truncated."""
    from src.bot.commands import logs_command
    from src.state import ContainerStateManager
    from src.models import ContainerInfo

    state = ContainerStateManager()
    state.update(ContainerInfo("radarr", "running", "healthy", "img", None))

    # Mock Docker client with long logs
    docker_client = MagicMock()
    docker_container = MagicMock()
    long_log = "A" * 5000
    docker_container.logs.return_value = long_log.encode()
    docker_client.containers.get.return_value = docker_container

    handler = logs_command(state, docker_client)

    message = MagicMock()
    message.text = "/logs radarr"
    message.answer = AsyncMock()

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]
    assert "truncated" in response.lower()


@pytest.mark.asyncio
async def test_logs_command_multiple_matches():
    """Test error message when multiple containers match."""
    from src.bot.commands import logs_command
    from src.state import ContainerStateManager
    from src.models import ContainerInfo

    state = ContainerStateManager()
    state.update(ContainerInfo("radarr", "running", None, "img", None))
    state.update(ContainerInfo("radarr-test", "running", None, "img", None))

    docker_client = MagicMock()

    handler = logs_command(state, docker_client)

    message = MagicMock()
    message.text = "/logs radar"
    message.answer = AsyncMock()

    await handler(message)

    response = message.answer.call_args[0][0]
    assert "radarr" in response
    assert "radarr-test" in response
    assert "multiple" in response.lower() or "matches" in response.lower()
