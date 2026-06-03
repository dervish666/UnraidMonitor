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
    assert format_uptime(3600) == "1h"
    assert format_uptime(86400) == "1d"
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


class TestChatIdStore:
    """Tests for multi-user ChatIdStore."""

    def test_stores_multiple_chat_ids(self):
        from src.alerts.manager import ChatIdStore

        store = ChatIdStore()
        store.set_chat_id(111)
        store.set_chat_id(222)
        assert store.get_all_chat_ids() == {111, 222}

    def test_get_chat_id_returns_any_valid(self):
        from src.alerts.manager import ChatIdStore

        store = ChatIdStore()
        store.set_chat_id(111)
        store.set_chat_id(222)
        # Backward compat: get_chat_id returns any valid chat_id
        assert store.get_chat_id() in {111, 222}

    def test_deduplicates(self):
        from src.alerts.manager import ChatIdStore

        store = ChatIdStore()
        store.set_chat_id(111)
        store.set_chat_id(111)
        assert store.get_all_chat_ids() == {111}

    def test_get_all_empty(self):
        from src.alerts.manager import ChatIdStore

        store = ChatIdStore()
        assert store.get_all_chat_ids() == set()


class TestChatIdStorePersistence:
    """Tests for ChatIdStore JSON persistence (Bug: alerts lost after restart)."""

    def test_persists_to_disk(self, tmp_path):
        """Chat IDs saved by one store instance should be loadable by another."""
        from src.alerts.manager import ChatIdStore

        json_path = str(tmp_path / "chat_ids.json")

        store1 = ChatIdStore(json_path=json_path)
        store1.set_chat_id(111)
        store1.set_chat_id(222)

        # Simulate restart -- new store loads from same file
        store2 = ChatIdStore(json_path=json_path)
        assert store2.get_all_chat_ids() == {111, 222}

    def test_survives_missing_file(self, tmp_path):
        """Store should start empty when JSON file doesn't exist."""
        from src.alerts.manager import ChatIdStore

        json_path = str(tmp_path / "nonexistent" / "chat_ids.json")
        store = ChatIdStore(json_path=json_path)
        assert store.get_all_chat_ids() == set()

    def test_survives_corrupt_file(self, tmp_path):
        """Store should start empty when JSON file is corrupt."""
        from src.alerts.manager import ChatIdStore

        json_path = str(tmp_path / "chat_ids.json")
        with open(json_path, "w") as f:
            f.write("not json{{{")

        store = ChatIdStore(json_path=json_path)
        assert store.get_all_chat_ids() == set()

    def test_no_persistence_without_path(self):
        """Without json_path, store behaves as pure in-memory (backward compat)."""
        from src.alerts.manager import ChatIdStore

        store = ChatIdStore()  # No json_path
        store.set_chat_id(111)
        assert store.get_all_chat_ids() == {111}

    def test_only_saves_on_new_id(self, tmp_path):
        """Duplicate set_chat_id calls should not trigger extra writes."""
        from src.alerts.manager import ChatIdStore
        from unittest.mock import patch

        json_path = str(tmp_path / "chat_ids.json")
        store = ChatIdStore(json_path=json_path)

        with patch.object(store, "_save", wraps=store._save) as mock_save:
            store.set_chat_id(111)
            store.set_chat_id(111)  # duplicate
            store.set_chat_id(222)

            # _save called only for genuinely new IDs
            assert mock_save.call_count == 2


@pytest.mark.asyncio
async def test_proxy_sends_to_all_users():
    """AlertManagerProxy should send each alert to all registered chat IDs."""
    from src.alert_proxy import AlertManagerProxy
    from src.alerts.manager import ChatIdStore
    from unittest.mock import patch

    bot = MagicMock()
    store = ChatIdStore()
    store.set_chat_id(111)
    store.set_chat_id(222)

    proxy = AlertManagerProxy(bot, store)

    with patch("src.alert_proxy.AlertManager") as MockAM:
        mock_instance = MagicMock()
        mock_instance.send_crash_alert = AsyncMock()
        MockAM.return_value = mock_instance

        await proxy.send_crash_alert(
            container_name="test", exit_code=1, image="img"
        )

        # Should have created managers for both chat IDs
        assert MockAM.call_count == 2
        chat_ids_called = {call.args[1] for call in MockAM.call_args_list}
        assert chat_ids_called == {111, 222}


@pytest.mark.asyncio
async def test_proxy_flushes_queue_to_all_users():
    """Queued alerts should be flushed to all users when chat IDs become available."""
    from src.alert_proxy import AlertManagerProxy
    from src.alerts.manager import ChatIdStore
    from unittest.mock import patch

    bot = MagicMock()
    store = ChatIdStore()
    proxy = AlertManagerProxy(bot, store)

    # Queue alert while no users
    with patch("src.alert_proxy.AlertManager"):
        await proxy.send_crash_alert(container_name="r", exit_code=1, image="i")
    assert len(proxy._queued_alerts) == 1

    # Now register two users
    store.set_chat_id(111)
    store.set_chat_id(222)

    with patch("src.alert_proxy.AlertManager") as MockAM:
        mock_instance = MagicMock()
        mock_instance.send_crash_alert = AsyncMock()
        mock_instance.send_log_error_alert = AsyncMock()
        MockAM.return_value = mock_instance

        await proxy.send_log_error_alert(container_name="s", error_line="err")

        # Queued crash alert should have been sent to both + the new alert to both
        # Total: 2 (flush to 111, 222) + 2 (new alert to 111, 222) = 4 calls
        assert mock_instance.send_crash_alert.call_count == 2
        assert mock_instance.send_log_error_alert.call_count == 2


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


@pytest.mark.asyncio
async def test_alert_manager_sends_recovery_alert():
    """Test send_recovery_alert sends a brief recovery message."""
    from src.alerts.manager import AlertManager

    bot = MagicMock()
    bot.send_message = AsyncMock()

    manager = AlertManager(bot=bot, chat_id=12345)

    await manager.send_recovery_alert("radarr")

    bot.send_message.assert_called_once()
    call_args = bot.send_message.call_args
    assert call_args[1]["chat_id"] == 12345
    text = call_args[1]["text"]
    assert "radarr" in text
    assert "recovered" in text
    assert "✅" in text


async def test_send_autoheal_alert_restarted():
    from src.alerts.manager import AlertManager
    bot = MagicMock(); bot.send_message = AsyncMock()
    mgr = AlertManager(bot=bot, chat_id=123)
    await mgr.send_autoheal_alert(container_name="radarr", attempt=1, max_attempts=3, gave_up=False)
    bot.send_message.assert_awaited_once()
    text = bot.send_message.call_args.kwargs["text"]
    assert "Auto-restarted" in text and "radarr" in text


async def test_send_autoheal_alert_gave_up():
    from src.alerts.manager import AlertManager
    bot = MagicMock(); bot.send_message = AsyncMock()
    mgr = AlertManager(bot=bot, chat_id=123)
    await mgr.send_autoheal_alert(container_name="radarr", attempt=3, max_attempts=3, gave_up=True)
    text = bot.send_message.call_args.kwargs["text"]
    assert "gave up" in text.lower()
