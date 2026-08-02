"""Tests for parity-operation awareness in the array monitor.

A parity sync makes its target disk report DISK_INVALID. Before this, the bot
called that a "Parity Disk Problem" -- confirmed live on a real server mid-sync.
The suppression must be narrow: only the statuses a rebuild legitimately
produces, only while an operation is actually running.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config import UnraidConfig
from src.unraid.monitors.array_monitor import ArrayMonitor


def _snapshot(parity_status="DISK_OK", parity_check=None, disk_status="DISK_OK"):
    return {
        "state": "STARTED",
        "capacity": {"kilobytes": {"free": 5000, "used": 5000, "total": 10000}},
        "parities": [{"name": "parity", "temp": 37, "status": parity_status}],
        "disks": [{"name": "disk1", "temp": 35, "status": disk_status}],
        "caches": [],
        "parityCheckStatus": parity_check or {},
    }


def _monitor(snapshot):
    client = MagicMock()
    client.get_array_status = AsyncMock(return_value=snapshot)
    mute = MagicMock()
    mute.is_array_muted.return_value = False
    sent: list[tuple[str, str]] = []

    async def on_alert(title, message, alert_type):
        sent.append((title, message))

    return ArrayMonitor(client, UnraidConfig(), on_alert, mute), sent


def _titles(sent):
    return [t for t, _ in sent]


@pytest.mark.asyncio
async def test_running_sync_reports_progress_not_a_problem():
    """The confirmed live false alarm."""
    monitor, sent = _monitor(
        _snapshot("DISK_INVALID", {"status": "RUNNING", "progress": 45, "speed": "39"})
    )

    await monitor.check_once()

    assert "🔄 Parity Operation Running" in _titles(sent)
    assert not any("Problem" in t for t in _titles(sent))
    assert any("45%" in m for _, m in sent)


@pytest.mark.asyncio
async def test_running_sync_alert_does_not_repeat():
    monitor, sent = _monitor(
        _snapshot("DISK_INVALID", {"status": "RUNNING", "progress": 45})
    )

    await monitor.check_once()
    sent.clear()
    await monitor.check_once()

    assert sent == []


@pytest.mark.asyncio
async def test_paused_sync_still_suppresses_and_says_so():
    monitor, sent = _monitor(
        _snapshot("DISK_INVALID", {"status": "PAUSED", "progress": 12})
    )

    await monitor.check_once()

    assert not any("Problem" in t for t in _titles(sent))
    assert any("PAUSED" in m for _, m in sent)


@pytest.mark.asyncio
async def test_completion_is_announced():
    monitor, sent = _monitor(
        _snapshot("DISK_INVALID", {"status": "RUNNING", "progress": 99})
    )
    await monitor.check_once()

    monitor._client.get_array_status = AsyncMock(
        return_value=_snapshot("DISK_OK", {"status": "COMPLETED", "progress": 100, "errors": 0})
    )
    sent.clear()
    await monitor.check_once()

    assert "✅ Parity Operation Complete" in _titles(sent)
    assert any("Errors: 0" in m for _, m in sent)


@pytest.mark.asyncio
async def test_failed_operation_is_announced_as_failed():
    monitor, sent = _monitor(_snapshot("DISK_INVALID", {"status": "RUNNING"}))
    await monitor.check_once()

    monitor._client.get_array_status = AsyncMock(
        return_value=_snapshot("DISK_OK", {"status": "FAILED"})
    )
    sent.clear()
    await monitor.check_once()

    assert "🔴 Parity Operation Failed" in _titles(sent)


@pytest.mark.asyncio
async def test_null_error_count_is_not_reported_as_zero():
    """A live server returned errors=null mid-sync -- inventing 0 would be a lie."""
    monitor, sent = _monitor(_snapshot("DISK_INVALID", {"status": "RUNNING"}))
    await monitor.check_once()

    monitor._client.get_array_status = AsyncMock(
        return_value=_snapshot("DISK_OK", {"status": "COMPLETED", "errors": None})
    )
    sent.clear()
    await monitor.check_once()

    body = "".join(m for _, m in sent)
    assert "not reported" in body
    assert "Errors: 0" not in body


@pytest.mark.asyncio
async def test_disabled_disk_during_a_sync_still_alerts():
    """The safety property: suppression must not swallow a real failure."""
    monitor, sent = _monitor(
        _snapshot("DISK_INVALID", {"status": "RUNNING", "progress": 45}, disk_status="DISK_DSBL")
    )

    await monitor.check_once()

    assert "💾 Data Disk Problem" in _titles(sent)
    assert any("DISK_DSBL" in m for _, m in sent)


@pytest.mark.asyncio
async def test_invalid_disk_with_no_operation_running_still_alerts():
    """DISK_INVALID is only excused while something is actually rebuilding."""
    monitor, sent = _monitor(_snapshot("DISK_INVALID", {"status": "COMPLETED"}))

    await monitor.check_once()

    assert "💾 Parity Disk Problem" in _titles(sent)


@pytest.mark.asyncio
async def test_missing_parity_status_behaves_like_before():
    """Servers that don't return parityCheckStatus must not lose disk alerting."""
    snapshot = _snapshot("DISK_INVALID")
    del snapshot["parityCheckStatus"]
    monitor, sent = _monitor(snapshot)

    await monitor.check_once()

    assert "💾 Parity Disk Problem" in _titles(sent)


@pytest.mark.asyncio
async def test_fault_appearing_after_the_sync_is_not_suppressed_by_stale_state():
    """A disk excused during the sync must alert if it is still bad afterwards."""
    monitor, sent = _monitor(
        _snapshot("DISK_INVALID", {"status": "RUNNING", "progress": 50})
    )
    await monitor.check_once()

    monitor._client.get_array_status = AsyncMock(
        return_value=_snapshot("DISK_DSBL", {"status": "COMPLETED", "errors": 3})
    )
    sent.clear()
    await monitor.check_once()

    assert "💾 Parity Disk Problem" in _titles(sent)
