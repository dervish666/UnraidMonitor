"""Reply-to-alert extraction, tested against the alerts AlertManager really sends.

The bug this guards against: `extract_container_from_alert` matched formats that
`AlertManager` had stopped producing, so replying `/mute 1h` to a restart-loop
alert muted a container called "4" (the crash count) and reported success. The
old tests passed throughout, because they asserted against hand-written strings
nobody had checked against the sender.

So these tests send real alerts, render them the way Telegram does, and assert
the container name survives the round trip. Change an alert headline in
`manager.py` and one of these fails.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.alerts.manager import AlertManager
from src.utils.formatting import extract_alert_container


def render_like_telegram(text: str) -> str:
    """Approximate what Telegram puts in `Message.text` for a Markdown message.

    Bot API strips formatting markers and keeps the plain text; the markup is
    returned separately in `entities`. Escapes (`\\_`) become the bare
    character. This is why patterns must never expect literal asterisks.
    """
    out = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            out.append(text[i + 1])
            i += 2
            continue
        if ch in "*`":
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


@pytest.fixture
def sent():
    """AlertManager wired to a fake bot, plus a reader for the last text sent."""
    bot = MagicMock()
    bot.send_message = AsyncMock()
    manager = AlertManager(bot=bot, chat_id=12345)

    def last_text() -> str:
        return render_like_telegram(bot.send_message.call_args.kwargs["text"])

    return manager, last_text


async def _resource_alert(manager, name, metric="cpu"):
    await manager.send_resource_alert(
        container_name=name,
        metric=metric,
        current_value=95.0,
        threshold=80,
        duration_seconds=600,
        memory_bytes=2_000_000_000,
        memory_limit=4_000_000_000,
        memory_percent=50.0,
        cpu_percent=95.0,
    )


@pytest.mark.parametrize("name", ["plex", "my_media_server", "my-app-v2", "app.one"])
@pytest.mark.asyncio
async def test_every_container_alert_round_trips(sent, name):
    """Each alert that names a container must give that name back on reply."""
    manager, last_text = sent

    async def crash():
        await manager.send_crash_alert(container_name=name, exit_code=137, image="img", uptime_seconds=9240)

    async def restart_loop():
        await manager.send_crash_alert(
            container_name=name, exit_code=1, image="img", uptime_seconds=60, restart_loop_count=4
        )

    async def log_error():
        await manager.send_log_error_alert(container_name=name, error_line="boom", suppressed_count=0)

    async def health():
        await manager.send_health_alert(container_name=name, health_status="unhealthy")

    async def autoheal():
        await manager.send_autoheal_alert(container_name=name, attempt=1, max_attempts=3, gave_up=False)

    async def autoheal_failed():
        await manager.send_autoheal_alert(
            container_name=name, attempt=1, max_attempts=3, gave_up=False, failed=True
        )

    async def autoheal_gave_up():
        await manager.send_autoheal_alert(container_name=name, attempt=3, max_attempts=3, gave_up=True)

    senders = [
        crash,
        restart_loop,
        log_error,
        health,
        autoheal,
        autoheal_failed,
        autoheal_gave_up,
        lambda: _resource_alert(manager, name, "cpu"),
        lambda: _resource_alert(manager, name, "memory"),
    ]

    for send in senders:
        await send()
        text = last_text()
        got, description = extract_alert_container(text)
        assert got == name, f"{send.__name__ if hasattr(send, '__name__') else send} -> {got!r} from {text!r}"
        assert description, "every match should name the alert type"


@pytest.mark.asyncio
async def test_restart_loop_does_not_extract_the_crash_count(sent):
    """The regression: 'Crashed 4 times' in the body must not win over the headline."""
    manager, last_text = sent
    await manager.send_crash_alert(
        container_name="plex", exit_code=1, image="img", uptime_seconds=60, restart_loop_count=4
    )
    text = last_text()
    assert "Crashed 4 times" in text, "body wording changed; this test is guarding the wrong thing"
    assert extract_alert_container(text)[0] == "plex"


@pytest.mark.asyncio
async def test_alerts_carry_no_literal_markdown_asterisks_after_render(sent):
    """Guard the assumption the patterns rest on."""
    manager, last_text = sent
    await manager.send_log_error_alert(container_name="plex", error_line="boom", suppressed_count=0)
    assert "*" not in last_text()


@pytest.mark.parametrize(
    "text",
    [
        "🖥️ SERVER ALERT: Memory Critical\n\nMemory at 96%",
        "🔌 UPS ALERT: On Battery\n\nRuntime 12m",
        "💾 Array Capacity Warning\n\nUsage: 91% (threshold: 90%)",
        "🔴 Memory Critical\n\nMemory at 96%",
        "Random text",
        "",
    ],
)
def test_server_level_alerts_yield_no_container(text):
    """Replying /mute to a server alert must fail honestly, not invent a name."""
    assert extract_alert_container(text) == (None, "")
