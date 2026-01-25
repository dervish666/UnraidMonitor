import pytest
from unittest.mock import MagicMock, patch


def test_container_controller_is_protected():
    """Test that protected containers are identified."""
    from src.services.container_control import ContainerController

    mock_client = MagicMock()
    controller = ContainerController(
        docker_client=mock_client,
        protected_containers=["mariadb", "unraid-monitor-bot"],
    )

    assert controller.is_protected("mariadb") is True
    assert controller.is_protected("unraid-monitor-bot") is True
    assert controller.is_protected("radarr") is False


@pytest.mark.asyncio
async def test_container_controller_restart():
    """Test restart stops and starts container."""
    from src.services.container_control import ContainerController

    mock_container = MagicMock()
    mock_client = MagicMock()
    mock_client.containers.get.return_value = mock_container

    controller = ContainerController(docker_client=mock_client, protected_containers=[])

    result = await controller.restart("radarr")

    mock_container.restart.assert_called_once()
    assert "restarted" in result.lower()


@pytest.mark.asyncio
async def test_container_controller_stop():
    """Test stop container."""
    from src.services.container_control import ContainerController

    mock_container = MagicMock()
    mock_container.status = "running"
    mock_client = MagicMock()
    mock_client.containers.get.return_value = mock_container

    controller = ContainerController(docker_client=mock_client, protected_containers=[])

    result = await controller.stop("radarr")

    mock_container.stop.assert_called_once()
    assert "stopped" in result.lower()


@pytest.mark.asyncio
async def test_container_controller_stop_already_stopped():
    """Test stop on already stopped container."""
    from src.services.container_control import ContainerController

    mock_container = MagicMock()
    mock_container.status = "exited"
    mock_client = MagicMock()
    mock_client.containers.get.return_value = mock_container

    controller = ContainerController(docker_client=mock_client, protected_containers=[])

    result = await controller.stop("radarr")

    mock_container.stop.assert_not_called()
    assert "already stopped" in result.lower()


@pytest.mark.asyncio
async def test_container_controller_start():
    """Test start container."""
    from src.services.container_control import ContainerController

    mock_container = MagicMock()
    mock_container.status = "exited"
    mock_client = MagicMock()
    mock_client.containers.get.return_value = mock_container

    controller = ContainerController(docker_client=mock_client, protected_containers=[])

    result = await controller.start("radarr")

    mock_container.start.assert_called_once()
    assert "started" in result.lower()


@pytest.mark.asyncio
async def test_container_controller_start_already_running():
    """Test start on already running container."""
    from src.services.container_control import ContainerController

    mock_container = MagicMock()
    mock_container.status = "running"
    mock_client = MagicMock()
    mock_client.containers.get.return_value = mock_container

    controller = ContainerController(docker_client=mock_client, protected_containers=[])

    result = await controller.start("radarr")

    mock_container.start.assert_not_called()
    assert "already running" in result.lower()


@pytest.mark.asyncio
async def test_container_controller_not_found():
    """Test handling of container not found."""
    import docker
    from src.services.container_control import ContainerController

    mock_client = MagicMock()
    mock_client.containers.get.side_effect = docker.errors.NotFound("not found")

    controller = ContainerController(docker_client=mock_client, protected_containers=[])

    result = await controller.restart("nonexistent")

    assert "not found" in result.lower()
