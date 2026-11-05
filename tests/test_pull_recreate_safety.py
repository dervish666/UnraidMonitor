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
    def __init__(
        self, docker_: "FakeDocker", name: str, attrs: dict[str, Any], image: str, cid: str,
    ) -> None:
        self._docker = docker_
        self.name = name
        self.id = cid
        self.attrs = attrs
        self.image = MagicMock(tags=[image], id=f"sha256:{image}")
        self.status = "created"
        self.removed = False

    def stop(self, **_: Any) -> None:
        self.status = "exited"
        if (self.attrs.get("HostConfig") or {}).get("AutoRemove"):
            # --rm: the daemon deletes the container once it stops.
            self._docker.delete(self)

    def start(self) -> None:
        if self._docker.start_error and self.attrs.get("_replacement"):
            raise docker.errors.APIError("port is already allocated")
        self.status = "running"

    def rename(self, new_name: str) -> None:
        if self.removed:
            raise docker.errors.NotFound(self.name)
        if new_name in self._docker.by_name:
            raise docker.errors.APIError(f"Conflict: name {new_name} in use")
        del self._docker.by_name[self.name]
        self.name = new_name
        self._docker.by_name[new_name] = self

    def remove(self, force: bool = False, **_: Any) -> None:
        if self.status == "running" and not force:
            raise docker.errors.APIError("cannot remove a running container")
        self._docker.delete(self)


class FakeApi:
    """The low-level client: create_container returns only an ID."""

    _version = "1.44"

    def __init__(self, docker_: "FakeDocker") -> None:
        self._docker = docker_

    def create_container(self, image: str, name: str, **kwargs: Any) -> dict[str, str]:
        return self._docker.create(image, name, kwargs)

    def remove_container(self, cid: str, force: bool = False) -> None:
        # DELETE /containers/{id} needs no inspect, so it works when inspect does not.
        match = [c for c in self._docker.by_name.values() if c.id == cid]
        if not match:
            raise docker.errors.NotFound(cid)
        match[0].remove(force=force)


class FakeNetwork:
    def __init__(self, docker_: "FakeDocker", name: str) -> None:
        self._docker = docker_
        self.name = name

    def connect(self, container: FakeContainer, **_: Any) -> None:
        if self.name in self._docker.broken_networks:
            raise docker.errors.APIError(f"network {self.name} not found")
        self._docker.connected.append((container.id, self.name))


class FakeDocker:
    def __init__(self) -> None:
        self.by_name: dict[str, FakeContainer] = {}
        self.start_error = False
        self.create_error = False
        self.inspect_error_after_create = False
        self.broken_networks: set[str] = set()
        self.connected: list[tuple[str, str]] = []
        # What the daemon fills into a new container's MaskedPaths.
        self.default_masked_paths: list[str] | None = None
        self.created: list[dict[str, Any]] = []
        self._next_id = 0
        self.containers = MagicMock()
        self.containers.get.side_effect = self.get
        self.containers.client.api = FakeApi(self)
        self.images = MagicMock()
        self.networks = MagicMock()
        self.networks.get.side_effect = lambda name: FakeNetwork(self, name)

    def _new_id(self) -> str:
        self._next_id += 1
        return f"{self._next_id:064x}"

    def add(self, name: str, attrs: dict[str, Any], image: str = "app:latest") -> FakeContainer:
        c = FakeContainer(self, name, attrs, image, attrs.get("Id") or self._new_id())
        c.status = "running"
        self.by_name[name] = c
        return c

    def delete(self, c: FakeContainer) -> None:
        del self.by_name[c.name]
        c.removed = True

    def get(self, name_or_id: str) -> FakeContainer:
        for c in self.by_name.values():
            if name_or_id in (c.name, c.id):
                if self.inspect_error_after_create and c.attrs.get("_replacement"):
                    raise docker.errors.APIError("500 Server Error: inspect timed out")
                return c
        raise docker.errors.NotFound(name_or_id)

    def create(self, image: str, name: str, kwargs: dict[str, Any]) -> dict[str, str]:
        if self.create_error:
            raise docker.errors.APIError("invalid mount config")
        if name in self.by_name:
            raise docker.errors.APIError(f"Conflict: name {name} in use")
        self.created.append({"image": image, **kwargs})
        host_config = dict(kwargs.get("host_config") or {})
        host_config["MaskedPaths"] = self.default_masked_paths
        attrs = {"_replacement": True, "HostConfig": host_config, "NetworkSettings": {}}
        c = FakeContainer(self, name, attrs, image, self._new_id())
        self.by_name[name] = c
        return {"Id": c.id}


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
    kwargs = fake.created[0]
    assert kwargs["image"] == "app:latest"
    assert kwargs["host_config"]["Binds"] == ["/mnt/user/appdata/radarr:/config"]


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

    kwargs = fake.created[0]
    assert kwargs["host_config"]["NetworkMode"] == "br0"
    assert kwargs["networking_config"] == {"EndpointsConfig": {
        "br0": {"Aliases": ["pihole"], "IPAMConfig": {"IPv4Address": "192.168.0.53"}}
    }}


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

    kwargs = fake.created[0]
    assert kwargs["host_config"]["Binds"] == [
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

    host = fake.created[0]["host_config"]
    assert host["ReadonlyRootfs"] is True
    assert host["VolumesFrom"] == ["data-only"]
    assert host["Mounts"] == [{"Type": "volume", "Source": "anonvol", "Target": "/cache",
                               "ReadOnly": False}]
    assert "Binds" not in host  # /cache is covered by the structured mount


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


# -- Boundaries the round-2 review found the fake was missing ---------------


async def test_auto_remove_container_is_refused_before_anything_stops():
    """Stopping a --rm container deletes it, so the rollback would be gone."""
    fake = FakeDocker()
    original = fake.add("app", _attrs(AutoRemove=True))

    result = await ContainerController(fake, []).pull_and_recreate("app")  # type: ignore[arg-type]

    assert "Not updating app" in result and "--rm" in result
    assert original.status == "running" and not original.removed
    fake.images.pull.assert_not_called()


async def test_inspect_failing_after_create_removes_that_replacement_by_id():
    """docker-py's create() raised after creating, losing the ID with the name held."""
    fake = FakeDocker()
    fake.inspect_error_after_create = True
    original = fake.add("radarr", _attrs())

    result = await ContainerController(fake, []).pull_and_recreate("radarr")  # type: ignore[arg-type]

    assert "restored unchanged" in result
    assert fake.by_name == {"radarr": original}
    assert original.status == "running"


async def test_failed_secondary_network_keeps_the_original():
    fake = FakeDocker()
    fake.broken_networks = {"proxynet"}
    attrs = _attrs()
    attrs["NetworkSettings"]["Networks"] = {"bridge": {}, "proxynet": {"Aliases": ["radarr"]}}
    original = fake.add("radarr", attrs)

    result = await ContainerController(fake, []).pull_and_recreate("radarr")  # type: ignore[arg-type]

    assert "restored unchanged" in result and "proxynet" in result
    assert fake.by_name == {"radarr": original}
    assert original.status == "running" and not original.removed


async def test_custom_masked_paths_the_daemon_would_not_reproduce_keep_the_original():
    fake = FakeDocker()
    fake.default_masked_paths = ["/proc/kcore"]
    original = fake.add("app", _attrs(MaskedPaths=["/proc/kcore", "/proc/custom"]))

    result = await ContainerController(fake, []).pull_and_recreate("app")  # type: ignore[arg-type]

    assert "MaskedPaths" in result and "restored unchanged" in result
    assert fake.by_name == {"app": original}


async def test_daemon_default_masked_paths_do_not_block_an_update():
    fake = FakeDocker()
    fake.default_masked_paths = ["/proc/kcore"]
    fake.add("app", _attrs(MaskedPaths=["/proc/kcore"], CgroupnsMode="host"))

    result = await ContainerController(fake, []).pull_and_recreate("app")  # type: ignore[arg-type]

    assert result.startswith("✅ app updated")
    assert fake.created[0]["host_config"]["CgroupnsMode"] == "host"
