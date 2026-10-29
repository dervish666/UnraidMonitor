"""Secondary networks after /pull, driven through real docker-py.

v0.22.5 passed the endpoint from ``docker inspect`` (``Aliases``, ``IPAMConfig``,
``Links``) straight into ``Network.connect()``, which docker-py rejects with
"unexpected keyword argument 'Aliases'". The test that shipped with it asserted
that exact call on a mock, so it passed. These tests run docker-py's own
``Network.connect()`` -> ``connect_container_to_network()`` -> ``EndpointConfig``
and capture only the HTTP body, so a wrong keyword or a bad value shape fails here.
"""
import inspect
from typing import Any
from unittest.mock import MagicMock

import docker
import docker.api.network
import pytest

from src.services.container_control import ContainerController, _endpoint_connect_kwargs
from src.services.docker_client import SharedDockerClient

OLD_ID = "a1b2c3d4e5f6" + "0" * 52


def _real_docker_client() -> tuple[docker.DockerClient, list[dict[str, Any]]]:
    """A real DockerClient that records what it would POST instead of sending it."""
    client = docker.DockerClient(base_url="tcp://127.0.0.1:9", version="1.56")
    sent: list[dict[str, Any]] = []

    def post_json(url: str, data: dict[str, Any]) -> MagicMock:
        sent.append({"url": url, "data": data})
        return MagicMock()

    client.api._post_json = post_json  # type: ignore[method-assign]
    client.api._raise_for_status = MagicMock()  # type: ignore[method-assign]
    return client, sent


def _endpoint_sent(endpoint: dict[str, Any]) -> dict[str, Any]:
    """Run one inspect endpoint through the real connect path; return the EndpointConfig sent."""
    client, sent = _real_docker_client()
    network = client.networks.prepare_model({"Id": "netid", "Name": "proxynet"})
    network.connect("newcontainer", **_endpoint_connect_kwargs(endpoint, OLD_ID[:12]))
    assert sent[0]["url"].endswith("/networks/netid/connect")
    return dict(sent[0]["data"]["EndpointConfig"])


def test_kwargs_names_come_from_the_installed_docker_py():
    params = inspect.signature(
        docker.api.network.NetworkApiMixin.connect_container_to_network
    ).parameters
    for name in ("aliases", "links", "ipv4_address", "ipv6_address", "link_local_ips",
                 "driver_opt"):
        assert name in params


@pytest.mark.parametrize(
    ("endpoint", "expected"),
    [
        pytest.param(
            {"Aliases": ["sonarr", "tv"], "IPAMConfig": None, "Links": None},
            {"Aliases": ["sonarr", "tv"]},
            id="alias",
        ),
        pytest.param(
            {"Aliases": [], "IPAMConfig": {"IPv4Address": "172.31.250.5"}},
            {"IPAMConfig": {"IPv4Address": "172.31.250.5"}},
            id="static-ipv4",
        ),
        pytest.param(
            {"IPAMConfig": {"IPv4Address": "172.31.250.5", "IPv6Address": "fd00::5",
                            "LinkLocalIPs": ["169.254.0.5"]}},
            {"IPAMConfig": {"IPv4Address": "172.31.250.5", "IPv6Address": "fd00::5",
                            "LinkLocalIPs": ["169.254.0.5"]}},
            id="static-ipv4-ipv6-linklocal",
        ),
        pytest.param(
            {"Links": ["db:database", "cache"]},
            {"Links": ["cache", "db:database"]},  # docker-py sorts them
            id="links",
        ),
        pytest.param(
            {"DriverOpts": {"com.example.opt": "1"}},
            {"DriverOpts": {"com.example.opt": "1"}},
            id="driver-opts",
        ),
        pytest.param(
            {"Aliases": [], "IPAMConfig": {}, "Links": None, "DriverOpts": {}},
            {},
            id="none",
        ),
        pytest.param(
            # Engines before API 1.45 add the container's short ID to Aliases.
            {"Aliases": ["web", OLD_ID[:12]]},
            {"Aliases": ["web"]},
            id="own-short-id-dropped",
        ),
        pytest.param(
            {"Aliases": ["web"], "MacAddress": "02:42:ac:11:00:05"},
            {"Aliases": ["web"]},
            id="assigned-mac-not-pinned",
        ),
    ],
)
def test_inspect_endpoint_round_trips_through_docker_py(endpoint, expected):
    assert _endpoint_sent(endpoint) == expected


def test_old_inspect_style_kwargs_are_rejected_by_docker_py():
    """The v0.22.5 call shape, for the record: docker-py refuses it."""
    client, _ = _real_docker_client()
    network = client.networks.prepare_model({"Id": "netid", "Name": "proxynet"})
    with pytest.raises(TypeError, match="Aliases"):
        network.connect("newcontainer", Aliases=["sonarr"])


def _old_container(networks: dict[str, Any]) -> MagicMock:
    container = MagicMock()
    container.image.tags = ["lscr.io/linuxserver/sonarr:latest"]
    container.image.id = "sha256:old"
    container.attrs = {
        "Id": OLD_ID,
        "Config": {"Image": "lscr.io/linuxserver/sonarr:latest"},
        "HostConfig": {"NetworkMode": "bridge"},
        "NetworkSettings": {"Networks": networks},
    }
    return container


async def test_pull_reconnects_each_secondary_network_with_its_settings():
    real, sent = _real_docker_client()
    old = _old_container({
        "bridge": {},
        "proxynet": {"Aliases": ["sonarr", OLD_ID[:12]]},
        "ipnet": {"Aliases": [], "IPAMConfig": {"IPv4Address": "172.31.250.5"}},
        "plainnet": {"Aliases": [], "IPAMConfig": {}},
    })
    new = real.containers.prepare_model(
        {"Id": "new" + "0" * 61, "NetworkSettings": {"Networks": {"bridge": {}}}}
    )

    raw = MagicMock(spec=docker.DockerClient)
    raw.containers.get.side_effect = [old, new]
    raw.networks.get.side_effect = lambda name: real.networks.prepare_model(
        {"Id": f"id-{name}", "Name": name}
    )

    controller = ContainerController(SharedDockerClient(raw), [])  # type: ignore[arg-type]
    result = await controller.pull_and_recreate("sonarr")

    assert result == "✅ sonarr updated (pulled lscr.io/linuxserver/sonarr:latest and recreated)"
    by_net = {s["url"].rsplit("/", 2)[-2]: s["data"] for s in sent}
    assert by_net == {
        "id-proxynet": {"Container": new.id, "EndpointConfig": {"Aliases": ["sonarr"]}},
        "id-ipnet": {"Container": new.id,
                     "EndpointConfig": {"IPAMConfig": {"IPv4Address": "172.31.250.5"}}},
        "id-plainnet": {"Container": new.id, "EndpointConfig": {}},
    }


async def test_pull_skips_a_network_the_new_container_already_joined():
    """NetworkMode "default" is reported as the "bridge" network, so bridge looks
    secondary; connecting it again would fail and report a false problem."""
    real, sent = _real_docker_client()
    old = _old_container({"bridge": {}, "proxynet": {"Aliases": ["sonarr"]}})
    old.attrs["HostConfig"]["NetworkMode"] = "default"
    new = real.containers.prepare_model(
        {"Id": "new" + "0" * 61, "NetworkSettings": {"Networks": {"bridge": {}}}}
    )

    raw = MagicMock(spec=docker.DockerClient)
    raw.containers.get.side_effect = [old, new]
    raw.networks.get.side_effect = lambda name: real.networks.prepare_model(
        {"Id": f"id-{name}", "Name": name}
    )

    controller = ContainerController(SharedDockerClient(raw), [])  # type: ignore[arg-type]
    result = await controller.pull_and_recreate("sonarr")

    assert "Failed to reconnect" not in result
    assert [s["url"].rsplit("/", 2)[-2] for s in sent] == ["id-proxynet"]
