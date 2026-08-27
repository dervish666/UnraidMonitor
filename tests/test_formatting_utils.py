"""Tests for formatting utility functions (F43)."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timedelta

from aiogram.exceptions import TelegramBadRequest

from src.utils.formatting import (
    validate_container_name,
    safe_reply,
    safe_edit,
    escape_markdown,
    truncate_message,
    format_mute_expiry,
    extract_container_from_alert,
    truncate_callback_data,
    format_bytes,
    format_uptime,
    _strip_markdown,
)


class TestValidateContainerName:
    """Tests for validate_container_name."""

    def test_valid_simple_name(self):
        assert validate_container_name("plex") is True

    def test_valid_name_with_dashes(self):
        assert validate_container_name("my-container") is True

    def test_valid_name_with_underscores(self):
        assert validate_container_name("my_container_1") is True

    def test_valid_name_with_dots(self):
        assert validate_container_name("app.service.v2") is True

    def test_valid_name_with_colons(self):
        assert validate_container_name("compose:service:name") is True

    def test_empty_string_rejected(self):
        assert validate_container_name("") is False

    def test_too_long_rejected(self):
        assert validate_container_name("a" * 257) is False

    def test_max_length_accepted(self):
        assert validate_container_name("a" * 256) is True

    def test_starts_with_dash_rejected(self):
        assert validate_container_name("-invalid") is False

    def test_starts_with_dot_rejected(self):
        assert validate_container_name(".invalid") is False

    def test_slash_rejected(self):
        """F2: Forward slash should not be allowed."""
        assert validate_container_name("path/container") is False

    def test_space_rejected(self):
        assert validate_container_name("has space") is False

    def test_special_chars_rejected(self):
        assert validate_container_name("name@host") is False
        assert validate_container_name("name#1") is False


class TestSafeReply:
    """Tests for safe_reply."""

    @pytest.mark.asyncio
    async def test_sends_with_markdown(self):
        message = AsyncMock()
        message.answer = AsyncMock(return_value=AsyncMock())

        await safe_reply(message, "*bold text*")

        message.answer.assert_called_once_with("*bold text*", parse_mode="Markdown")

    @pytest.mark.asyncio
    async def test_falls_back_on_parse_error(self):
        message = AsyncMock()
        message.answer = AsyncMock(
            side_effect=[
                TelegramBadRequest(method=MagicMock(), message="can't parse entities"),
                AsyncMock(),
            ]
        )

        await safe_reply(message, "*bad markdown[")

        # Second call should strip markdown and send plain
        assert message.answer.call_count == 2
        second_call = message.answer.call_args_list[1]
        assert second_call.kwargs.get("parse_mode") is None

    @pytest.mark.asyncio
    async def test_reraises_other_errors(self):
        message = AsyncMock()
        message.answer = AsyncMock(
            side_effect=TelegramBadRequest(method=MagicMock(), message="message too long")
        )

        with pytest.raises(TelegramBadRequest):
            await safe_reply(message, "text")


class TestSafeEdit:
    """Tests for safe_edit."""

    @pytest.mark.asyncio
    async def test_edits_with_markdown(self):
        message = AsyncMock()
        message.edit_text = AsyncMock(return_value=AsyncMock())

        await safe_edit(message, "*edited*")

        message.edit_text.assert_called_once_with("*edited*", parse_mode="Markdown")

    @pytest.mark.asyncio
    async def test_falls_back_on_parse_error(self):
        message = AsyncMock()
        message.edit_text = AsyncMock(
            side_effect=[
                TelegramBadRequest(method=MagicMock(), message="can't parse entities"),
                AsyncMock(),
            ]
        )

        await safe_edit(message, "*bad markdown[")

        assert message.edit_text.call_count == 2

    @pytest.mark.asyncio
    async def test_passes_kwargs(self):
        message = AsyncMock()
        message.edit_text = AsyncMock(return_value=AsyncMock())
        keyboard = MagicMock()

        await safe_edit(message, "text", reply_markup=keyboard)

        message.edit_text.assert_called_once_with(
            "text", parse_mode="Markdown", reply_markup=keyboard
        )

    @pytest.mark.asyncio
    async def test_unchanged_content_is_not_an_error(self):
        """A Refresh that found nothing new must not surface as a failure."""
        message = AsyncMock()
        message.edit_text = AsyncMock(
            side_effect=TelegramBadRequest(
                method=MagicMock(), message="message is not modified"
            )
        )

        assert await safe_edit(message, "same text") is False

    @pytest.mark.asyncio
    async def test_other_bad_requests_still_raise(self):
        message = AsyncMock()
        message.edit_text = AsyncMock(
            side_effect=TelegramBadRequest(method=MagicMock(), message="message to edit not found")
        )

        with pytest.raises(TelegramBadRequest):
            await safe_edit(message, "text")


class TestEscapeMarkdown:
    """Tests for escape_markdown."""

    def test_escapes_asterisks(self):
        assert escape_markdown("*bold*") == "\\*bold\\*"

    def test_escapes_underscores(self):
        assert escape_markdown("_italic_") == "\\_italic\\_"

    def test_escapes_backticks(self):
        assert escape_markdown("`code`") == "\\`code\\`"

    def test_escapes_brackets(self):
        assert escape_markdown("[link]") == "\\[link]"

    def test_escapes_backslashes(self):
        assert escape_markdown("a\\b") == "a\\\\b"

    def test_plain_text_unchanged(self):
        assert escape_markdown("hello world") == "hello world"

    def test_container_name_with_special_chars(self):
        result = escape_markdown("my_container*v2")
        assert "\\_" in result
        assert "\\*" in result

    def test_empty_string(self):
        assert escape_markdown("") == ""


class TestStripMarkdown:
    """Tests for _strip_markdown including F17 bracket fix."""

    def test_strips_asterisks(self):
        assert _strip_markdown("*bold*") == "bold"

    def test_strips_backticks(self):
        assert _strip_markdown("`code`") == "code"

    def test_strips_underscores(self):
        assert _strip_markdown("_italic_") == "italic"

    def test_strips_brackets(self):
        """F17: Brackets should be stripped."""
        assert _strip_markdown("[link](url)") == "link(url)"

    def test_strips_all_combined(self):
        assert _strip_markdown("*_`[text]`_*") == "text"


class TestTruncateMessage:
    """Tests for truncate_message."""

    def test_short_message_unchanged(self):
        text = "short message"
        assert truncate_message(text) == text

    def test_exact_limit_unchanged(self):
        text = "x" * 4096
        assert truncate_message(text) == text

    def test_over_limit_truncated(self):
        text = "x" * 5000
        result = truncate_message(text)
        assert len(result) <= 4096
        assert "_(truncated)_" in result

    def test_custom_limit(self):
        text = "x" * 200
        result = truncate_message(text, max_length=100)
        assert len(result) <= 100

    def test_custom_suffix(self):
        text = "x" * 200
        result = truncate_message(text, max_length=100, suffix="...")
        assert result.endswith("...")


class TestFormatMuteExpiry:
    """Tests for format_mute_expiry."""

    def test_same_day(self):
        from unittest.mock import patch
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("Europe/London")
        # Pin "now" to midday so expiry (+1h) cannot roll into tomorrow.
        # Using the real clock made this flaky in the last hour before midnight.
        fixed_now = datetime(2026, 6, 2, 12, 0, tzinfo=tz)
        with patch("src.utils.formatting.datetime") as mock_dt:
            mock_dt.now.return_value = fixed_now
            expiry = fixed_now + timedelta(hours=1)
            result = format_mute_expiry(expiry)
        assert result.startswith("until ")
        assert "tomorrow" not in result

    def test_tomorrow(self):
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("Europe/London")
        now = datetime.now(tz)
        # Set expiry to tomorrow same time
        expiry = now + timedelta(days=1)
        result = format_mute_expiry(expiry)
        assert "tomorrow" in result

    def test_further_future(self):
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("Europe/London")
        now = datetime.now(tz)
        expiry = now + timedelta(days=5)
        result = format_mute_expiry(expiry)
        assert "until" in result
        # Should include month abbreviation
        assert any(month in result for month in [
            "Jan", "Feb", "Mar", "Apr", "May", "Jun",
            "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
        ])

    def test_naive_datetime_converted(self):
        """Naive datetimes should be treated as Europe/London."""
        future = datetime.now() + timedelta(hours=2)
        result = format_mute_expiry(future)
        assert result.startswith("until ")


class TestExtractContainerFromAlert:
    """Tests for extract_container_from_alert.

    Inputs here are the *rendered* text Telegram hands back on a reply, with
    formatting stripped. End-to-end coverage against what AlertManager really
    sends lives in test_alert_reply_extraction.py.
    """

    def test_errors_in_pattern(self):
        assert extract_container_from_alert("⚠️ ERRORS IN: plex") == "plex"

    def test_crashed_pattern(self):
        assert extract_container_from_alert("🔴 CONTAINER CRASHED: radarr") == "radarr"

    def test_high_usage_pattern(self):
        assert extract_container_from_alert("⚠️ HIGH CPU USAGE: sonarr") == "sonarr"

    def test_unhealthy_pattern(self):
        assert extract_container_from_alert("🏥 UNHEALTHY: jellyfin") == "jellyfin"

    def test_autoheal_pattern(self):
        text = "🔧 Auto-restarted: sabnzbd\n\nContainer was unhealthy - auto-restarted (attempt 1/3)."
        assert extract_container_from_alert(text) == "sabnzbd"

    def test_container_pattern(self):
        assert extract_container_from_alert("Container: jellyfin") == "jellyfin"

    def test_no_match(self):
        assert extract_container_from_alert("Random text") is None

    def test_empty_string(self):
        assert extract_container_from_alert("") is None

    def test_container_with_dashes(self):
        assert extract_container_from_alert("CRASHED: my-app-v2") == "my-app-v2"

    def test_restart_loop_beats_the_crash_count_in_the_body(self):
        """Regression: "Crashed 4 times" used to be extracted as container "4"."""
        text = "🔄🔴 RESTART LOOP: plex\n\nCrashed 4 times in the last 10 minutes!\nExit code: 1"
        assert extract_container_from_alert(text) == "plex"

    def test_autoheal_body_is_not_mistaken_for_a_name(self):
        """Regression: "Container was unhealthy" used to be extracted as "was"."""
        assert extract_container_from_alert("Container was unhealthy - auto-restarted.") is None

    def test_server_alert_yields_nothing(self):
        assert extract_container_from_alert("🖥️ SERVER ALERT: Memory Critical\n\nAt 96%") is None


class TestTruncateCallbackData:
    """Tests for truncate_callback_data (F1)."""

    def test_short_data_unchanged(self):
        result = truncate_callback_data("restart:", "plex")
        assert result == "restart:plex"

    def test_within_64_bytes(self):
        result = truncate_callback_data("mute:", "container:3600")
        assert result == "mute:container:3600"
        assert len(result.encode("utf-8")) <= 64

    def test_long_name_truncated(self):
        long_name = "a" * 100
        result = truncate_callback_data("restart:", long_name)
        assert len(result.encode("utf-8")) <= 64
        assert result.endswith("…")

    def test_unicode_name_truncated_safely(self):
        # Multi-byte chars should not be broken mid-character
        unicode_name = "\U0001f600" * 20  # 80 bytes of emoji
        result = truncate_callback_data("r:", unicode_name)
        assert len(result.encode("utf-8")) <= 64
        # Should be valid UTF-8
        result.encode("utf-8").decode("utf-8")

    def test_exact_64_bytes(self):
        # prefix "x:" is 2 bytes, so data can be 62 bytes
        data = "a" * 62
        result = truncate_callback_data("x:", data)
        assert result == "x:" + data
        assert len(result.encode("utf-8")) == 64

    def test_one_over_64_bytes(self):
        data = "a" * 63
        result = truncate_callback_data("x:", data)
        assert len(result.encode("utf-8")) <= 64
        assert result.endswith("…")


class TestFormatBytes:
    """Tests for format_bytes."""

    def test_gigabytes(self):
        assert format_bytes(2 * 1024**3) == "2.0GB"

    def test_megabytes(self):
        assert format_bytes(512 * 1024**2) == "512MB"

    def test_fractional_gigabytes(self):
        assert format_bytes(int(1.5 * 1024**3)) == "1.5GB"


class TestFormatUptime:
    """Tests for format_uptime."""

    def test_days_hours_minutes(self):
        assert format_uptime(3 * 86400 + 14 * 3600 + 22 * 60) == "3d 14h 22m"

    def test_hours_minutes(self):
        assert format_uptime(2 * 3600 + 15 * 60) == "2h 15m"

    def test_minutes_only(self):
        assert format_uptime(45 * 60) == "45m"

    def test_zero(self):
        assert format_uptime(0) == "0m"

    def test_negative(self):
        assert format_uptime(-100) == "0m"
