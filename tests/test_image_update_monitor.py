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


def _monitor_with_state(client, state_path, ignored=None):
    alert = MagicMock(); alert.send_update_alert = AsyncMock()
    cfg = ImageUpdatesConfig(enabled=True, poll_interval_hours=24)
    mon = ImageUpdateMonitor(
        docker_client=client, config=cfg, alert_manager=alert,
        ignored_containers=ignored, state_path=str(state_path),
    )
    return mon, alert


async def test_dedup_survives_restart(tmp_path):
    """A fresh instance loading the same state file must not re-announce."""
    state = tmp_path / "announced_updates.json"
    client = MagicMock()
    client.containers.list.return_value = [_container("radarr", "linuxserver/radarr:latest", ["linuxserver/radarr@sha256:old"])]
    client.images.get_registry_data.return_value = MagicMock(id="sha256:new")

    mon1, alert1 = _monitor_with_state(client, state)
    await mon1.check_once()
    alert1.send_update_alert.assert_awaited_once()

    # Simulate restart: new instance, same state file
    mon2, alert2 = _monitor_with_state(client, state)
    await mon2.check_once()
    alert2.send_update_alert.assert_not_awaited()


async def test_corrupted_state_file_degrades_to_empty(tmp_path):
    state = tmp_path / "announced_updates.json"
    state.write_text("{not json!", encoding="utf-8")
    client = MagicMock()
    client.containers.list.return_value = [_container("radarr", "linuxserver/radarr:latest", ["linuxserver/radarr@sha256:old"])]
    client.images.get_registry_data.return_value = MagicMock(id="sha256:new")

    mon, alert = _monitor_with_state(client, state)
    await mon.check_once()
    # Corrupted file treated as empty -> update announced once, file rewritten
    alert.send_update_alert.assert_awaited_once()
    import json
    assert json.loads(state.read_text(encoding="utf-8")) == {"radarr": "sha256:new"}


async def test_prunes_entries_for_removed_containers(tmp_path):
    state = tmp_path / "announced_updates.json"
    state.write_text('{"ghost": "sha256:gone", "radarr": "sha256:new"}', encoding="utf-8")
    client = MagicMock()
    client.containers.list.return_value = [_container("radarr", "linuxserver/radarr:latest", ["linuxserver/radarr@sha256:old"])]
    client.images.get_registry_data.return_value = MagicMock(id="sha256:new")

    mon, alert = _monitor_with_state(client, state)
    await mon.check_once()

    import json
    persisted = json.loads(state.read_text(encoding="utf-8"))
    assert "ghost" not in persisted
    assert persisted == {"radarr": "sha256:new"}
    # radarr's update was already announced (loaded from state) -> no re-alert
    alert.send_update_alert.assert_not_awaited()


async def test_no_prune_when_container_list_fails(tmp_path):
    """A failed Docker poll must not wipe the dedup map."""
    state = tmp_path / "announced_updates.json"
    state.write_text('{"radarr": "sha256:new"}', encoding="utf-8")
    client = MagicMock()
    client.containers.list.side_effect = RuntimeError("daemon down")

    mon, alert = _monitor_with_state(client, state)
    await mon.check_once()

    import json
    assert json.loads(state.read_text(encoding="utf-8")) == {"radarr": "sha256:new"}
