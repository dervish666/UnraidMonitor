"""Tests for /health command handler."""

import pytest
from unittest.mock import MagicMock, AsyncMock
from datetime import datetime, timezone, timedelta

from src.bot.health_command import health_command, BOT_VERSION, _format_health_uptime


class TestFormatHealthUptime:
    def test_uptime_minutes_only(self):
        start = datetime.now(timezone.utc) - timedelta(minutes=5)
        result = _format_health_uptime(start)
        assert "5m" in result

    def test_uptime_hours_and_minutes(self):
        start = datetime.now(timezone.utc) - timedelta(hours=2, minutes=30)
        result = _format_health_uptime(start)
        assert "2h" in result
        assert "30m" in result

    def test_uptime_days(self):
        start = datetime.now(timezone.utc) - timedelta(days=3, hours=1)
        result = _format_health_uptime(start)
        assert "3d" in result
        assert "1h" in result

    def test_uptime_zero_shows_0m(self):
        start = datetime.now(timezone.utc)
        result = _format_health_uptime(start)
        assert "0m" in result


class TestHealthCommand:
    @pytest.mark.asyncio
    async def test_health_shows_version_and_uptime(self):
        start_time = datetime.now(timezone.utc) - timedelta(hours=1)
        handler = health_command(start_time)

        message = MagicMock()
        message.answer = AsyncMock()

        await handler(message)

        text = message.answer.call_args[0][0]
        assert BOT_VERSION in text
        assert "Uptime" in text

    @pytest.mark.asyncio
    async def test_health_shows_monitors_not_configured(self):
        """When no monitors are passed, shows 'Not configured'."""
        start_time = datetime.now(timezone.utc)
        handler = health_command(start_time)

        message = MagicMock()
        message.answer = AsyncMock()

        await handler(message)

        text = message.answer.call_args[0][0]
        assert "Not configured" in text
        assert "Disabled" in text

    @pytest.mark.asyncio
    async def test_health_shows_running_monitors(self):
        """When monitors are running, shows Running status."""
        start_time = datetime.now(timezone.utc)

        mock_monitor = MagicMock()
        mock_monitor.is_running = True
        mock_monitor.state_manager.get_all.return_value = [MagicMock(), MagicMock()]
        mock_monitor.crash_tracker.get_active_crash_loops.return_value = []

        mock_log_watcher = MagicMock()
        mock_log_watcher.is_running = True
        mock_log_watcher.containers = ["plex", "radarr"]

        mock_resource = MagicMock()
        mock_resource.is_running = True

        mock_memory = MagicMock()
        mock_memory.is_running = True

        handler = health_command(
            start_time,
            monitor=mock_monitor,
            log_watcher=mock_log_watcher,
            resource_monitor=mock_resource,
            memory_monitor=mock_memory,
        )

        message = MagicMock()
        message.answer = AsyncMock()

        await handler(message)

        text = message.answer.call_args[0][0]
        assert "Running" in text
        assert "2 containers" in text
        assert "2 containers" in text  # log watcher

    @pytest.mark.asyncio
    async def test_health_shows_stopped_monitors(self):
        """When monitors are stopped, shows Stopped status."""
        start_time = datetime.now(timezone.utc)

        mock_monitor = MagicMock()
        mock_monitor.is_running = False
        mock_monitor.state_manager.get_all.return_value = []
        mock_monitor.crash_tracker.get_active_crash_loops.return_value = []

        handler = health_command(start_time, monitor=mock_monitor)

        message = MagicMock()
        message.answer = AsyncMock()

        await handler(message)

        text = message.answer.call_args[0][0]
        assert "Stopped" in text

    @pytest.mark.asyncio
    async def test_health_shows_crash_tracker(self):
        """When crash tracker has active loops, shows them."""
        start_time = datetime.now(timezone.utc)

        mock_monitor = MagicMock()
        mock_monitor.is_running = True
        mock_monitor.state_manager.get_all.return_value = []

        # Simulate a container with crash loop
        mock_monitor.crash_tracker.get_active_crash_loops.return_value = [("plex", 5)]

        handler = health_command(start_time, monitor=mock_monitor)

        message = MagicMock()
        message.answer = AsyncMock()

        await handler(message)

        text = message.answer.call_args[0][0]
        assert "plex" in text
        assert "5x" in text


def test_build_status_lines_includes_new_monitors():
    from src.bot.health_command import build_status_lines
    from src.config import AutoHealConfig
    monitor = MagicMock(); monitor.is_running = True
    monitor.state_manager.get_all.return_value = [1, 2, 3]
    image_mon = MagicMock(); image_mon.is_running = True
    lines = build_status_lines(
        monitor=monitor, log_watcher=None, resource_monitor=None, memory_monitor=None,
        unraid_client=None, unraid_system_monitor=None, unraid_array_monitor=None,
        image_update_monitor=image_mon,
        auto_heal_config=AutoHealConfig(enabled=True, containers=["radarr"], max_restarts=3, window_minutes=60),
    )
    blob = "\n".join(lines)
    assert "Image updates" in blob
    assert "Auto-heal" in blob
    assert "1 container" in blob
    # Both features on -> no discoverability hint
    assert "Features" not in blob


def test_build_status_lines_hint_when_features_off():
    """When image-updates or auto-heal is off, a /manage hint is appended."""
    from src.bot.health_command import build_status_lines
    monitor = MagicMock()
    monitor.is_running = True
    monitor.state_manager.get_all.return_value = []
    lines = build_status_lines(
        monitor=monitor, image_update_monitor=None, auto_heal_config=None,
    )
    blob = "\n".join(lines)
    assert "/manage" in blob
    assert "Features" in blob
