from unittest.mock import AsyncMock, MagicMock

from src.config import ImageUpdatesConfig
from src.monitors.image_update_monitor import ImageUpdateMonitor, extract_local_digests


def _container(name, tag, repo_digests):
    c = MagicMock()
    c.name = name
    c.image.tags = [tag]
    c.image.attrs = {"RepoDigests": repo_digests}
    return c


def _monitor(client, ignored=None):
    alert = MagicMock(); alert.send_update_alert = AsyncMock()
    cfg = ImageUpdatesConfig(enabled=True, poll_interval_hours=24)
    return ImageUpdateMonitor(docker_client=client, config=cfg, alert_manager=alert, ignored_containers=ignored), alert


def test_extract_local_digests():
    c = _container("x", "img:latest", ["repo@sha256:aaa", "repo@sha256:bbb"])
    assert extract_local_digests(c) == ["sha256:aaa", "sha256:bbb"]


async def test_alerts_when_remote_differs():
    client = MagicMock()
    client.containers.list.return_value = [_container("radarr", "linuxserver/radarr:latest", ["linuxserver/radarr@sha256:old"])]
    client.images.get_registry_data.return_value = MagicMock(id="sha256:new")
    mon, alert = _monitor(client)
    await mon.check_once()
    alert.send_update_alert.assert_awaited_once_with([("radarr", "linuxserver/radarr:latest")])


async def test_no_alert_when_up_to_date():
    client = MagicMock()
    client.containers.list.return_value = [_container("radarr", "linuxserver/radarr:latest", ["linuxserver/radarr@sha256:same"])]
    client.images.get_registry_data.return_value = MagicMock(id="sha256:same")
    mon, alert = _monitor(client)
    await mon.check_once()
    alert.send_update_alert.assert_not_awaited()


async def test_dedup_same_remote_digest():
    client = MagicMock()
    client.containers.list.return_value = [_container("radarr", "linuxserver/radarr:latest", ["linuxserver/radarr@sha256:old"])]
    client.images.get_registry_data.return_value = MagicMock(id="sha256:new")
    mon, alert = _monitor(client)
    await mon.check_once()
    await mon.check_once()
    assert alert.send_update_alert.await_count == 1


async def test_skips_ignored():
    client = MagicMock()
    client.containers.list.return_value = [_container("radarr", "linuxserver/radarr:latest", ["linuxserver/radarr@sha256:old"])]
    client.images.get_registry_data.return_value = MagicMock(id="sha256:new")
    mon, alert = _monitor(client, ignored=["radarr"])
    await mon.check_once()
    alert.send_update_alert.assert_not_awaited()


async def test_skips_when_no_repo_digests():
    client = MagicMock()
    client.containers.list.return_value = [_container("built", "local/built:latest", [])]
    mon, alert = _monitor(client)
    await mon.check_once()
    alert.send_update_alert.assert_not_awaited()


async def test_registry_error_does_not_crash():
    client = MagicMock()
    client.containers.list.return_value = [_container("radarr", "linuxserver/radarr:latest", ["linuxserver/radarr@sha256:old"])]
    client.images.get_registry_data.side_effect = RuntimeError("registry down")
    mon, alert = _monitor(client)
    await mon.check_once()
    alert.send_update_alert.assert_not_awaited()
