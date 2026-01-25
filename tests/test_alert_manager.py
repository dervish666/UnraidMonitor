import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_alert_manager_sends_crash_alert():
    from src.alerts.manager import AlertManager

    bot = MagicMock()
    bot.send_message = AsyncMock()

    manager = AlertManager(bot=bot, chat_id=12345)

    await manager.send_crash_alert(
        container_name="radarr",
        exit_code=137,
        image="linuxserver/radarr:latest",
        uptime_seconds=9240,  # 2h 34m
    )

    bot.send_message.assert_called_once()
    call_args = bot.send_message.call_args
    assert call_args[1]["chat_id"] == 12345
    assert "CRASHED" in call_args[1]["text"]
    assert "radarr" in call_args[1]["text"]
    assert "137" in call_args[1]["text"]


@pytest.mark.asyncio
async def test_alert_manager_sends_log_error_alert():
    from src.alerts.manager import AlertManager

    bot = MagicMock()
    bot.send_message = AsyncMock()

    manager = AlertManager(bot=bot, chat_id=12345)

    await manager.send_log_error_alert(
        container_name="radarr",
        error_line="Database connection failed: timeout",
        suppressed_count=0,
    )

    bot.send_message.assert_called_once()
    call_args = bot.send_message.call_args
    assert "ERRORS" in call_args[1]["text"]
    assert "radarr" in call_args[1]["text"]
    assert "Database connection failed" in call_args[1]["text"]


@pytest.mark.asyncio
async def test_alert_manager_includes_suppressed_count():
    from src.alerts.manager import AlertManager

    bot = MagicMock()
    bot.send_message = AsyncMock()

    manager = AlertManager(bot=bot, chat_id=12345)

    await manager.send_log_error_alert(
        container_name="radarr",
        error_line="Latest error",
        suppressed_count=5,
    )

    call_args = bot.send_message.call_args
    assert "6 errors" in call_args[1]["text"]  # 5 suppressed + 1 current


@pytest.mark.asyncio
async def test_alert_manager_formats_uptime():
    from src.alerts.manager import format_uptime

    assert format_uptime(3661) == "1h 1m"
    assert format_uptime(120) == "2m"
    assert format_uptime(3600) == "1h 0m"
    assert format_uptime(86400) == "24h 0m"
    assert format_uptime(45) == "0m"


@pytest.mark.asyncio
async def test_chat_id_store_saves_and_retrieves():
    from src.alerts.manager import ChatIdStore

    store = ChatIdStore()

    store.set_chat_id(12345)

    assert store.get_chat_id() == 12345


@pytest.mark.asyncio
async def test_chat_id_store_returns_none_when_not_set():
    from src.alerts.manager import ChatIdStore

    store = ChatIdStore()

    assert store.get_chat_id() is None


@pytest.mark.asyncio
async def test_send_resource_alert_cpu():
    """Test sending CPU resource alert."""
    from src.alerts.manager import AlertManager
    from unittest.mock import AsyncMock

    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock()

    manager = AlertManager(mock_bot, chat_id=123)

    await manager.send_resource_alert(
        container_name="plex",
        metric="cpu",
        current_value=92.5,
        threshold=80,
        duration_seconds=180,
        memory_bytes=4_000_000_000,
        memory_limit=8_000_000_000,
        memory_percent=50.0,
        cpu_percent=92.5,
    )

    mock_bot.send_message.assert_called_once()
    call_args = mock_bot.send_message.call_args
    text = call_args.kwargs["text"]

    assert "HIGH RESOURCE USAGE" in text
    assert "plex" in text
    assert "CPU: 92.5%" in text
    assert "threshold: 80%" in text
    assert "3 minutes" in text


@pytest.mark.asyncio
async def test_send_resource_alert_memory():
    """Test sending memory resource alert."""
    from src.alerts.manager import AlertManager
    from unittest.mock import AsyncMock

    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock()

    manager = AlertManager(mock_bot, chat_id=123)

    await manager.send_resource_alert(
        container_name="radarr",
        metric="memory",
        current_value=95.0,
        threshold=85,
        duration_seconds=240,
        memory_bytes=3_800_000_000,
        memory_limit=4_000_000_000,
        memory_percent=95.0,
        cpu_percent=45.0,
    )

    mock_bot.send_message.assert_called_once()
    call_args = mock_bot.send_message.call_args
    text = call_args.kwargs["text"]

    assert "HIGH MEMORY USAGE" in text
    assert "radarr" in text
    assert "Memory: 95.0%" in text
    assert "4 minutes" in text
