"""Tier 4: credentials, alert routing, regex safety, provider resolution, array truth."""

import json
import logging
from unittest.mock import AsyncMock, MagicMock

from aiogram.types import Chat, Message, User

from src.alerts.ignore_manager import IgnoreManager, validate_regex_pattern
from src.alerts.manager import AlertManager, ChatIdStore
from src.alerts.recent_errors import RecentErrorsBuffer
from src.analysis.pattern_analyzer import PatternAnalyzer
from src.bot.ignore_command import ignore_similar_callback
from src.bot.telegram_bot import AuthMiddleware
from src.services.diagnostic import _filter_env_vars
from src.services.llm.anthropic_provider import AnthropicProvider
from src.services.llm.provider import LLMResponse
from src.services.nl_tools import NLToolExecutor
from src.state import ContainerStateManager
from src.utils.sanitize import sanitize_for_prompt

SECRETS = {
    "DB_PASSWORD=hunter2hunter2": "hunter2hunter2",
    "db password: Tr0ub4dor&3xyz": "Tr0ub4dor",
    "connecting to postgres://app:s3cretpw@db:5432/app": "s3cretpw",
    "SENTRY_DSN=https://abc123def@o1.ingest.sentry.io/42": "abc123def",
    "posting to https://discord.com/api/webhooks/123/AbCdEfGhIjKlMnOp": "AbCdEfGhIjKlMnOp",
    "bot 1234567890:AAH9xYzAbCdEfGhIjKlMnOpQrStUvWxYz12 failed": "AAH9xYzAbCdEfGhIjKlMnOpQrStUvWxYz12",
    "key sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123 rejected": "abcdefghijklmnopqrstuvwxyz0123",
    "auth_token: eyJhbGciOi.payload+/sig==": "payload+/sig==",
}


class FakeBot:
    def __init__(self) -> None:
        self.texts: list[str] = []
        self.send_message = AsyncMock(side_effect=self._send)

    async def _send(self, **kwargs):
        self.texts.append(kwargs["text"])
        return MagicMock()


# -- credential redaction ---------------------------------------------------------


def test_prompts_never_carry_credentials():
    for line, secret in SECRETS.items():
        assert secret not in sanitize_for_prompt(line), line


async def test_log_error_alert_redacts_credentials():
    bot = FakeBot()
    manager = AlertManager(bot, chat_id=1, error_display_max_chars=500)
    for line, secret in SECRETS.items():
        await manager.send_log_error_alert(container_name="app", error_line=f"ERROR {line}")
    for text in bot.texts:
        for secret in SECRETS.values():
            assert secret not in text


def test_diagnostic_env_filter_drops_credential_names():
    env = [
        "TZ=Europe/London", "PUID=99", "DB_PASS=x", "SMTP_PASSPHRASE=x", "SENTRY_DSN=x",
        "DISCORD_WEBHOOK=x", "SESSION_SECRET=x", "API_TOKEN=x", "COOKIE_KEY=x",
    ]
    assert _filter_env_vars(env) == ["TZ=Europe/London", "PUID=99"]


async def test_ignore_similar_still_finds_the_error_behind_a_redacted_preview(tmp_path):
    """The alert's button carries the redacted text; the lookup must match it."""
    bot = FakeBot()
    raw = "ERROR login failed password=hunter2hunter2 for admin"
    await AlertManager(bot, chat_id=1).send_log_error_alert(container_name="app", error_line=raw)
    keyboard = bot.send_message.call_args.kwargs["reply_markup"]
    data = keyboard.inline_keyboard[0][0].callback_data
    assert "hunter2" not in data

    buffer = RecentErrorsBuffer()
    buffer.add("app", raw)
    ignores = IgnoreManager({}, str(tmp_path / "ignored.json"))
    callback = MagicMock()
    callback.data = data
    callback.answer = AsyncMock()
    callback.message = MagicMock()
    callback.message.answer = AsyncMock()

    await ignore_similar_callback(ignores, None, buffer)(callback)

    assert ignores.is_ignored("app", raw)
    assert "hunter2" not in str(callback.message.answer.call_args)


# -- alert chats ---------------------------------------------------------------------


def _message(chat_type: str, chat_id: int, user_id: int = 1) -> Message:
    return Message.model_construct(
        message_id=1, date=0, chat=Chat(id=chat_id, type=chat_type),
        from_user=User(id=user_id, is_bot=False, first_name="u"), text="/status",
    )


async def test_group_chat_is_not_stored_for_alerts(tmp_path):
    store = ChatIdStore(json_path=str(tmp_path / "chat_ids.json"), allowed_users=[1])
    middleware = AuthMiddleware([1], chat_id_store=store)
    handler = AsyncMock()

    await middleware(handler, _message("group", -100123), {})
    await middleware(handler, _message("supergroup", -100456), {})
    assert store.get_all_chat_ids() == set()

    await middleware(handler, _message("private", 1), {})
    assert store.get_all_chat_ids() == {1}


def test_stored_chats_of_removed_users_are_dropped(tmp_path):
    path = tmp_path / "chat_ids.json"
    path.write_text(json.dumps([1, 2, -100123]))

    store = ChatIdStore(json_path=str(path), allowed_users=[1])

    assert store.get_all_chat_ids() == {1}
    assert json.loads(path.read_text()) == [1]


# -- regex safety ----------------------------------------------------------------------


def test_nested_quantifiers_are_rejected():
    for pattern in [r"(a+)+$", r"(\w*x)*y", r"(a|aa)*b", r"(?:ab+){2,}c"]:
        ok, _ = validate_regex_pattern(pattern)
        assert not ok, pattern
    for pattern in [r"Connection refused \d+", r"^\[\w+\] warn", r"timeout after \d+ms"]:
        assert validate_regex_pattern(pattern) == (True, "")


def test_unsafe_regex_in_the_ignore_file_is_dropped_on_load(tmp_path, caplog):
    path = tmp_path / "ignored.json"
    path.write_text(json.dumps({"app": [
        {"pattern": "(a+)+$", "match_type": "regex"},
        {"pattern": r"refused \d+", "match_type": "regex"},
    ]}))

    with caplog.at_level(logging.ERROR):
        ignores = IgnoreManager({}, str(path))

    assert [p for _, p, _ in ignores.get_runtime_ignores("app")] == [r"refused \d+"]
    assert "unsafe ignore regex" in caplog.text


async def test_model_regex_is_stored_as_a_literal_never_compiled():
    provider = MagicMock()
    provider.chat = AsyncMock(return_value=LLMResponse(
        text='{"pattern": "(a+)+ Timeout contacting [a-z]+ after \\\\d+s", '
             '"match_type": "regex", "explanation": "timeouts"}',
        stop_reason="end",
    ))
    result = await PatternAnalyzer(provider).analyze_error(
        "app", "ERROR Timeout contacting tracker after 30s", [],
    )
    assert result is not None
    assert result["match_type"] == "substring"
    assert result["pattern"] == "Timeout contacting"


async def test_model_literal_that_is_too_generic_is_refused():
    provider = MagicMock()
    provider.chat = AsyncMock(return_value=LLMResponse(
        text='{"pattern": "error", "match_type": "substring", "explanation": "errors"}',
        stop_reason="end",
    ))
    assert await PatternAnalyzer(provider).analyze_error("app", "error: disk full", []) is None


# -- provider resolution ------------------------------------------------------------------


async def test_pattern_analyzer_uses_the_model_picked_after_startup():
    """Built with no provider (none configured yet), then /model picks one."""
    registry = MagicMock()
    registry.get_provider.return_value = None
    analyzer = PatternAnalyzer(registry=registry)
    assert await analyzer.analyze_error("app", "Timeout contacting tracker", []) is None

    provider = MagicMock()
    provider.chat = AsyncMock(return_value=LLMResponse(
        text='{"pattern": "Timeout contacting", "match_type": "substring", "explanation": "x"}',
        stop_reason="end",
    ))
    registry.get_provider.return_value = provider
    result = await analyzer.analyze_error("app", "Timeout contacting tracker", [])
    assert result is not None and result["pattern"] == "Timeout contacting"
    registry.get_provider.assert_called_with("pattern_analyzer")


# -- array truth in chat --------------------------------------------------------------------


def _array(parity_status: str, disk_status: str) -> dict:
    return {
        "state": "STARTED",
        "capacity": {"kilobytes": {"used": 1, "total": 2, "free": 1}},
        "parityCheckStatus": {"status": parity_status, "progress": 45},
        "parities": [{"name": "parity", "status": "DISK_OK", "temp": 30}],
        "disks": [{"name": "disk1", "status": disk_status, "temp": 30}],
    }


async def test_nl_array_tool_does_not_call_a_rebuilding_disk_broken():
    unraid = MagicMock()
    unraid.get_array_status = AsyncMock(return_value=_array("RUNNING", "DISK_INVALID"))
    executor = NLToolExecutor(ContainerStateManager(), MagicMock(), unraid_system_monitor=unraid)

    text = await executor.execute("get_array_status", {})

    assert "being rebuilt" in text
    assert "have issues" not in text and "FAULT" not in text


async def test_nl_array_tool_reports_invalid_disk_outside_a_sync_as_a_fault():
    unraid = MagicMock()
    unraid.get_array_status = AsyncMock(return_value=_array("IDLE", "DISK_INVALID"))
    executor = NLToolExecutor(ContainerStateManager(), MagicMock(), unraid_system_monitor=unraid)

    text = await executor.execute("get_array_status", {})

    assert "1 disk(s) have issues" in text and "FAULT" in text


# -- token usage ------------------------------------------------------------------------------


async def test_each_llm_call_logs_its_token_usage(caplog):
    response = MagicMock()
    response.content = [MagicMock(type="text", text="hi")]
    response.stop_reason = "end_turn"
    response.usage = MagicMock(input_tokens=1200, output_tokens=80,
                               cache_read_input_tokens=1000, cache_creation_input_tokens=0)
    client = MagicMock()
    client.messages.create = AsyncMock(return_value=response)
    provider = AnthropicProvider(client=client, model="claude-test")

    with caplog.at_level(logging.INFO, logger="src.services.llm.usage"):
        result = await provider.chat(messages=[{"role": "user", "content": "x"}], max_tokens=10)

    assert result.usage["input_tokens"] == 1200 and result.usage["cache_read_input_tokens"] == 1000
    assert "input_tokens=1200" in caplog.text and "output_tokens=80" in caplog.text
