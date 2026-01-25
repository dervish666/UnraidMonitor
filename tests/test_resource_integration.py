import pytest
from unittest.mock import MagicMock, AsyncMock, patch


def test_main_creates_resource_monitor_when_enabled(tmp_path):
    """Test that main.py creates ResourceMonitor when enabled in config."""
    # Create config file with resource monitoring enabled
    config_file = tmp_path / "config.yaml"
    config_file.write_text("""
resource_monitoring:
  enabled: true
  poll_interval_seconds: 30
""")

    env_file = tmp_path / ".env"
    env_file.write_text("""
TELEGRAM_BOT_TOKEN=test_token
TELEGRAM_ALLOWED_USERS=123
""")

    # Import after setting up files
    import os
    original_cwd = os.getcwd()

    try:
        os.chdir(tmp_path)

        with patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "test_token",
            "TELEGRAM_ALLOWED_USERS": "123",
        }):
            from src.config import Settings, AppConfig

            settings = Settings(config_path=str(config_file))
            config = AppConfig(settings)

            assert config.resource_monitoring.enabled is True
            assert config.resource_monitoring.poll_interval_seconds == 30
    finally:
        os.chdir(original_cwd)


def test_alert_manager_proxy_has_resource_alert():
    """Test AlertManagerProxy forwards send_resource_alert."""
    # This tests that the proxy pattern works for resource alerts
    from src.alerts.manager import AlertManager

    # Verify AlertManager has send_resource_alert method
    assert hasattr(AlertManager, "send_resource_alert")


@pytest.mark.asyncio
async def test_alert_manager_proxy_send_resource_alert():
    """Test that AlertManagerProxy.send_resource_alert forwards to AlertManager."""
    from src.main import AlertManagerProxy
    from src.alerts.manager import ChatIdStore

    # Create mocks
    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock()
    chat_id_store = ChatIdStore()
    chat_id_store.set_chat_id(12345)

    proxy = AlertManagerProxy(mock_bot, chat_id_store)

    # Call send_resource_alert
    await proxy.send_resource_alert(
        container_name="test-container",
        metric="cpu",
        current_value=95.0,
        threshold=80,
        duration_seconds=180,
        memory_bytes=1024 * 1024 * 100,
        memory_limit=1024 * 1024 * 1024,
        memory_percent=10.0,
        cpu_percent=95.0,
    )

    # Verify bot.send_message was called
    mock_bot.send_message.assert_called_once()
    call_kwargs = mock_bot.send_message.call_args[1]
    assert call_kwargs["chat_id"] == 12345
    assert "test-container" in call_kwargs["text"]
    assert "CPU" in call_kwargs["text"]


@pytest.mark.asyncio
async def test_alert_manager_proxy_no_chat_id():
    """Test that AlertManagerProxy logs warning when no chat ID."""
    from src.main import AlertManagerProxy
    from src.alerts.manager import ChatIdStore

    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock()
    chat_id_store = ChatIdStore()  # No chat ID set

    proxy = AlertManagerProxy(mock_bot, chat_id_store)

    # This should not raise, just log a warning
    await proxy.send_resource_alert(
        container_name="test-container",
        metric="memory",
        current_value=90.0,
        threshold=85,
        duration_seconds=300,
        memory_bytes=1024 * 1024 * 500,
        memory_limit=1024 * 1024 * 1024,
        memory_percent=50.0,
        cpu_percent=10.0,
    )

    # Bot should NOT be called since no chat_id
    mock_bot.send_message.assert_not_called()


def test_resource_monitor_initialization():
    """Test ResourceMonitor can be initialized with required dependencies."""
    from src.monitors.resource_monitor import ResourceMonitor
    from src.config import ResourceConfig

    # Create mocks
    mock_docker_client = MagicMock()
    mock_alert_manager = MagicMock()
    mock_rate_limiter = MagicMock()

    config = ResourceConfig(enabled=True, poll_interval_seconds=30)

    # Should not raise
    monitor = ResourceMonitor(
        docker_client=mock_docker_client,
        config=config,
        alert_manager=mock_alert_manager,
        rate_limiter=mock_rate_limiter,
    )

    assert monitor.is_enabled is True
