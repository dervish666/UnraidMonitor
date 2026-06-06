import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime


def test_parse_container_from_docker_api():
    from src.monitors.docker_events import parse_container

    # Mock Docker container object
    mock_container = MagicMock()
    mock_container.name = "radarr"
    mock_container.status = "running"
    mock_container.image.tags = ["linuxserver/radarr:latest"]
    mock_container.attrs = {
        "State": {
            "Health": {"Status": "healthy"},
            "StartedAt": "2025-01-25T10:00:00.000000000Z",
        }
    }

    info = parse_container(mock_container)
    assert info.name == "radarr"
    assert info.status == "running"
    assert info.health == "healthy"
    assert info.image == "linuxserver/radarr:latest"


def test_parse_container_without_health_check():
    from src.monitors.docker_events import parse_container

    mock_container = MagicMock()
    mock_container.name = "plex"
    mock_container.status = "running"
    mock_container.image.tags = ["linuxserver/plex:latest"]
    mock_container.attrs = {
        "State": {
            "StartedAt": "2025-01-25T10:00:00.000000000Z",
        }
    }

    info = parse_container(mock_container)
    assert info.health is None


def test_parse_container_no_image_tags():
    from src.monitors.docker_events import parse_container

    mock_container = MagicMock()
    mock_container.name = "test"
    mock_container.status = "running"
    mock_container.image.tags = []
    mock_container.image.id = "sha256:abc123"
    mock_container.attrs = {"State": {}}

    info = parse_container(mock_container)
    assert info.image == "sha256:abc123"


def test_reconnect_calls_containers_list_once():
    """Reconnect should fetch container list once, not twice."""
    from src.monitors.docker_events import DockerEventMonitor
    from src.state import ContainerStateManager

    state = ContainerStateManager()
    monitor = DockerEventMonitor(
        state_manager=state,
        ignored_containers=[],
    )

    mock_client = MagicMock()
    mock_container = MagicMock()
    mock_container.name = "plex"
    mock_container.status = "running"
    mock_container.image.tags = ["linuxserver/plex:latest"]
    mock_container.attrs = {"State": {"Health": {}}}
    mock_client.containers.list.return_value = [mock_container]

    # Patch DockerClient so _reconnect() returns our mock
    with patch("src.monitors.docker_events.docker.DockerClient", return_value=mock_client):
        monitor._reconnect()

    assert mock_client.containers.list.call_count == 1


def test_load_initial_state_uses_prefetched_containers():
    """load_initial_state should skip API call when containers are provided."""
    from src.monitors.docker_events import DockerEventMonitor
    from src.state import ContainerStateManager

    state = ContainerStateManager()
    monitor = DockerEventMonitor(
        state_manager=state,
        ignored_containers=[],
    )

    mock_client = MagicMock()
    monitor._client = mock_client

    mock_container = MagicMock()
    mock_container.name = "sonarr"
    mock_container.status = "running"
    mock_container.image.tags = ["linuxserver/sonarr:latest"]
    mock_container.attrs = {"State": {}}

    monitor.load_initial_state(containers=[mock_container])

    # Should NOT call containers.list when pre-fetched list is passed
    mock_client.containers.list.assert_not_called()
    # Container should be loaded into state
    assert state.get("sonarr") is not None
    assert state.get("sonarr").status == "running"


def test_backoff_doubles_on_reconnect_failure():
    """Backoff should double after each failed reconnect."""
    from src.monitors.docker_events import DockerEventMonitor
    from src.state import ContainerStateManager

    state = ContainerStateManager()
    monitor = DockerEventMonitor(
        state_manager=state,
        ignored_containers=[],
    )

    assert monitor._backoff_seconds == DockerEventMonitor.INITIAL_BACKOFF_SECONDS

    # Simulate failed reconnect cycles by manually doubling (as the start() loop would)
    monitor._backoff_seconds = min(
        monitor._backoff_seconds * 2,
        DockerEventMonitor.MAX_BACKOFF_SECONDS,
    )
    assert monitor._backoff_seconds == 2

    monitor._backoff_seconds = min(
        monitor._backoff_seconds * 2,
        DockerEventMonitor.MAX_BACKOFF_SECONDS,
    )
    assert monitor._backoff_seconds == 4

    # Keep doubling until we hit cap
    for _ in range(10):
        monitor._backoff_seconds = min(
            monitor._backoff_seconds * 2,
            DockerEventMonitor.MAX_BACKOFF_SECONDS,
        )
    assert monitor._backoff_seconds == DockerEventMonitor.MAX_BACKOFF_SECONDS


def test_backoff_resets_on_successful_reconnect():
    """Backoff should reset to initial value after successful reconnect."""
    from src.monitors.docker_events import DockerEventMonitor
    from src.state import ContainerStateManager

    state = ContainerStateManager()
    monitor = DockerEventMonitor(
        state_manager=state,
        ignored_containers=[],
    )

    # Simulate high backoff
    monitor._backoff_seconds = 32

    # Simulate successful reconnect (as start() does)
    mock_client = MagicMock()
    mock_container = MagicMock()
    mock_container.name = "plex"
    mock_container.status = "running"
    mock_container.image.tags = ["linuxserver/plex:latest"]
    mock_container.attrs = {"State": {}}
    mock_client.containers.list.return_value = [mock_container]

    with patch("src.monitors.docker_events.docker.DockerClient", return_value=mock_client):
        monitor._reconnect()
        monitor._backoff_seconds = DockerEventMonitor.INITIAL_BACKOFF_SECONDS

    assert monitor._backoff_seconds == DockerEventMonitor.INITIAL_BACKOFF_SECONDS


def test_load_initial_state_fetches_when_no_containers_provided():
    """load_initial_state should call API when no containers are provided."""
    from src.monitors.docker_events import DockerEventMonitor
    from src.state import ContainerStateManager

    state = ContainerStateManager()
    monitor = DockerEventMonitor(
        state_manager=state,
        ignored_containers=[],
    )

    mock_client = MagicMock()
    mock_container = MagicMock()
    mock_container.name = "radarr"
    mock_container.status = "running"
    mock_container.image.tags = ["linuxserver/radarr:latest"]
    mock_container.attrs = {"State": {}}
    mock_client.containers.list.return_value = [mock_container]
    monitor._client = mock_client

    monitor.load_initial_state()

    mock_client.containers.list.assert_called_once_with(all=True)
    assert state.get("radarr") is not None


# --- CrashTracker recovery tests ---


def test_crash_tracker_should_send_recovery_after_crash():
    """Recovery alert should be sent when container had recent crashes."""
    from src.monitors.docker_events import CrashTracker

    tracker = CrashTracker()
    tracker.record_crash("app")

    assert tracker.should_send_recovery("app") is True


def test_crash_tracker_no_recovery_without_crash():
    """No recovery alert for containers that haven't crashed."""
    from src.monitors.docker_events import CrashTracker

    tracker = CrashTracker()

    assert tracker.should_send_recovery("app") is False


def test_crash_tracker_recovery_cooldown():
    """Recovery alert should respect cooldown."""
    from src.monitors.docker_events import CrashTracker

    tracker = CrashTracker()
    tracker.record_crash("app")
    tracker.record_recovery_alert("app")

    # After recording recovery, crash history is cleared
    assert tracker.should_send_recovery("app") is False

    # Even if we crash again, recovery cooldown applies
    tracker.record_crash("app")
    assert tracker.should_send_recovery("app") is False


def test_crash_tracker_recovery_clears_crash_history():
    """Recording recovery should clear crash history."""
    from src.monitors.docker_events import CrashTracker

    tracker = CrashTracker()
    tracker.record_crash("app")
    tracker.record_crash("app")
    assert tracker.get_crash_count("app") == 2

    tracker.record_recovery_alert("app")
    assert tracker.get_crash_count("app") == 0


@pytest.mark.asyncio
async def test_handle_recovery_event():
    """Test that recovery event triggers send_recovery_alert."""
    from unittest.mock import AsyncMock
    from src.monitors.docker_events import DockerEventMonitor
    from src.state import ContainerStateManager

    state = ContainerStateManager()
    mock_alert_manager = MagicMock()
    mock_alert_manager.send_recovery_alert = AsyncMock()

    monitor = DockerEventMonitor(
        state_manager=state,
        alert_manager=mock_alert_manager,
    )

    # Record a crash first so recovery is meaningful
    monitor._crash_tracker.record_crash("app")

    event = {
        "Action": "start",
        "Actor": {"Attributes": {"name": "app"}},
        "_alert_type": "recovery",
    }

    await monitor._handle_recovery_event(event)

    mock_alert_manager.send_recovery_alert.assert_called_once_with("app")


@pytest.mark.asyncio
async def test_handle_recovery_event_no_prior_crash():
    """Test that recovery event is ignored if no prior crash."""
    from unittest.mock import AsyncMock
    from src.monitors.docker_events import DockerEventMonitor
    from src.state import ContainerStateManager

    state = ContainerStateManager()
    mock_alert_manager = MagicMock()
    mock_alert_manager.send_recovery_alert = AsyncMock()

    monitor = DockerEventMonitor(
        state_manager=state,
        alert_manager=mock_alert_manager,
    )

    event = {
        "Action": "start",
        "Actor": {"Attributes": {"name": "app"}},
        "_alert_type": "recovery",
    }

    await monitor._handle_recovery_event(event)

    mock_alert_manager.send_recovery_alert.assert_not_called()


@pytest.mark.asyncio
async def test_handle_recovery_event_ignored_container():
    """Test that recovery events are ignored for ignored containers."""
    from unittest.mock import AsyncMock
    from src.monitors.docker_events import DockerEventMonitor
    from src.state import ContainerStateManager

    state = ContainerStateManager()
    mock_alert_manager = MagicMock()
    mock_alert_manager.send_recovery_alert = AsyncMock()

    monitor = DockerEventMonitor(
        state_manager=state,
        ignored_containers=["app"],
        alert_manager=mock_alert_manager,
    )

    monitor._crash_tracker.record_crash("app")

    event = {
        "Action": "start",
        "Actor": {"Attributes": {"name": "app"}},
        "_alert_type": "recovery",
    }

    await monitor._handle_recovery_event(event)

    mock_alert_manager.send_recovery_alert.assert_not_called()


async def test_unhealthy_opted_in_calls_healer_and_skips_alert():
    from src.monitors.docker_events import DockerEventMonitor
    from src.state import ContainerStateManager
    alert = MagicMock(); alert.send_health_alert = AsyncMock()
    mon = DockerEventMonitor(state_manager=ContainerStateManager(), alert_manager=alert)
    healer = MagicMock(); healer.is_enabled.return_value = True; healer.heal = AsyncMock()
    mon.set_auto_healer(healer)
    await mon._handle_health_event({"Actor": {"Attributes": {"name": "radarr"}}, "_alert_type": "health"})
    healer.heal.assert_awaited_once_with("radarr")
    alert.send_health_alert.assert_not_awaited()


async def test_unhealthy_not_opted_in_sends_alert():
    from src.monitors.docker_events import DockerEventMonitor
    from src.state import ContainerStateManager
    alert = MagicMock(); alert.send_health_alert = AsyncMock()
    mon = DockerEventMonitor(state_manager=ContainerStateManager(), alert_manager=alert)
    healer = MagicMock(); healer.is_enabled.return_value = False; healer.heal = AsyncMock()
    mon.set_auto_healer(healer)
    await mon._handle_health_event({"Actor": {"Attributes": {"name": "plex"}}, "_alert_type": "health"})
    healer.heal.assert_not_awaited()
    alert.send_health_alert.assert_awaited_once()


async def test_unhealthy_reenters_heal_when_still_unhealthy():
    """A container that stays unhealthy after a heal attempt is healed again — the
    storm guard bounds the retry loop, not the _unhealthy_alerted dedup set."""
    from src.monitors.docker_events import DockerEventMonitor
    from src.state import ContainerStateManager
    alert = MagicMock(); alert.send_health_alert = AsyncMock()
    mon = DockerEventMonitor(state_manager=ContainerStateManager(), alert_manager=alert)
    healer = MagicMock(); healer.is_enabled.return_value = True
    healer.heal = AsyncMock(return_value="restarted")
    mon.set_auto_healer(healer)
    event = {"Actor": {"Attributes": {"name": "radarr"}}, "_alert_type": "health"}
    await mon._handle_health_event(event)
    await mon._handle_health_event(event)  # no healthy event in between
    assert healer.heal.await_count == 2


async def test_unhealthy_reenters_heal_after_failed_restart():
    from src.monitors.docker_events import DockerEventMonitor
    from src.state import ContainerStateManager
    alert = MagicMock(); alert.send_health_alert = AsyncMock()
    mon = DockerEventMonitor(state_manager=ContainerStateManager(), alert_manager=alert)
    healer = MagicMock(); healer.is_enabled.return_value = True
    healer.heal = AsyncMock(return_value="failed")
    mon.set_auto_healer(healer)
    event = {"Actor": {"Attributes": {"name": "radarr"}}, "_alert_type": "health"}
    await mon._handle_health_event(event)
    await mon._handle_health_event(event)
    assert healer.heal.await_count == 2


async def test_unhealthy_no_reentry_after_gave_up():
    """Once the healer gives up, further unhealthy events stay quiet until a
    healthy transition clears the dedup flag."""
    from src.monitors.docker_events import DockerEventMonitor
    from src.state import ContainerStateManager
    alert = MagicMock(); alert.send_health_alert = AsyncMock()
    mon = DockerEventMonitor(state_manager=ContainerStateManager(), alert_manager=alert)
    healer = MagicMock(); healer.is_enabled.return_value = True
    healer.heal = AsyncMock(return_value="gave_up")
    mon.set_auto_healer(healer)
    event = {"Actor": {"Attributes": {"name": "radarr"}}, "_alert_type": "health"}
    await mon._handle_health_event(event)
    await mon._handle_health_event(event)
    assert healer.heal.await_count == 1
    alert.send_health_alert.assert_not_awaited()


async def test_unhealthy_storm_guard_end_to_end():
    """Real AutoHealer wired to the monitor: a container that never goes healthy
    is restarted max_restarts times, then exactly one gave-up escalation fires
    and subsequent unhealthy events stay quiet."""
    from src.config import AutoHealConfig
    from src.monitors.docker_events import DockerEventMonitor
    from src.services.auto_healer import AutoHealer
    from src.state import ContainerStateManager
    alert = MagicMock()
    alert.send_health_alert = AsyncMock()
    alert.send_autoheal_alert = AsyncMock()
    controller = MagicMock()
    controller.is_protected.return_value = False
    controller.restart = AsyncMock(return_value="✅ radarr restarted successfully")
    config = AutoHealConfig(enabled=True, containers=["radarr"], max_restarts=3, window_minutes=60)
    mon = DockerEventMonitor(state_manager=ContainerStateManager(), alert_manager=alert)
    mon.set_auto_healer(AutoHealer(config=config, controller=controller, alert_manager=alert))
    event = {"Actor": {"Attributes": {"name": "radarr"}}, "_alert_type": "health"}
    for _ in range(6):  # container never transitions to healthy
        await mon._handle_health_event(event)
    assert controller.restart.await_count == 3
    gave_up_calls = [c for c in alert.send_autoheal_alert.call_args_list if c.kwargs["gave_up"]]
    assert len(gave_up_calls) == 1
    alert.send_health_alert.assert_not_awaited()
