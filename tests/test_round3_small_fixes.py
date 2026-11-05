"""Round 3 section H: mute expiry notices, bot-URL redaction, redact-before-truncate."""

from datetime import datetime, timedelta

from src.alerts.server_mute_manager import ServerMuteManager
from src.utils.sanitize import _MAX_LINE_LENGTH, sanitize_for_prompt

_TOKEN = "AAH" + "x" * 32  # 35 chars, real Telegram token shape


def test_mute_server_expiry_drains_as_one_batch(tmp_path):
    """One /mute-server ending must surface every category in one drain.

    is_server_muted() expires the system key alone; before the fix the array
    and UPS keys trickled out on later maintenance passes as extra notices.
    """
    manager = ServerMuteManager(str(tmp_path / "server_mutes.json"))
    manager.mute_server(timedelta(hours=1))
    past = datetime.now() - timedelta(seconds=1)
    for cat in ServerMuteManager.CATEGORIES:
        manager._mutes[cat] = past

    assert manager.is_server_muted() is False  # monitor check expires "server" only
    assert sorted(manager.drain_expired()) == ["array", "server", "ups"]
    assert manager.drain_expired() == []


def test_telegram_bot_api_url_token_redacted():
    """Sonarr/Radarr log the Bot API URL; the id has no word boundary after 'bot'."""
    line = f"POST https://api.telegram.org/bot123456789:{_TOKEN}/sendMessage failed"
    out = sanitize_for_prompt(line)
    assert _TOKEN not in out
    assert "123456789:" not in out


def test_secret_redacted_before_line_truncation():
    """A cut at the line limit must not leave a recognisable token prefix behind."""
    pad = "x" * (_MAX_LINE_LENGTH - 20)
    line = f"{pad} token 123456789:{_TOKEN}"
    out = sanitize_for_prompt(line)
    assert "123456789:AAH" not in out
