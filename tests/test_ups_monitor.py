"""Tests for the UPS monitor: alert edges, and the unavailable-vs-healthy split."""


import pytest

from src.config import NutConfig
from src.nut.client import NutAuthError, NutUnavailable
from src.nut.monitor import UpsMonitor, format_duration, format_runtime, parse_status


class FakeClient:
    """Returns queued readings, or raises queued errors, one per fetch."""

    target = "127.0.0.1:3493"

    def __init__(self, readings):
        self._readings = list(readings)
        self._last = readings[-1] if readings else {}
        self.calls = 0

    async def fetch(self, ups_name=None):
        self.calls += 1
        item = self._readings.pop(0) if self._readings else self._last
        self._last = item
        if isinstance(item, Exception):
            raise item
        return "myups", item


class FakeMutes:
    def __init__(self, muted=False):
        self.muted = muted

    def is_ups_muted(self):
        return self.muted


class Recorder:
    def __init__(self):
        self.alerts = []

    async def __call__(self, title, message, alert_type):
        self.alerts.append((title, message, alert_type))

    @property
    def titles(self):
        return [a[0] for a in self.alerts]


def build(readings, muted=False, **config_kwargs):
    config = NutConfig(enabled=True, host="127.0.0.1", **config_kwargs)
    recorder = Recorder()
    monitor = UpsMonitor(
        client=FakeClient(readings),
        config=config,
        on_alert=recorder,
        mute_manager=FakeMutes(muted),
    )
    return monitor, recorder


ONLINE = {"ups.status": "OL", "battery.charge": "100", "battery.runtime": "3600", "ups.load": "20"}
ON_BATTERY = {"ups.status": "OB DISCHRG", "battery.charge": "80", "battery.runtime": "900", "ups.load": "20"}
LOW_BATTERY = {"ups.status": "OB LB DISCHRG", "battery.charge": "12", "battery.runtime": "120", "ups.load": "20"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_parse_status_splits_multiple_flags():
    assert parse_status("OL TRIM CHRG") == {"OL", "TRIM", "CHRG"}


def test_parse_status_of_nothing_is_empty():
    assert parse_status(None) == set()
    assert parse_status("") == set()


@pytest.mark.parametrize("seconds,expected", [
    ("3600", "1h"), ("4320", "1h 12m"), ("900", "15m"), ("45", "45s"),
    (None, "unknown"), ("not-a-number", "unknown"), ("-1", "unknown"),
])
def test_format_runtime(seconds, expected):
    assert format_runtime(seconds) == expected


def test_format_duration_spans():
    assert format_duration(30) == "30s"
    assert format_duration(90) == "1m 30s"
    assert format_duration(3600) == "1h"
    assert format_duration(5400) == "1h 30m"


# ---------------------------------------------------------------------------
# Status edges
# ---------------------------------------------------------------------------


async def test_first_healthy_poll_is_silent():
    monitor, rec = build([ONLINE])
    await monitor.check_once()
    assert rec.alerts == []
    assert monitor.is_available


async def test_going_on_battery_alerts_once():
    monitor, rec = build([ONLINE, ON_BATTERY, ON_BATTERY])
    for _ in range(3):
        await monitor.check_once()
    assert rec.titles.count("UPS On Battery") == 1


async def test_the_alert_carries_the_battery_and_runtime():
    monitor, rec = build([ONLINE, ON_BATTERY])
    await monitor.check_once()
    await monitor.check_once()
    body = rec.alerts[0][1]
    assert "Battery: 80%" in body
    assert "Runtime: 15m" in body


async def test_returning_to_mains_sends_a_recovery():
    monitor, rec = build([ONLINE, ON_BATTERY, ONLINE])
    for _ in range(3):
        await monitor.check_once()
    assert "UPS Back On Mains" in rec.titles


async def test_low_battery_alerts_separately_from_on_battery():
    monitor, rec = build([ONLINE, ON_BATTERY, LOW_BATTERY])
    for _ in range(3):
        await monitor.check_once()
    assert "UPS On Battery" in rec.titles
    assert "UPS Battery Critical" in rec.titles


async def test_replace_battery_does_not_repeat_every_poll():
    reading = dict(ONLINE, **{"ups.status": "OL RB"})
    monitor, rec = build([ONLINE, reading, reading, reading])
    for _ in range(4):
        await monitor.check_once()
    assert rec.titles.count("UPS Battery Needs Replacing") == 1


async def test_calibration_does_not_alert():
    # CAL puts the UPS on battery deliberately, the same reason a parity sync
    # is not reported as a failing disk.
    calibrating = {"ups.status": "OL CAL", "battery.charge": "90", "ups.load": "20"}
    monitor, rec = build([ONLINE, calibrating])
    await monitor.check_once()
    await monitor.check_once()
    assert rec.alerts == []


async def test_overload_alerts():
    over = dict(ONLINE, **{"ups.status": "OL OVER"})
    monitor, rec = build([ONLINE, over])
    await monitor.check_once()
    await monitor.check_once()
    assert "UPS Overloaded" in rec.titles


async def test_bypass_alerts():
    bypass = dict(ONLINE, **{"ups.status": "OL BYPASS"})
    monitor, rec = build([ONLINE, bypass])
    await monitor.check_once()
    await monitor.check_once()
    assert "UPS On Bypass" in rec.titles


# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------


async def test_low_charge_on_mains_does_not_alert():
    # Charging back up after an outage is normal and must stay quiet.
    charging = {"ups.status": "OL CHRG", "battery.charge": "20", "ups.load": "20"}
    monitor, rec = build([charging])
    await monitor.check_once()
    assert rec.alerts == []


async def test_low_charge_on_battery_alerts():
    draining = {"ups.status": "OB DISCHRG", "battery.charge": "20", "battery.runtime": "300", "ups.load": "20"}
    monitor, rec = build([ONLINE, draining])
    await monitor.check_once()
    await monitor.check_once()
    assert "UPS Battery Low" in rec.titles


async def test_high_load_alerts_with_watts_when_nominal_is_known():
    loaded = {"ups.status": "OL", "battery.charge": "100", "ups.load": "95",
              "ups.realpower.nominal": "900"}
    monitor, rec = build([loaded])
    await monitor.check_once()
    assert "UPS Load High" in rec.titles
    body = next(a[1] for a in rec.alerts if a[0] == "UPS Load High")
    assert "855W of 900W" in body


async def test_load_under_the_threshold_is_quiet():
    monitor, rec = build([ONLINE], load_threshold=80)
    await monitor.check_once()
    assert rec.alerts == []


async def test_junk_values_do_not_crash_a_poll():
    junk = {"ups.status": "OL", "battery.charge": "n/a", "ups.load": "", "battery.runtime": "???"}
    monitor, rec = build([junk])
    assert await monitor.check_once() is not None
    assert rec.alerts == []


# ---------------------------------------------------------------------------
# Availability: the part that must never read as "fine"
# ---------------------------------------------------------------------------


async def test_never_connected_never_alerts():
    # Most installs have no NUT server. Turning this on by default must not
    # mean a message for everyone who does not.
    monitor, rec = build([NutUnavailable("refused")] * 5)
    for _ in range(5):
        await monitor.check_once()
    assert rec.alerts == []
    assert not monitor.is_available


async def test_losing_a_working_server_alerts_after_the_grace_period():
    monitor, rec = build([ONLINE, NutUnavailable("gone"), NutUnavailable("gone"), NutUnavailable("gone")])
    await monitor.check_once()
    await monitor.check_once()
    assert rec.alerts == [], "one dropped poll should not alert"
    await monitor.check_once()
    await monitor.check_once()
    assert "UPS Monitoring Unavailable" in rec.titles


async def test_the_unavailable_alert_says_unknown_not_healthy():
    monitor, rec = build([ONLINE] + [NutUnavailable("gone")] * 3)
    for _ in range(4):
        await monitor.check_once()
    body = next(a[1] for a in rec.alerts if a[0] == "UPS Monitoring Unavailable")
    assert "unknown, not healthy" in body


async def test_recovery_after_an_outage_is_announced():
    monitor, rec = build([ONLINE] + [NutUnavailable("gone")] * 3 + [ONLINE])
    for _ in range(5):
        await monitor.check_once()
    assert "UPS Monitoring Restored" in rec.titles


async def test_bad_credentials_stop_the_loop_instead_of_retrying_forever():
    monitor, rec = build([NutAuthError("denied")])
    monitor._running = True
    await monitor.check_once()
    assert "UPS Monitoring Failed" in rec.titles
    assert not monitor.is_running


async def test_snapshot_when_unreachable_reports_unavailable_with_the_error():
    monitor, _ = build([NutUnavailable("connection refused")])
    snapshot = await monitor.get_snapshot()
    assert snapshot["available"] is False
    assert "connection refused" in snapshot["error"]
    assert snapshot["variables"] == {}


async def test_snapshot_when_reachable_carries_the_variables():
    monitor, _ = build([ONLINE])
    snapshot = await monitor.get_snapshot()
    assert snapshot["available"] is True
    assert snapshot["ups"] == "myups"
    assert snapshot["variables"]["ups.status"] == "OL"


# ---------------------------------------------------------------------------
# Muting
# ---------------------------------------------------------------------------


async def test_muted_ups_alerts_are_suppressed():
    monitor, rec = build([ONLINE, ON_BATTERY], muted=True)
    await monitor.check_once()
    await monitor.check_once()
    assert rec.alerts == []


async def test_muting_still_tracks_state_so_unmuting_does_not_replay_history():
    monitor, rec = build([ONLINE, ON_BATTERY, ON_BATTERY], muted=True)
    await monitor.check_once()
    await monitor.check_once()
    monitor._mute_manager.muted = False
    await monitor.check_once()
    assert rec.alerts == []


async def test_clear_alert_state_lets_the_condition_be_reported_again():
    monitor, rec = build([ONLINE, ON_BATTERY, ON_BATTERY])
    await monitor.check_once()
    await monitor.check_once()
    monitor.clear_alert_state()
    await monitor.check_once()
    assert rec.titles.count("UPS On Battery") == 2
