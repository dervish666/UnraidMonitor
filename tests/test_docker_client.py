"""Tests for SharedDockerClient wrapper."""

import logging
import pathlib
import re
from unittest.mock import MagicMock, patch

import docker


class TestSharedDockerClient:
    def test_get_client_returns_current(self):
        from src.services.docker_client import SharedDockerClient

        mock_client = MagicMock()
        shared = SharedDockerClient(mock_client)
        assert shared.client is mock_client

    def test_replace_client_updates_reference(self):
        from src.services.docker_client import SharedDockerClient

        old = MagicMock()
        new = MagicMock()
        shared = SharedDockerClient(old)
        shared.replace(new)
        assert shared.client is new

    def test_containers_delegates_to_current_client(self):
        from src.services.docker_client import SharedDockerClient

        mock_client = MagicMock()
        shared = SharedDockerClient(mock_client)
        _ = shared.containers.list()
        mock_client.containers.list.assert_called_once()

    def test_images_delegates_to_current_client(self):
        from src.services.docker_client import SharedDockerClient

        mock_client = MagicMock()
        shared = SharedDockerClient(mock_client)
        _ = shared.images.list()
        mock_client.images.list.assert_called_once()

    def test_close_delegates(self):
        from src.services.docker_client import SharedDockerClient

        mock_client = MagicMock()
        shared = SharedDockerClient(mock_client)
        shared.close()
        mock_client.close.assert_called_once()

    def test_replace_updates_containers_delegation(self):
        """After replace, containers delegates to the NEW client."""
        from src.services.docker_client import SharedDockerClient

        old = MagicMock()
        new = MagicMock()
        shared = SharedDockerClient(old)
        shared.replace(new)
        _ = shared.containers.list()
        new.containers.list.assert_called_once()
        old.containers.list.assert_not_called()


def _spec_client() -> MagicMock:
    """A mock with the real DockerClient's surface, so a missing proxy shows up."""
    return MagicMock(spec=docker.DockerClient)


class TestWrapperCoversWhatConsumersUse:
    """Consumers are typed as docker.DockerClient and given the wrapper behind a
    type: ignore, so mypy never saw that info() and networks were missing. These
    tests drive the real wrapper instead of a bare MagicMock, which had any
    attribute you asked for."""

    def test_every_client_attribute_read_in_src_is_proxied(self):
        from src.services.docker_client import SharedDockerClient

        src = pathlib.Path(__file__).parent.parent / "src"
        pattern = re.compile(r"\b(?:docker_client|_docker_client|_docker)\.([a-z_]+)\b")
        used = {
            name
            for path in src.rglob("*.py")
            for name in pattern.findall(path.read_text())
            if hasattr(docker.DockerClient, name)
        }
        assert {"containers", "images", "networks", "info"} <= used
        missing = sorted(name for name in used if not hasattr(SharedDockerClient, name))
        assert missing == []

    def test_host_cpu_cores_reads_ncpu_through_the_wrapper(self, caplog):
        from src.monitors.resource_monitor import host_cpu_cores
        from src.services.docker_client import SharedDockerClient

        raw = _spec_client()
        raw.info.return_value = {"NCPU": 16}
        # The fallback must not be able to produce the right answer by accident.
        with patch("src.monitors.resource_monitor.os.cpu_count", return_value=3), \
                caplog.at_level(logging.WARNING, logger="src.monitors.resource_monitor"):
            assert host_cpu_cores(SharedDockerClient(raw)) == 16
        assert caplog.records == []

    def test_info_follows_a_replaced_client(self):
        from src.services.docker_client import SharedDockerClient

        old, new = _spec_client(), _spec_client()
        new.info.return_value = {"NCPU": 8}
        shared = SharedDockerClient(old)
        shared.replace(new)
        assert shared.info() == {"NCPU": 8}
        old.info.assert_not_called()

    def test_host_cpu_cores_fallback_names_the_value_it_used(self, caplog):
        from src.monitors.resource_monitor import host_cpu_cores
        from src.services.docker_client import SharedDockerClient

        raw = _spec_client()
        raw.info.side_effect = docker.errors.APIError("daemon gone")
        with patch("src.monitors.resource_monitor.os.cpu_count", return_value=3), \
                caplog.at_level(logging.WARNING, logger="src.monitors.resource_monitor"):
            assert host_cpu_cores(SharedDockerClient(raw)) == 3
        assert "using 3 from os.cpu_count()" in caplog.text

    async def test_pull_reconnects_secondary_networks_through_the_wrapper(self):
        from src.services.container_control import ContainerController
        from src.services.docker_client import SharedDockerClient

        container = MagicMock()
        container.image.tags = ["lscr.io/linuxserver/sonarr:latest"]
        container.image.id = "sha256:old"
        container.attrs = {
            "Config": {"Image": "lscr.io/linuxserver/sonarr:latest"},
            "HostConfig": {"NetworkMode": "bridge"},
            "NetworkSettings": {"Networks": {"bridge": {}, "proxynet": {"Aliases": ["sonarr"]}}},
        }
        raw = _spec_client()
        raw.containers.get.return_value = container

        controller = ContainerController(SharedDockerClient(raw), [])  # type: ignore[arg-type]
        result = await controller.pull_and_recreate("sonarr")

        assert result.startswith("✅ sonarr updated"), result
        assert "Failed to reconnect" not in result
        raw.networks.get.assert_called_once_with("proxynet")
        raw.networks.get.return_value.connect.assert_called_once_with(
            container, Aliases=["sonarr"]
        )
