import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime, timezone


@pytest.mark.asyncio
async def test_docker_monitor_triggers_crash_alert():
    from src.monitors.docker_events import DockerEventMonitor
    from src.state import ContainerStateManager
    from src.alerts.manager import AlertManager, ChatIdStore
    from src.alerts.rate_limiter import RateLimiter

    state = ContainerStateManager()
    chat_store = ChatIdStore()
    chat_store.set_chat_id(12345)

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

    # Simulate a crash event
    event = {
        "Action": "die",
        "Actor": {
            "Attributes": {
                "name": "radarr",
                "exitCode": "137",
            }
        },
    }

    await monitor._handle_crash_event(event)

    bot.send_message.assert_called_once()
    assert "CRASHED" in bot.send_message.call_args[1]["text"]


@pytest.mark.asyncio
async def test_docker_monitor_ignores_normal_stop():
    from src.monitors.docker_events import DockerEventMonitor
    from src.state import ContainerStateManager
    from src.alerts.manager import AlertManager, ChatIdStore
    from src.alerts.rate_limiter import RateLimiter

    state = ContainerStateManager()
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

    # Simulate a normal stop (exit code 0)
    event = {
        "Action": "die",
        "Actor": {
            "Attributes": {
                "name": "radarr",
                "exitCode": "0",
            }
        },
    }

    await monitor._handle_crash_event(event)

    bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_docker_monitor_respects_ignored_containers():
    from src.monitors.docker_events import DockerEventMonitor
    from src.state import ContainerStateManager
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

    event = {
        "Action": "die",
        "Actor": {
            "Attributes": {
                "name": "Kometa",
                "exitCode": "1",
            }
        },
    }

    await monitor._handle_crash_event(event)

    bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_log_error_alert_does_not_silence_crash_alert():
    """Log errors and crashes share one RateLimiter; a log alert for a container
    must not suppress its crash alert (audit 2026-09-22 L3)."""
    from src.monitors.docker_events import DockerEventMonitor
    from src.monitor_callbacks import make_log_error_handler
    from src.state import ContainerStateManager
    from src.alerts.manager import AlertManager
    from src.alerts.rate_limiter import RateLimiter

    bot = MagicMock()
    bot.send_message = AsyncMock()
    alert_manager = AlertManager(bot=bot, chat_id=12345)
    rate_limiter = RateLimiter(cooldown_seconds=900)
    mute_manager = MagicMock()
    mute_manager.is_muted.return_value = False

    on_log_error = make_log_error_handler(mute_manager, rate_limiter, alert_manager)
    monitor = DockerEventMonitor(
        state_manager=ContainerStateManager(),
        ignored_containers=[],
        alert_manager=alert_manager,
        rate_limiter=rate_limiter,
    )

    await on_log_error("plex", "ERROR: database locked")
    await monitor._handle_crash_event(
        {"Action": "die", "Actor": {"Attributes": {"name": "plex", "exitCode": "137"}}}
    )

    texts = [c[1]["text"] for c in bot.send_message.call_args_list]
    assert len(texts) == 2
    assert "CRASHED" in texts[1]

    # A second crash inside the cooldown is still rate limited.
    await monitor._handle_crash_event(
        {"Action": "die", "Actor": {"Attributes": {"name": "plex", "exitCode": "137"}}}
    )
    assert bot.send_message.call_count == 2
