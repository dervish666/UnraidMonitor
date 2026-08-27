"""Tests for NUT configuration and the /ups rendering."""

import pytest

from src.bot.ups_command import describe_status, format_ups
from src.config import NutConfig
from src.constants import NUT_DEFAULT_PORT


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def test_defaults_are_on_with_no_host():
    config = NutConfig.from_dict({})
    assert config.enabled is True
    assert config.host == ""
    assert config.port == NUT_DEFAULT_PORT


def test_explicit_host_wins_over_the_unraid_fallback():
    config = NutConfig.from_dict({"host": "10.0.0.9"})
    assert config.resolve_host("192.168.1.100") == "10.0.0.9"


def test_falls_back_to_the_unraid_host():
    # The Unraid NUT plugin runs upsd on the Unraid box, and the bot is usually
    # in a container that cannot see it as localhost.
    config = NutConfig.from_dict({})
    assert config.resolve_host("192.168.1.100") == "192.168.1.100"


def test_no_host_anywhere_resolves_to_nothing():
    assert NutConfig.from_dict({}).resolve_host("") == ""


def test_whitespace_only_host_is_not_a_host():
    assert NutConfig.from_dict({"host": "   "}).resolve_host("") == ""


def test_disabled_is_respected():
    assert NutConfig.from_dict({"enabled": False}).enabled is False


@pytest.mark.parametrize("given,expected", [(0, 1), (70000, 65535), (3493, 3493)])
def test_port_is_clamped(given, expected):
    assert NutConfig.from_dict({"port": given}).port == expected


def test_poll_interval_has_a_floor():
    assert NutConfig.from_dict({"poll_seconds": 1}).poll_seconds == 10


def test_thresholds_are_clamped():
    config = NutConfig.from_dict({"thresholds": {"battery_charge": 500, "load": -3}})
    assert config.battery_charge_threshold == 100
    assert config.load_threshold == 1


# ---------------------------------------------------------------------------
# Status descriptions
# ---------------------------------------------------------------------------


def test_describe_status_expands_known_flags_and_keeps_the_raw():
    described = describe_status("OL CHRG")
    assert "On line, mains present" in described
    assert "(OL CHRG)" in described


def test_describe_status_passes_unknown_flags_through():
    assert describe_status("WEIRD") == "WEIRD"


def test_describe_status_of_nothing():
    assert describe_status(None) == "unknown"
    assert describe_status("  ") == "unknown"


# ---------------------------------------------------------------------------
# /ups rendering
# ---------------------------------------------------------------------------


def healthy_snapshot(**overrides):
    snapshot = {
        "available": True,
        "target": "192.168.1.100:3493",
        "ups": "myups",
        "variables": {
            "ups.status": "OL",
            "ups.mfr": "APC",
            "ups.model": "Back-UPS 1500",
            "battery.charge": "100",
            "battery.runtime": "4320",
            "ups.load": "34",
            "ups.realpower.nominal": "900",
            "input.voltage": "241.0",
        },
        "error": None,
        "age_seconds": 8,
        "on_battery_since": None,
    }
    snapshot.update(overrides)
    return snapshot


def test_healthy_render_shows_the_model_battery_and_load():
    text = format_ups(healthy_snapshot())
    assert "APC Back-UPS 1500" in text
    assert "100%" in text
    assert "1h 12m left" in text
    assert "306W of 900W" in text


def test_unavailable_render_says_unavailable_and_names_the_error():
    text = format_ups({
        "available": False,
        "target": "192.168.1.100:3493",
        "ups": None,
        "variables": {},
        "error": "cannot reach 192.168.1.100:3493: Connection refused",
        "age_seconds": None,
        "on_battery_since": None,
    })
    assert "Unavailable" in text
    assert "Connection refused" in text
    assert "not the same as healthy" in text


def test_unavailable_render_never_shows_a_healthy_tick():
    text = format_ups({
        "available": False, "target": "h:3493", "ups": None, "variables": {},
        "error": "gone", "age_seconds": None, "on_battery_since": None,
    })
    assert "✅" not in text


def test_on_battery_render_is_flagged_as_a_warning():
    text = format_ups(healthy_snapshot(variables={
        "ups.status": "OB DISCHRG", "battery.charge": "62", "battery.runtime": "600",
    }))
    assert "⚠️" in text
    assert "On battery, mains lost" in text


def test_low_battery_render_is_flagged_red():
    text = format_ups(healthy_snapshot(variables={
        "ups.status": "OB LB DISCHRG", "battery.charge": "9", "battery.runtime": "60",
    }))
    assert "\U0001f534" in text


def test_detailed_render_lists_every_variable():
    text = format_ups(healthy_snapshot(), detailed=True)
    assert "`ups.realpower.nominal`" in text
    assert "`input.voltage`" in text


def test_render_falls_back_to_the_ups_name_when_the_model_is_missing():
    text = format_ups(healthy_snapshot(variables={"ups.status": "OL"}))
    assert "myups" in text
