from unittest.mock import AsyncMock, MagicMock

import src.startup as startup_mod
from src.config import AutoHealConfig


def _ctx():
    bot = MagicMock(); bot.send_message = AsyncMock()
    chat_store = MagicMock(); chat_store.get_all_chat_ids.return_value = [555]
    state = MagicMock(); state.get_all.return_value = [1, 2]
    uc = MagicMock(); uc.client = None
    return bot, chat_store, state, uc


async def test_whats_new_shown_on_version_change(tmp_path, monkeypatch):
    bot, chat_store, state, uc = _ctx()
    path = str(tmp_path / "announced_version.json")
    monkeypatch.setattr(startup_mod, "BOT_VERSION", "0.12.0")
    monkeypatch.setattr(startup_mod, "ANNOUNCED_VERSION_PATH", path)
    await startup_mod._send_startup_notification(
        bot, chat_store, state, {"containers": []}, uc,
        image_update_monitor=None, auto_heal_config=AutoHealConfig(),
        resource_monitor=None, memory_monitor=None, log_watcher=None, monitor=None,
    )
    text = bot.send_message.call_args.kwargs["text"]
    assert "What's new" in text
    assert "Image-update detection" in text


async def test_whats_new_hidden_on_same_version(tmp_path, monkeypatch):
    bot, chat_store, state, uc = _ctx()
    path = str(tmp_path / "announced_version.json")
    from src.utils.version_store import write_announced_version
    write_announced_version(path, "0.12.0")
    monkeypatch.setattr(startup_mod, "BOT_VERSION", "0.12.0")
    monkeypatch.setattr(startup_mod, "ANNOUNCED_VERSION_PATH", path)
    await startup_mod._send_startup_notification(
        bot, chat_store, state, {"containers": []}, uc,
        image_update_monitor=None, auto_heal_config=AutoHealConfig(),
        resource_monitor=None, memory_monitor=None, log_watcher=None, monitor=None,
    )
    text = bot.send_message.call_args.kwargs["text"]
    assert "What's new" not in text
    assert "Bot started" in text


async def test_whats_new_hidden_for_dev_version(tmp_path, monkeypatch):
    bot, chat_store, state, uc = _ctx()
    path = str(tmp_path / "announced_version.json")
    monkeypatch.setattr(startup_mod, "BOT_VERSION", "0.99.0")
    monkeypatch.setattr(startup_mod, "ANNOUNCED_VERSION_PATH", path)
    await startup_mod._send_startup_notification(
        bot, chat_store, state, {"containers": []}, uc,
        image_update_monitor=None, auto_heal_config=AutoHealConfig(),
        resource_monitor=None, memory_monitor=None, log_watcher=None, monitor=None,
    )
    text = bot.send_message.call_args.kwargs["text"]
    assert "What's new" not in text
    assert "Bot started" in text
