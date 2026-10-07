"""Status collector: one read of monitor state, two renderings.

/health keeps its verbose output byte for byte (the golden cases below were
captured from v0.22.0's build_status_lines before the collector existed); the
startup message gets the collapsed layout.
"""

from types import SimpleNamespace as NS

from src.bot.health_command import build_status_lines


def _docker(running=True, n=2):
    return NS(is_running=running, state_manager=NS(get_all=lambda: list(range(n))))


def _ups(running=True, available=False, name=None, last_error=None, polled=None, never=False):
    return NS(
        is_running=running, is_available=available, ups_name=name, last_error=last_error,
        has_polled=(available or last_error is not None) if polled is None else polled,
        never_reached=never, target="nas:3493",
    )


def _scenarios():
    heal_on = NS(enabled=True, containers=["plex", "sonarr"])
    heal_off = NS(enabled=False, containers=[])
    return {
        "nothing": {},
        "all_healthy": dict(
            monitor=_docker(True, 63),
            log_watcher=NS(is_running=True, containers=["a", "b", "c"], total_drops=0),
            resource_monitor=NS(is_running=True),
            memory_monitor=NS(is_running=True),
            unraid_client=NS(is_connected=True),
            unraid_system_monitor=NS(is_running=True),
            unraid_array_monitor=NS(is_running=True),
            unraid_notification_monitor=NS(is_running=True),
            image_update_monitor=NS(is_running=True),
            auto_heal_config=heal_on,
            ups_monitor=_ups(available=True, name="apc_ups"),
        ),
        "all_broken": dict(
            monitor=_docker(False, 5),
            log_watcher=NS(is_running=False, containers=["a"], total_drops=7),
            resource_monitor=NS(is_running=False),
            memory_monitor=NS(is_running=False),
            unraid_client=NS(is_connected=False),
            unraid_system_monitor=NS(is_running=False),
            unraid_array_monitor=NS(is_running=True),
            image_update_monitor=NS(is_running=False),
            auto_heal_config=heal_off,
            ups_monitor=_ups(available=False, last_error="Connection refused"),
        ),
        "ups_stopped": dict(ups_monitor=_ups(running=False)),
        "ups_no_reading": dict(ups_monitor=_ups(), auto_heal_config=heal_on),
    }


GOLDEN = {
    'nothing': [
        '*Monitors:*',
        '  Docker Events: ⚪ Not configured',
        '  Log Watcher: ⚪ Not configured',
        '  Resources: ⚪ Disabled',
        '  Memory: ⚪ Disabled',
        '  Image updates: ⚪ Disabled',
        '  Auto-heal: ⚪ Disabled',
        '  UPS: ⚪ Disabled',
        '  Unraid: ⚪ Not configured',
        '',
        '💡 Turn these on in /manage → ⚙️ Features',
    ],
    'all_healthy': [
        '*Monitors:*',
        '  Docker Events: ✅ Running (63 containers)',
        '  Log Watcher: ✅ Running (3 containers)',
        '  Resources: ✅ Running',
        '  Memory: ✅ Running',
        '  Image updates: ✅ Running',
        '  Auto-heal: ✅ 2 container(s)',
        '  UPS: ✅ apc_ups',
        '  Unraid: ✅ Connected',
        '    System: ✅',
        '    Array: ✅',
        '    Notifications: ✅',
    ],
    'all_broken': [
        '*Monitors:*',
        '  Docker Events: 🔴 Stopped (5 containers)',
        '  Log Watcher: 🔴 Stopped (1 containers, 7 dropped)',
        '  Resources: 🔴 Stopped',
        '  Memory: 🔴 Stopped',
        '  Image updates: 🔴 Stopped',
        '  Auto-heal: ⚪ Disabled',
        '  UPS: ⚠️ Unavailable (Connection refused)',
        '  Unraid: 🔴 Disconnected',
        '    System: 🔴',
        '    Array: ✅',
        '',
        '💡 Turn these on in /manage → ⚙️ Features',
    ],
    'ups_stopped': [
        '*Monitors:*',
        '  Docker Events: ⚪ Not configured',
        '  Log Watcher: ⚪ Not configured',
        '  Resources: ⚪ Disabled',
        '  Memory: ⚪ Disabled',
        '  Image updates: ⚪ Disabled',
        '  Auto-heal: ⚪ Disabled',
        '  UPS: 🔴 Stopped',
        '  Unraid: ⚪ Not configured',
        '',
        '💡 Turn these on in /manage → ⚙️ Features',
    ],
    'ups_no_reading': [
        '*Monitors:*',
        '  Docker Events: ⚪ Not configured',
        '  Log Watcher: ⚪ Not configured',
        '  Resources: ⚪ Disabled',
        '  Memory: ⚪ Disabled',
        '  Image updates: ⚪ Disabled',
        '  Auto-heal: ✅ 2 container(s)',
        '  UPS: ⚠️ Unavailable (no reading yet)',
        '  Unraid: ⚪ Not configured',
        '',
        '💡 Turn these on in /manage → ⚙️ Features',
    ],
}


def test_health_output_unchanged():
    for name, kwargs in _scenarios().items():
        assert build_status_lines(**kwargs) == GOLDEN[name], name
