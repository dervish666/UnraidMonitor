"""Tests for the Unraid notification relay."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config import UnraidConfig
from src.unraid.monitors.notification_monitor import (
    UnraidNotificationMonitor,
    _importance_rank,
)


def _notification(nid, importance="WARNING", title="Disk Problem", subject="s", description="d"):
    return {
        "id": nid,
        "title": title,
        "subject": subject,
        "description": description,
        "importance": importance,
        "link": "/Main",
        "timestamp": "2026-08-02T06:18:07.000Z",
        "formattedTimestamp": "Sun 02 Aug 2026 07:18:07 AM",
    }


def _monitor(notifications, state_path=None, importance="WARNING", muted=False, primed=True):
    client = MagicMock()
    client.get_notifications = AsyncMock(return_value=notifications)
    config = UnraidConfig(notifications_enabled=True, notifications_min_importance=importance)
    mute = MagicMock()
    mute.is_server_muted.return_value = muted
    sent: list[tuple[str, str]] = []

    async def on_alert(title, message, alert_type):
        sent.append((title, message))

    monitor = UnraidNotificationMonitor(client, config, on_alert, mute, state_path)
    if primed:
        monitor._primed = True
    return monitor, sent, config


@pytest.mark.asyncio
async def test_first_run_primes_instead_of_replaying_the_backlog():
    monitor, sent, _ = _monitor([_notification("a"), _notification("b")], primed=False)

    relayed = await monitor.check_once()

    assert relayed == 0
    assert sent == []


@pytest.mark.asyncio
async def test_new_notification_is_relayed():
    monitor, sent, _ = _monitor([_notification("a", "ALERT", title="Disk Error")])

    relayed = await monitor.check_once()

    assert relayed == 1
    assert sent[0][0] == "🔴 Disk Error"


@pytest.mark.asyncio
async def test_below_the_floor_is_filtered():
    monitor, sent, _ = _monitor([_notification("a", "INFO")], importance="WARNING")

    assert await monitor.check_once() == 0
    assert sent == []


@pytest.mark.asyncio
async def test_lowering_the_floor_lets_info_through():
    monitor, sent, config = _monitor([_notification("a", "INFO")], importance="WARNING")
    assert await monitor.check_once() == 0

    # Applies live -- the monitor re-reads the shared config object each poll.
    config.notifications_min_importance = "INFO"

    assert await monitor.check_once() == 1


@pytest.mark.asyncio
async def test_unknown_importance_is_relayed_not_dropped():
    """A value from a future Unraid release must reach the user."""
    monitor, sent, _ = _monitor([_notification("a", "CATASTROPHE")], importance="ALERT")

    assert await monitor.check_once() == 1


@pytest.mark.asyncio
async def test_already_relayed_is_not_repeated():
    monitor, sent, _ = _monitor([_notification("a")])
    await monitor.check_once()
    sent.clear()

    assert await monitor.check_once() == 0
    assert sent == []


@pytest.mark.asyncio
async def test_dedup_survives_a_restart(tmp_path):
    state = str(tmp_path / "seen.json")
    monitor, sent, _ = _monitor([_notification("a")], state_path=state)
    await monitor.check_once()
    assert len(sent) == 1

    restarted, sent2, _ = _monitor([_notification("a")], state_path=state)
    assert await restarted.check_once() == 0
    assert sent2 == []


@pytest.mark.asyncio
async def test_a_failed_send_is_retried_next_poll(tmp_path):
    """Marking as seen before delivery would lose the alert permanently."""
    client = MagicMock()
    client.get_notifications = AsyncMock(return_value=[_notification("a")])
    config = UnraidConfig(notifications_enabled=True)
    attempts = []

    async def flaky(title, message, alert_type):
        attempts.append(title)
        if len(attempts) == 1:
            raise RuntimeError("telegram down")

    monitor = UnraidNotificationMonitor(client, config, flaky, None, str(tmp_path / "s.json"))
    monitor._primed = True

    assert await monitor.check_once() == 0
    assert await monitor.check_once() == 1
    assert len(attempts) == 2


@pytest.mark.asyncio
async def test_muted_server_suppresses_delivery_but_still_dedups():
    monitor, sent, _ = _monitor([_notification("a")], muted=True)

    assert await monitor.check_once() == 1
    assert sent == []


@pytest.mark.asyncio
async def test_burst_is_capped_and_the_overflow_is_announced():
    many = [_notification(f"n{i}") for i in range(15)]
    monitor, sent, _ = _monitor(many)

    await monitor.check_once()

    assert len(sent) == 11  # 10 relayed + 1 overflow notice
    assert "More Unraid Notifications" in sent[-1][0]
    assert "5 further" in sent[-1][1]


@pytest.mark.asyncio
async def test_oldest_is_sent_first():
    """The API returns newest-first; a burst should read chronologically."""
    monitor, sent, _ = _monitor([
        _notification("newest", title="Third"),
        _notification("middle", title="Second"),
        _notification("oldest", title="First"),
    ])

    await monitor.check_once()

    assert [t for t, _ in sent] == ["⚠️ First", "⚠️ Second", "⚠️ Third"]


@pytest.mark.asyncio
async def test_fetch_failure_is_logged_not_raised():
    client = MagicMock()
    client.get_notifications = AsyncMock(side_effect=RuntimeError("unreachable"))
    monitor = UnraidNotificationMonitor(client, UnraidConfig(), AsyncMock(), None, None)
    monitor._primed = True

    assert await monitor.check_once() == 0


@pytest.mark.asyncio
async def test_dedup_history_is_bounded(tmp_path):
    from src.constants import NOTIFICATION_DEDUP_HISTORY

    state = str(tmp_path / "seen.json")
    monitor, _, _ = _monitor([], state_path=state)
    monitor._remember([f"id{i}" for i in range(NOTIFICATION_DEDUP_HISTORY + 50)])

    assert len(monitor._seen) == NOTIFICATION_DEDUP_HISTORY
    assert "id0" not in monitor._seen_set
    assert f"id{NOTIFICATION_DEDUP_HISTORY + 49}" in monitor._seen_set


@pytest.mark.asyncio
async def test_corrupt_state_file_does_not_crash(tmp_path):
    state = tmp_path / "seen.json"
    state.write_text("{ not json")

    monitor, sent, _ = _monitor([_notification("a")], state_path=str(state))

    assert await monitor.check_once() == 1
    assert json.loads(state.read_text()) == ["a"]


def test_importance_ranking_orders_correctly():
    assert _importance_rank("INFO") < _importance_rank("WARNING") < _importance_rank("ALERT")
    assert _importance_rank("alert") == _importance_rank("ALERT")


def test_message_omits_a_description_identical_to_the_subject():
    body = UnraidNotificationMonitor._format_message(
        {"subject": "same", "description": "same", "formattedTimestamp": "now"}
    )

    assert body.count("same") == 1


def test_message_survives_an_empty_notification():
    body = UnraidNotificationMonitor._format_message({})

    assert body == "(no detail provided)"
