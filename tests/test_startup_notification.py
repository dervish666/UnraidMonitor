from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock

import src.startup as startup_mod
from src.bot.health_command import ai_summary, build_startup_lines, collect_status
from src.config import AutoHealConfig
from src.services.llm.provider import ModelInfo
from src.services.llm.registry import ProviderRegistry


def _ctx():
    bot = MagicMock()
    bot.send_message = AsyncMock()
    chat_store = MagicMock()
    chat_store.get_all_chat_ids.return_value = [555]
    state = MagicMock()
    state.get_all.return_value = [1, 2]
    uc = MagicMock()
    uc.client = None
    uc.system_monitor = uc.array_monitor = uc.notification_monitor = None
    uc.ups_monitor = None
    return bot, chat_store, state, uc


async def _send(bot, chat_store, state, uc, **overrides):
    kwargs = dict(
        image_update_monitor=None, auto_heal_config=AutoHealConfig(),
        resource_monitor=None, memory_monitor=None, log_watcher=None, monitor=None,
    )
    kwargs.update(overrides)
    await startup_mod._send_startup_notification(
        bot, chat_store, state, {"containers": []}, uc, **kwargs,
    )
    return bot.send_message.call_args.kwargs["text"]


async def test_whats_new_shown_on_version_change(tmp_path, monkeypatch):
    bot, chat_store, state, uc = _ctx()
    path = str(tmp_path / "announced_version.json")
    monkeypatch.setattr(startup_mod, "BOT_VERSION", "0.12.0")
    monkeypatch.setattr(startup_mod, "ANNOUNCED_VERSION_PATH", path)
    text = await _send(bot, chat_store, state, uc)
    assert "What's new" in text
    assert "Image-update detection" in text
    # What's new stays below everything else
    assert text.index("AI: off") < text.index("What's new")


async def test_whats_new_hidden_on_same_version(tmp_path, monkeypatch):
    bot, chat_store, state, uc = _ctx()
    path = str(tmp_path / "announced_version.json")
    from src.utils.version_store import write_announced_version
    write_announced_version(path, "0.12.0")
    monkeypatch.setattr(startup_mod, "BOT_VERSION", "0.12.0")
    monkeypatch.setattr(startup_mod, "ANNOUNCED_VERSION_PATH", path)
    text = await _send(bot, chat_store, state, uc)
    assert "What's new" not in text
    assert "Unraid Monitor is up" in text


async def test_whats_new_hidden_for_dev_version(tmp_path, monkeypatch):
    bot, chat_store, state, uc = _ctx()
    path = str(tmp_path / "announced_version.json")
    monkeypatch.setattr(startup_mod, "BOT_VERSION", "0.99.0")
    monkeypatch.setattr(startup_mod, "ANNOUNCED_VERSION_PATH", path)
    text = await _send(bot, chat_store, state, uc)
    assert "What's new" not in text
    assert "Unraid Monitor is up" in text


# ---------------------------------------------------------------------------
# Collapsed layout
# ---------------------------------------------------------------------------

def _ups(*, running=True, available=True, polled=True, never=False, last_error=None):
    return NS(
        is_running=running, is_available=available, ups_name="apc", last_error=last_error,
        has_polled=polled, never_reached=never, target="tower:3493",
    )


def _healthy(**overrides):
    kwargs = dict(
        monitor=NS(is_running=True, state_manager=NS(get_all=lambda: list(range(63)))),
        log_watcher=NS(is_running=True, containers=list(range(9)), total_drops=0),
        resource_monitor=NS(is_running=True),
        memory_monitor=NS(is_running=True),
        image_update_monitor=NS(is_running=True),
        auto_heal_config=NS(enabled=True, containers=list(range(9))),
        ups_monitor=_ups(),
        unraid_client=NS(is_connected=True),
        unraid_system_monitor=NS(is_running=True),
        unraid_array_monitor=NS(is_running=True),
        unraid_notification_monitor=NS(is_running=True),
    )
    kwargs.update(overrides)
    return collect_status(**kwargs)


def _render(items, ai_lines=("AI: off (no API key set)",), ai_problems=()):
    return "\n".join(build_startup_lines(
        items, version="0.22.2", ai_lines=list(ai_lines), ai_problems=list(ai_problems),
    ))


def _anthropic_registry(tmp_path, **kwargs):
    return ProviderRegistry(
        anthropic_client=MagicMock(name="anthropic"),
        data_dir=str(tmp_path),
        discovered_anthropic_models=["claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5"],
        model_display_names={"claude-opus-5-5": "Claude Opus 5.5"},
        **kwargs,
    )


def test_all_healthy_matches_variant_a(tmp_path):
    reg = _anthropic_registry(
        tmp_path, default_model="opus",
        feature_models={"nl_processor": "sonnet", "diagnostic": "opus", "pattern_analyzer": "opus"},
    )
    ai_lines, ai_problems = ai_summary(reg)
    text = _render(_healthy(), ai_lines, ai_problems)
    assert text == (
        "🟢 *Unraid Monitor is up* · v0.22.2\n"
        "\n"
        "Everything's running: Docker events, logs, resources, memory, image updates, UPS and Unraid.\n"
        "Watching 63 containers, 9 for log errors, 9 with auto-heal.\n"
        "\n"
        "AI: Claude Opus 5.5\n"
        "Chat uses Claude Sonnet 5.5."
    )
    assert "✅" not in text


def test_unraid_down_and_ups_off_matches_degraded_variant():
    items = _healthy(
        image_update_monitor=None,
        ups_monitor=None,
        unraid_client=NS(is_connected=False),
    )
    # Image updates is off too, so three things need a look here
    text = _render(items)
    lines = text.split("\n")
    assert lines[0] == "🟡 *Unraid Monitor is up* · v0.22.2 · 3 things need a look"
    assert lines[2] == (
        "🔴 Unraid: can't reach the server. System, array and notification alerts "
        "wait until it reconnects."
    )
    assert lines[3] == "⚪ Off: image updates and UPS. Turn them on in /manage → Features."
    assert "Running: Docker events, logs, resources, memory." in lines
    # Problems above the summary, nothing indented
    assert lines.index("Running: Docker events, logs, resources, memory.") > 3
    assert not any(line.startswith(" ") for line in lines)


def test_single_off_item_says_it():
    text = _render(_healthy(ups_monitor=None))
    assert "· 1 thing needs a look" in text
    assert "⚪ Off: UPS. Turn it on in /manage → Features." in text


def test_docker_events_down_gives_red_header():
    items = _healthy(monitor=NS(is_running=False, state_manager=NS(get_all=lambda: [])))
    text = _render(items)
    assert text.startswith("🔴 *Unraid Monitor is up*")
    assert "🔴 Docker events: stopped." in text
    assert "Watching" not in text


def test_ups_first_poll_pending_is_not_a_problem():
    text = _render(_healthy(ups_monitor=_ups(available=False, polled=False)))
    assert text.startswith("🟢")
    assert "UPS (connecting)" in text
    assert "Unavailable" not in text


def test_ups_with_no_nut_server_stays_quiet():
    """Most installs run no UPS. A NUT server that never answered is not a problem."""
    text = _render(_healthy(ups_monitor=_ups(available=False, never=True, last_error="refused")))
    assert text.startswith("🟢 *Unraid Monitor is up* · v0.22.2\n")
    assert "UPS" not in text
    assert "Everything's running: Docker events, logs, resources, memory, image updates and Unraid." in text


def test_ups_turned_off_on_purpose_still_shows_off():
    text = _render(_healthy(ups_monitor=None))
    assert "⚪ Off: UPS. Turn it on in /manage → Features." in text


def test_ups_that_answered_then_stopped_is_red():
    text = _render(_healthy(ups_monitor=_ups(available=False, last_error="timed out")))
    assert text.startswith("🟡")
    assert "🔴 UPS: lost contact with the NUT server at tower:3493." in text


def test_health_still_details_an_unreached_ups():
    from src.bot.health_command import build_status_lines
    lines = build_status_lines(ups_monitor=_ups(available=False, never=True, last_error="refused"))
    assert "  UPS: ⚠️ Unavailable (refused)" in lines


def test_no_providers_is_ai_off_and_not_a_problem(tmp_path):
    reg = ProviderRegistry(data_dir=str(tmp_path))
    ai_lines, ai_problems = ai_summary(reg)
    assert ai_lines == ["AI: off (no API key set)"]
    assert ai_problems == []
    assert ai_summary(None) == (["AI: off (no API key set)"], [])
    text = _render(_healthy(), ai_lines, ai_problems)
    assert text.startswith("🟢")


def test_overrides_grouped_by_model(tmp_path):
    reg = _anthropic_registry(
        tmp_path, default_model="opus",
        feature_models={"nl_processor": "sonnet", "diagnostic": "haiku", "pattern_analyzer": "haiku"},
    )
    lines, _ = ai_summary(reg)
    assert lines == [
        "AI: Claude Opus 5.5",
        "Chat uses Claude Sonnet 5.5.",
        "Diagnose and analyze use Claude Haiku 5.5.",
    ]


def test_no_override_line_when_every_feature_uses_default(tmp_path):
    reg = _anthropic_registry(tmp_path, default_model="opus")
    lines, _ = ai_summary(reg)
    assert lines == ["AI: Claude Opus 5.5"]


def test_underscore_in_model_name_is_escaped(tmp_path):
    reg = ProviderRegistry(
        ollama_client=MagicMock(name="ollama"),
        ollama_models=[ModelInfo(id="my_model:7b", name="my_model:7b", provider="ollama")],
        default_model="my_model:7b",
        data_dir=str(tmp_path),
    )
    lines, _ = ai_summary(reg)
    assert lines == ["AI: my\\_model:7b"]


def test_rejected_anthropic_key_is_a_problem(tmp_path):
    reg = _anthropic_registry(
        tmp_path, default_model="opus",
        provider_problems={"anthropic": "Anthropic rejected the API key"},
    )
    ai_lines, ai_problems = ai_summary(reg)
    assert ai_lines == []
    assert ai_problems == [
        "🔴 AI: Anthropic rejected the API key. Chat and /diagnose are off until it's fixed."
    ]
    text = _render(_healthy(), ai_lines, ai_problems)
    assert text.startswith("🟡 *Unraid Monitor is up* · v0.22.2 · 1 thing needs a look")


async def test_startup_passes_ups_monitor_through(tmp_path, monkeypatch):
    """Regression: startup never passed ups_monitor, so the message always said UPS was off."""
    bot, chat_store, state, uc = _ctx()
    uc.ups_monitor = _ups()
    monkeypatch.setattr(startup_mod, "ANNOUNCED_VERSION_PATH", str(tmp_path / "v.json"))
    text = await _send(bot, chat_store, state, uc)
    assert "⚪ Off: UPS" not in text
    assert "UPS" in text.split("Running:")[1]


async def test_startup_message_shows_registry_models(tmp_path, monkeypatch):
    bot, chat_store, state, uc = _ctx()
    monkeypatch.setattr(startup_mod, "ANNOUNCED_VERSION_PATH", str(tmp_path / "v.json"))
    reg = _anthropic_registry(tmp_path, default_model="opus")
    text = await _send(bot, chat_store, state, uc, registry=reg)
    assert "AI: Claude Opus 5.5" in text


async def test_startup_log_cannot_trip_the_log_watcher(tmp_path, monkeypatch, caplog):
    """The bot watches its own logs. v0.22.2 logged the whole message, and the line
    "Watching 63 containers, 9 for log errors" came back as a log-error alert."""
    bot, chat_store, state, uc = _ctx()
    monkeypatch.setattr(startup_mod, "ANNOUNCED_VERSION_PATH", str(tmp_path / "v.json"))
    with caplog.at_level("DEBUG", logger="src.startup"):
        await _send(
            bot, chat_store, state, uc,
            log_watcher=NS(is_running=True, containers=[1, 2], total_drops=0),
            monitor=NS(is_running=True, state_manager=NS(get_all=lambda: [1, 2])),
        )
    assert "for log errors" in bot.send_message.call_args.kwargs["text"]
    for record in caplog.records:
        message = record.getMessage()
        assert "\n" not in message
        assert "error" not in message.lower()
