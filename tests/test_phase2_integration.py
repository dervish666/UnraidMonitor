"""
Phase 2 integration tests - verify alert flow works end-to-end.
"""
import pytest
from unittest.mock import MagicMock, AsyncMock


@pytest.mark.asyncio
async def test_crash_alert_flow():
    """Test: Container crash triggers Telegram alert."""
    from src.state import ContainerStateManager
    from src.models import ContainerInfo
    from src.monitors.docker_events import DockerEventMonitor
    from src.alerts.manager import AlertManager
    from src.alerts.rate_limiter import RateLimiter

    # Setup
    state = ContainerStateManager()
    state.update(ContainerInfo("radarr", "running", None, "linuxserver/radarr", None))

    bot = MagicMock()
    bot.send_message = AsyncMock()

    alert_manager = AlertManager(bot=bot, chat_id=12345)
    rate_limiter = RateLimiter(cooldown_seconds=900)

    monitor = DockerEventMonitor(
        state_manager=state,
        ignored_containers=[],
        alert_manager=alert_manager,
        rate_limiter=rate_limiter,
    )

    # Simulate crash
    event = {
        "Action": "die",
        "Actor": {"Attributes": {"name": "radarr", "exitCode": "137"}}
    }

    await monitor._handle_crash_event(event)

    # Verify
    bot.send_message.assert_called_once()
    msg = bot.send_message.call_args[1]["text"]
    assert "CRASHED" in msg
    assert "radarr" in msg
    assert "137" in msg


@pytest.mark.asyncio
async def test_log_error_with_rate_limiting():
    """Test: Log errors are rate limited."""
    from src.alerts.manager import AlertManager
    from src.alerts.rate_limiter import RateLimiter

    bot = MagicMock()
    bot.send_message = AsyncMock()

    alert_manager = AlertManager(bot=bot, chat_id=12345)
    rate_limiter = RateLimiter(cooldown_seconds=900)

    # First error - should alert
    if rate_limiter.should_alert("radarr"):
        await alert_manager.send_log_error_alert("radarr", "Error 1", 0)
        rate_limiter.record_alert("radarr")

    assert bot.send_message.call_count == 1

    # Second error - should be suppressed
    if rate_limiter.should_alert("radarr"):
        await alert_manager.send_log_error_alert("radarr", "Error 2", 0)
        rate_limiter.record_alert("radarr")
    else:
        rate_limiter.record_suppressed("radarr")

    assert bot.send_message.call_count == 1  # Still 1
    assert rate_limiter.get_suppressed_count("radarr") == 1


@pytest.mark.asyncio
async def test_ignored_container_no_alert():
    """Test: Ignored containers don't trigger alerts."""
    from src.state import ContainerStateManager
    from src.monitors.docker_events import DockerEventMonitor
    from src.alerts.manager import AlertManager
    from src.alerts.rate_limiter import RateLimiter

    state = ContainerStateManager()

    bot = MagicMock()
    bot.send_message = AsyncMock()

    alert_manager = AlertManager(bot=bot, chat_id=12345)
    rate_limiter = RateLimiter(cooldown_seconds=900)

    monitor = DockerEventMonitor(
        state_manager=state,
        ignored_containers=["Kometa"],
        alert_manager=alert_manager,
        rate_limiter=rate_limiter,
    )

    # Simulate Kometa crash
    event = {
        "Action": "die",
        "Actor": {"Attributes": {"name": "Kometa", "exitCode": "1"}}
    }

    await monitor._handle_crash_event(event)

    # Verify no alert sent
    bot.send_message.assert_not_called()
