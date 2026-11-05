"""/pull against a stateful fake Docker that behaves like the daemon.

Names are unique, create() leaves a container behind even when start() then
fails, and remove() really removes. The old tests raised from an abstract
run() call, which modelled a daemon that never exists: in the real one a port
conflict leaves the half-made replacement holding the name, and the old
rollback could not recreate the original over it.
"""

from typing import Any
from unittest.mock import MagicMock

import docker
import pytest

from src.services.container_control import (
    ContainerController,
    unsupported_recreate_settings,
)


class FakeContainer:
    def __init__(self, docker_: "FakeDocker", name: str, attrs: dict[str, Any], image: str) -> None:
        self._docker = docker_
        self.name = name
        self.attrs = attrs
        self.image = MagicMock(tags=[image], id=f"sha256:{image}")
        self.status = "created"
        self.removed = False

    def stop(self, **_: Any) -> None:
        self.status = "exited"

    def start(self) -> None:
        if self._docker.start_error and self.attrs.get("_replacement"):
            raise docker.errors.APIError("port is already allocated")
        self.status = "running"

    def rename(self, new_name: str) -> None:
        if new_name in self._docker.by_name:
            raise docker.errors.APIError(f"Conflict: name {new_name} in use")
        del self._docker.by_name[self.name]
        self.name = new_name
        self._docker.by_name[new_name] = self

    def remove(self, force: bool = False, **_: Any) -> None:
        if self.status == "running" and not force:
            raise docker.errors.APIError("cannot remove a running container")
        del self._docker.by_name[self.name]
        self.removed = True


class FakeDocker:
    def __init__(self) -> None:
        self.by_name: dict[str, FakeContainer] = {}
        self.start_error = False
        self.create_error = False
        self.created: list[tuple[str, dict[str, Any]]] = []
        self.containers = MagicMock()
        self.containers.get.side_effect = self._get
        self.containers.create.side_effect = self._create
        self.images = MagicMock()
        self.networks = MagicMock()

    def add(self, name: str, attrs: dict[str, Any], image: str = "app:latest") -> FakeContainer:
        c = FakeContainer(self, name, attrs, image)
        c.status = "running"
        self.by_name[name] = c
        return c

    def _get(self, name: str) -> FakeContainer:
        if name not in self.by_name:
            raise docker.errors.NotFound(name)
        return self.by_name[name]

    def _create(self, image: str, name: str, **kwargs: Any) -> FakeContainer:
        if self.create_error:
            raise docker.errors.APIError("invalid mount config")
        if name in self.by_name:
            raise docker.errors.APIError(f"Conflict: name {name} in use")
        self.created.append((image, kwargs))
        c = FakeContainer(self, name, {"_replacement": True, "NetworkSettings": {}}, image)
        self.by_name[name] = c
        return c


def _attrs(**host_config: Any) -> dict[str, Any]:
    return {
        "Id": "abcdef0123456789" + "0" * 48,
        "State": {"Running": True},
        "Config": {"Image": "app:latest", "Env": ["TZ=UTC"]},
        "HostConfig": {"NetworkMode": "bridge", **host_config},
        "NetworkSettings": {"Networks": {"bridge": {}}},
        "Mounts": [],
    }


async def test_success_replaces_the_original():
    fake = FakeDocker()
    original = fake.add("radarr", _attrs(Binds=["/mnt/user/appdata/radarr:/config"]))

    result = await ContainerController(fake, []).pull_and_recreate("radarr")  # type: ignore[arg-type]

    assert result.startswith("✅ radarr updated")
    assert original.removed
    assert fake.by_name["radarr"].status == "running"
    image, kwargs = fake.created[0]
    assert image == "app:latest"
    assert kwargs["volumes"] == ["/mnt/user/appdata/radarr:/config"]


async def test_start_failure_removes_only_the_replacement_and_restores_the_original():
    """create() succeeds, start() hits a port conflict: the classic partial state."""
    fake = FakeDocker()
    fake.start_error = True
    original = fake.add("radarr", _attrs())

    result = await ContainerController(fake, []).pull_and_recreate("radarr")  # type: ignore[arg-type]

    assert "restored unchanged" in result
    assert fake.by_name["radarr"] is original
    assert original.status == "running"
    assert not original.removed
    assert len(fake.by_name) == 1  # the failed replacement is gone


async def test_create_failure_restores_the_original():
    fake = FakeDocker()
    fake.create_error = True
    original = fake.add("radarr", _attrs())

    result = await ContainerController(fake, []).pull_and_recreate("radarr")  # type: ignore[arg-type]

    assert "restored unchanged" in result
    assert fake.by_name["radarr"] is original and original.status == "running"


async def test_a_stopped_container_stays_stopped_on_rollback():
    fake = FakeDocker()
    fake.start_error = True
    attrs = _attrs()
    attrs["State"] = {"Running": False}
    original = fake.add("radarr", attrs)
    original.status = "exited"

    await ContainerController(fake, []).pull_and_recreate("radarr")  # type: ignore[arg-type]

    assert original.status == "exited"


async def test_primary_network_static_ip_and_alias_set_at_create():
    """A br0 container with a fixed address came back on a random one."""
    fake = FakeDocker()
    attrs = _attrs(NetworkMode="br0")
    attrs["NetworkSettings"]["Networks"] = {
        "br0": {
            "Aliases": ["pihole", attrs["Id"][:12]],
            "IPAMConfig": {"IPv4Address": "192.168.0.53"},
            "MacAddress": "02:42:c0:a8:00:35",
        }
    }
    fake.add("pihole", attrs)

    await ContainerController(fake, []).pull_and_recreate("pihole")  # type: ignore[arg-type]

    _, kwargs = fake.created[0]
    assert kwargs["network"] == "br0"
    assert "network_mode" not in kwargs
    assert kwargs["networking_config"] == {
        "br0": {"Aliases": ["pihole"], "IPAMConfig": {"IPv4Address": "192.168.0.53"}}
    }


async def test_primary_endpoint_config_is_accepted_by_docker_py():
    """Run the extracted kwargs through docker-py's own create-args builder."""
    from docker.models.containers import _create_container_args

    attrs = _attrs(NetworkMode="br0", ReadonlyRootfs=True, Init=True, PidsLimit=200)
    attrs["NetworkSettings"]["Networks"] = {"br0": {"IPAMConfig": {"IPv4Address": "192.168.0.53"}}}
    run_config = ContainerController(MagicMock(), [])._extract_run_config(attrs)

    args = _create_container_args({"image": "app", "name": "x", "version": "1.44", **run_config})

    assert args["networking_config"] == {
        "EndpointsConfig": {"br0": {"IPAMConfig": {"IPv4Address": "192.168.0.53"}}}
    }
    host = args["host_config"]
    assert host["NetworkMode"] == "br0"
    assert host["ReadonlyRootfs"] is True
    assert host["Init"] is True
    assert host["PidsLimit"] == 200


async def test_anonymous_volume_is_carried_by_name():
    """An image VOLUME the user never mapped: recreating made a fresh empty one."""
    fake = FakeDocker()
    attrs = _attrs(Binds=["/mnt/user/appdata/db:/config"])
    attrs["Mounts"] = [
        {"Type": "bind", "Source": "/mnt/user/appdata/db", "Destination": "/config", "RW": True},
        {"Type": "volume", "Name": "3f9c0e1d" * 8, "Destination": "/var/lib/postgresql/data",
         "RW": True},
    ]
    fake.add("db", attrs)

    await ContainerController(fake, []).pull_and_recreate("db")  # type: ignore[arg-type]

    _, kwargs = fake.created[0]
    assert kwargs["volumes"] == [
        "/mnt/user/appdata/db:/config",
        f"{'3f9c0e1d' * 8}:/var/lib/postgresql/data:rw",
    ]


async def test_structured_mounts_and_readonly_rootfs_are_kept():
    fake = FakeDocker()
    attrs = _attrs(
        ReadonlyRootfs=True,
        VolumesFrom=["data-only"],
        Mounts=[{"Type": "volume", "Source": "", "Target": "/cache", "ReadOnly": False}],
    )
    attrs["Mounts"] = [{"Type": "volume", "Name": "anonvol", "Destination": "/cache", "RW": True}]
    fake.add("app", attrs)

    await ContainerController(fake, []).pull_and_recreate("app")  # type: ignore[arg-type]

    _, kwargs = fake.created[0]
    assert kwargs["read_only"] is True
    assert kwargs["volumes_from"] == ["data-only"]
    assert kwargs["mounts"] == [{"Type": "volume", "Source": "anonvol", "Target": "/cache",
                                 "ReadOnly": False}]
    assert "volumes" not in kwargs  # /cache is covered by the structured mount


async def test_unknown_host_setting_is_refused_before_anything_stops():
    fake = FakeDocker()
    original = fake.add("app", _attrs(Annotations={"io.example/owner": "me"}))

    result = await ContainerController(fake, []).pull_and_recreate("app")  # type: ignore[arg-type]

    assert "Not updating app" in result and "Annotations" in result
    assert original.status == "running"
    fake.images.pull.assert_not_called()
    assert fake.created == []


@pytest.mark.parametrize("field,value", [
    ("CpusetCpus", "2-5"),
    ("MemorySwap", 4_294_967_296),
    ("OomKillDisable", True),
    ("ReadonlyRootfs", True),
    ("PidsLimit", 100),
])
def test_common_unraid_settings_are_carried_not_refused(field, value):
    assert unsupported_recreate_settings(_attrs(**{field: value})) == []


def test_daemon_defaults_are_not_mistaken_for_settings():
    attrs = _attrs(
        ConsoleSize=[0, 0], CgroupnsMode="private", MaskedPaths=["/proc/kcore"],
        ReadonlyPaths=["/proc/bus"], Isolation="", KernelMemory=0,
    )
    assert unsupported_recreate_settings(attrs) == []
