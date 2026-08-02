"""Tests for the run config /pull rebuilds a container from.

Anything missed here is lost silently on update: the container comes back
healthy but subtly different from the one the user was running.
"""

from unittest.mock import MagicMock

from src.services.container_control import ContainerController


def _controller() -> ContainerController:
    return ContainerController(docker_client=MagicMock(), protected_containers=[])


def _attrs(**host_config) -> dict:
    return {"Config": {"Image": "plex:latest"}, "HostConfig": host_config}


def test_nvidia_gpu_access_is_preserved():
    """DeviceRequests is how nvidia GPUs are attached -- losing it kills transcoding."""
    request = {
        "Driver": "nvidia",
        "Count": -1,
        "DeviceIDs": None,
        "Capabilities": [["gpu"]],
        "Options": {},
    }

    run_config = _controller()._extract_run_config(_attrs(DeviceRequests=[request]))

    assert "device_requests" in run_config
    extracted = run_config["device_requests"][0]
    assert extracted["Driver"] == "nvidia"
    assert extracted["Count"] == -1
    assert extracted["Capabilities"] == [["gpu"]]


def test_intel_quicksync_device_passthrough_is_preserved():
    """Devices covers /dev/dri -- the other half of hardware transcoding."""
    device = {
        "PathOnHost": "/dev/dri",
        "PathInContainer": "/dev/dri",
        "CgroupPermissions": "rwm",
    }

    run_config = _controller()._extract_run_config(_attrs(Devices=[device]))

    assert run_config["devices"] == ["/dev/dri:/dev/dri:rwm"]


def test_custom_runtime_is_preserved():
    run_config = _controller()._extract_run_config(_attrs(Runtime="nvidia"))

    assert run_config["runtime"] == "nvidia"


def test_default_runtime_is_not_passed_through():
    run_config = _controller()._extract_run_config(_attrs(Runtime="runc"))

    assert "runtime" not in run_config


def test_supplementary_groups_are_preserved():
    run_config = _controller()._extract_run_config(_attrs(GroupAdd=["video", "render"]))

    assert run_config["group_add"] == ["video", "render"]


def test_absent_keys_are_omitted():
    run_config = _controller()._extract_run_config(_attrs())

    for key in ("device_requests", "runtime", "group_add"):
        assert key not in run_config


def test_gpu_container_round_trips_every_hardware_setting():
    """A realistic nvidia Plex container -- all three keys travel together."""
    attrs = _attrs(
        DeviceRequests=[{"Driver": "nvidia", "Count": -1, "Capabilities": [["gpu"]]}],
        Runtime="nvidia",
        GroupAdd=["video"],
        Devices=[{"PathOnHost": "/dev/dri", "PathInContainer": "/dev/dri"}],
        Binds=["/mnt/user/media:/media"],
    )

    run_config = _controller()._extract_run_config(attrs)

    assert run_config["device_requests"]
    assert run_config["runtime"] == "nvidia"
    assert run_config["group_add"] == ["video"]
    assert run_config["devices"] == ["/dev/dri:/dev/dri:rwm"]
    assert run_config["volumes"] == ["/mnt/user/media:/media"]
