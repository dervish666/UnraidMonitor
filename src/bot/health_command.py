"""Bot health and status command."""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable, TYPE_CHECKING

from aiogram.types import Message

from src import __version__ as _FALLBACK_VERSION
from src.utils.formatting import escape_markdown, safe_reply

if TYPE_CHECKING:
    from src.monitors.docker_events import DockerEventMonitor
    from src.monitors.log_watcher import LogWatcher
    from src.monitors.resource_monitor import ResourceMonitor
    from src.monitors.memory_monitor import MemoryMonitor
    from src.unraid.monitors.system_monitor import UnraidSystemMonitor
    from src.unraid.monitors.array_monitor import ArrayMonitor
    from src.unraid.client import UnraidClientWrapper
    from src.services.llm.registry import ProviderRegistry

logger = logging.getLogger(__name__)

try:
    from importlib.metadata import version as _pkg_version
    BOT_VERSION = _pkg_version("unraid-monitor-bot")
except Exception:
    BOT_VERSION = _FALLBACK_VERSION


def _format_health_uptime(start_time: datetime) -> str:
    """Format bot uptime from start time to now."""
    delta = datetime.now(timezone.utc) - start_time
    days = delta.days
    hours = delta.seconds // 3600
    minutes = (delta.seconds % 3600) // 60

    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0 or not parts:
        parts.append(f"{minutes}m")
    return " ".join(parts)


RUNNING = "running"
OFF = "off"
BROKEN = "broken"
PENDING = "pending"

_FEATURES_FIX = "Turn {it} on in /manage → Features."
_CONFIG_FIX = "Turn {it} on in config.yaml."


@dataclass(frozen=True)
class StatusItem:
    """One monitor's state, read once and rendered two ways.

    ``health`` holds the verbose /health lines. ``detail`` and ``fix`` feed the
    collapsed startup message: ``detail`` says what is wrong (broken), what is
    still happening (pending) or why it is idle (off), and ``fix`` says how to
    turn an off item on, with ``{it}`` standing in for "it" or "them".
    """

    name: str
    state: str
    health: list[str]
    detail: str = ""
    fix: str = ""
    count: int | None = None
    in_summary: bool = True


def _state_line(label: str, running: bool) -> str:
    return f"  {label}: {'✅ Running' if running else '🔴 Stopped'}"


def _join(words: list[str]) -> str:
    """'a', 'a and b', 'a, b and c'."""
    if len(words) <= 1:
        return "".join(words)
    return f"{', '.join(words[:-1])} and {words[-1]}"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def collect_status(
    monitor: "DockerEventMonitor | None" = None,
    log_watcher: "LogWatcher | None" = None,
    resource_monitor: "ResourceMonitor | None" = None,
    memory_monitor: "MemoryMonitor | None" = None,
    unraid_client: Any = None,
    unraid_system_monitor: "UnraidSystemMonitor | None" = None,
    unraid_array_monitor: "ArrayMonitor | None" = None,
    unraid_notification_monitor: Any = None,
    image_update_monitor: Any = None,
    auto_heal_config: Any = None,
    ups_monitor: Any = None,
) -> list[StatusItem]:
    """Read every monitor's state once, in display order."""
    items: list[StatusItem] = []

    if monitor:
        n = len(monitor.state_manager.get_all())
        line = f"{_state_line('Docker Events', monitor.is_running)} ({n} containers)"
        if monitor.is_running:
            items.append(StatusItem("Docker events", RUNNING, [line], count=n))
        else:
            items.append(StatusItem(
                "Docker events", BROKEN, [line],
                detail="stopped. Crash and restart alerts are off until the bot restarts.",
            ))
    else:
        items.append(StatusItem(
            "Docker events", BROKEN, ["  Docker Events: ⚪ Not configured"],
            detail="not connected to Docker. Container alerts are off.",
        ))

    if log_watcher:
        drop_info = f", {log_watcher.total_drops} dropped" if log_watcher.total_drops else ""
        n = len(log_watcher.containers)
        line = f"{_state_line('Log Watcher', log_watcher.is_running)} ({n} containers{drop_info})"
        if log_watcher.is_running:
            items.append(StatusItem("logs", RUNNING, [line], count=n))
        else:
            items.append(StatusItem("logs", BROKEN, [line], detail="stopped. Log error alerts are off."))
    else:
        items.append(StatusItem(
            "logs", OFF, ["  Log Watcher: ⚪ Not configured"], fix=_CONFIG_FIX,
        ))

    for name, label, mon, broken_detail, fix in (
        ("resources", "Resources", resource_monitor,
         "stopped. Per-container CPU and memory alerts are off.", _CONFIG_FIX),
        ("memory", "Memory", memory_monitor,
         "stopped. Memory pressure handling is off.", _CONFIG_FIX),
        ("image updates", "Image updates", image_update_monitor,
         "stopped.", _FEATURES_FIX),
    ):
        if mon:
            line = _state_line(label, mon.is_running)
            if mon.is_running:
                items.append(StatusItem(name, RUNNING, [line]))
            else:
                items.append(StatusItem(name, BROKEN, [line], detail=broken_detail))
        else:
            items.append(StatusItem(name, OFF, [f"  {label}: ⚪ Disabled"], fix=fix))

    if auto_heal_config is not None and auto_heal_config.enabled and auto_heal_config.containers:
        n = len(auto_heal_config.containers)
        items.append(StatusItem(
            "auto-heal", RUNNING, [f"  Auto-heal: ✅ {n} container(s)"], count=n, in_summary=False,
        ))
    else:
        items.append(StatusItem(
            "auto-heal", OFF, ["  Auto-heal: ⚪ Disabled"], fix=_FEATURES_FIX, in_summary=False,
        ))

    items.append(_ups_status(ups_monitor))
    items.append(_unraid_status(
        unraid_client, unraid_system_monitor, unraid_array_monitor, unraid_notification_monitor,
    ))
    return items


def _ups_status(ups_monitor: Any) -> StatusItem:
    if ups_monitor is None:
        return StatusItem("UPS", OFF, ["  UPS: ⚪ Disabled"], fix=_FEATURES_FIX)
    target = escape_markdown(ups_monitor.target)
    if not ups_monitor.is_running:
        detail = "stopped."
        if ups_monitor.last_error:
            # The poll loop only stops itself on an auth failure.
            detail = (
                f"the NUT server at {target} rejected the credentials. "
                "Check NUT\\_USERNAME and NUT\\_PASSWORD."
            )
        return StatusItem("UPS", BROKEN, ["  UPS: 🔴 Stopped"], detail=detail)
    if ups_monitor.is_available:
        return StatusItem("UPS", RUNNING, [f"  UPS: ✅ {ups_monitor.ups_name or 'connected'}"])

    # Never "OK": a monitor that cannot reach upsd knows nothing.
    reason = ups_monitor.last_error or "no reading yet"
    health = [f"  UPS: ⚠️ Unavailable ({reason})"]
    if not ups_monitor.has_polled:
        # The poll loop is a task started just before the startup message; its
        # first answer may not be in yet. Not asked is not the same as down.
        return StatusItem("UPS", PENDING, health, detail="connecting")
    if ups_monitor.never_reached:
        return StatusItem(
            "UPS", OFF, health,
            detail=f"no NUT server answered at {target}, so it is idle.",
            fix="Set nut.host, or turn {it} off in /manage → Features.",
        )
    return StatusItem(
        "UPS", BROKEN, health,
        detail=f"lost contact with the NUT server at {target}. UPS status is unknown, not healthy.",
    )


def _unraid_status(client: Any, system: Any, array: Any, notifications: Any) -> StatusItem:
    if not client:
        return StatusItem(
            "Unraid", OFF, ["  Unraid: ⚪ Not configured"],
            fix="Set UNRAID\\_API\\_KEY to turn {it} on.",
        )
    health = [f"  Unraid: {'✅ Connected' if client.is_connected else '🔴 Disconnected'}"]
    subs = [(label, mon) for label, mon in (
        ("System", system), ("Array", array), ("Notifications", notifications),
    ) if mon]
    health.extend(f"    {label}: {'✅' if mon.is_running else '🔴'}" for label, mon in subs)

    if not client.is_connected:
        nouns = {"System": "system", "Array": "array", "Notifications": "notification"}
        waiting = [nouns[label] for label, _ in subs]
        what = f"{_join(waiting)} alerts".capitalize() if waiting else "Unraid alerts"
        return StatusItem(
            "Unraid", BROKEN, health,
            detail=f"can't reach the server. {what} wait until it reconnects.",
        )
    stopped = [label.lower() for label, mon in subs if not mon.is_running]
    if stopped:
        return StatusItem(
            "Unraid", BROKEN, health,
            detail=f"connected, but the {_join(stopped)} monitor{'s' if len(stopped) > 1 else ''} stopped.",
        )
    return StatusItem("Unraid", RUNNING, health)


def build_status_lines(
    monitor: "DockerEventMonitor | None" = None,
    log_watcher: "LogWatcher | None" = None,
    resource_monitor: "ResourceMonitor | None" = None,
    memory_monitor: "MemoryMonitor | None" = None,
    unraid_client: Any = None,
    unraid_system_monitor: "UnraidSystemMonitor | None" = None,
    unraid_array_monitor: "ArrayMonitor | None" = None,
    unraid_notification_monitor: Any = None,
    image_update_monitor: Any = None,
    auto_heal_config: Any = None,
    ups_monitor: Any = None,
) -> list[str]:
    """Verbose per-monitor lines for /health."""
    items = collect_status(
        monitor=monitor, log_watcher=log_watcher, resource_monitor=resource_monitor,
        memory_monitor=memory_monitor, unraid_client=unraid_client,
        unraid_system_monitor=unraid_system_monitor, unraid_array_monitor=unraid_array_monitor,
        unraid_notification_monitor=unraid_notification_monitor,
        image_update_monitor=image_update_monitor, auto_heal_config=auto_heal_config,
        ups_monitor=ups_monitor,
    )
    lines: list[str] = ["*Monitors:*"]
    for item in items:
        lines.extend(item.health)
    if any(i.state == OFF and i.name in ("image updates", "auto-heal") for i in items):
        lines.append("")
        lines.append("💡 Turn these on in /manage → ⚙️ Features")
    return lines


# ---------------------------------------------------------------------------
# Startup message (collapsed): problems first, then one summary line
# ---------------------------------------------------------------------------

_FEATURE_LABELS = {"nl_processor": "chat", "diagnostic": "diagnose", "pattern_analyzer": "analyze"}


def ai_summary(registry: "ProviderRegistry | None") -> tuple[list[str], list[str]]:
    """Return ``(ai_lines, problems)`` for the startup message.

    No configured provider is a choice, not a fault, so it is one plain line
    and never a problem.
    """
    if registry is None or not registry.get_available_providers():
        return ["AI: off (no API key set)"], []
    default = registry.resolved_model()
    if default is None:
        return ["AI: off (no model available)"], []

    def name(model_id: str) -> str:
        return escape_markdown(registry.display_name(model_id))

    used = {default[0]} | {
        route[0] for f in _FEATURE_LABELS if (route := registry.resolved_model(f))
    }
    problems = [
        f"🔴 AI: {msg}. Chat and /diagnose are off until it's fixed."
        for provider, msg in registry.provider_problems.items()
        if provider in used
    ]
    if default[0] in registry.provider_problems:
        return [], problems

    lines = [f"AI: {name(default[1])}"]
    by_model: dict[tuple[str, str], list[str]] = {}
    for feature, label in _FEATURE_LABELS.items():
        route = registry.resolved_model(feature)
        if route is not None and route != default:
            by_model.setdefault(route, []).append(label)
    for (_, model_id), labels in by_model.items():
        verb = "uses" if len(labels) == 1 else "use"
        lines.append(f"{_join(labels).capitalize()} {verb} {name(model_id)}.")
    return lines, problems


def build_startup_lines(
    items: list[StatusItem],
    *,
    version: str,
    ai_lines: list[str],
    ai_problems: list[str],
) -> list[str]:
    """Collapsed startup layout: header, problems, summary, watching, AI."""
    problems: list[str] = []
    count = 0

    for item in items:
        if item.in_summary and item.state == BROKEN:
            problems.append(f"🔴 {item.name[0].upper()}{item.name[1:]}: {item.detail}")
            count += 1
    problems.extend(ai_problems)
    count += len(ai_problems)

    grouped: dict[str, list[str]] = {}
    for item in items:
        if not (item.in_summary and item.state == OFF):
            continue
        count += 1
        if item.detail:
            fix = f" {item.fix.format(it='it')}" if item.fix else ""
            problems.append(f"⚪ {item.name[0].upper()}{item.name[1:]}: {item.detail}{fix}")
        else:
            grouped.setdefault(item.fix, []).append(item.name)
    for fix, names in grouped.items():
        problems.append(f"⚪ Off: {_join(names)}. {fix.format(it='it' if len(names) == 1 else 'them')}")

    running = [
        f"{i.name} ({i.detail})" if i.state == PENDING else i.name
        for i in items if i.in_summary and i.state in (RUNNING, PENDING)
    ]
    if count == 0:
        summary = f"Everything's running: {_join(running)}."
    elif running:
        # A plain list here, a sentence above: matches the approved layout.
        summary = f"Running: {', '.join(running)}."
    else:
        summary = "Nothing is running."

    by_name = {i.name: i for i in items}
    watching: list[str] = []
    docker = by_name.get("Docker events")
    if docker and docker.state == RUNNING and docker.count is not None:
        watching.append(f"Watching {_plural(docker.count, 'container')}")
        for key, suffix in (("logs", "for log errors"), ("auto-heal", "with auto-heal")):
            it = by_name.get(key)
            if it and it.state == RUNNING and it.count:
                watching.append(f"{it.count} {suffix}")

    docker_down = docker is not None and docker.state == BROKEN
    emoji = "🔴" if docker_down else "🟡" if count else "🟢"
    header = f"{emoji} *Unraid Monitor is up* · v{version}"
    if count:
        header += f" · {_plural(count, 'thing')} need{'s' if count == 1 else ''} a look"

    lines = [header, ""]
    if problems:
        lines.extend(problems)
        lines.append("")
    lines.append(summary)
    if watching:
        lines.append(", ".join(watching) + ".")
    if ai_lines:
        lines.append("")
        lines.extend(ai_lines)
    return lines


def health_command(
    start_time: datetime,
    monitor: "DockerEventMonitor | None" = None,
    log_watcher: "LogWatcher | None" = None,
    resource_monitor: "ResourceMonitor | None" = None,
    memory_monitor: "MemoryMonitor | None" = None,
    unraid_client: "UnraidClientWrapper | None" = None,
    unraid_system_monitor: "UnraidSystemMonitor | None" = None,
    unraid_array_monitor: "ArrayMonitor | None" = None,
    unraid_notification_monitor: Any = None,
    alert_manager: object | None = None,
    image_update_monitor: Any = None,
    auto_heal_config: Any = None,
    ups_monitor: Any = None,
) -> Callable[[Message], Awaitable[None]]:
    """Factory for /health command handler."""

    async def handler(message: Message) -> None:
        uptime = _format_health_uptime(start_time)

        lines = [
            "🏥 *Bot Health*",
            "",
            f"*Version:* {BOT_VERSION}",
            f"*Uptime:* {uptime}",
            "",
        ]

        lines.extend(build_status_lines(
            monitor=monitor,
            log_watcher=log_watcher,
            resource_monitor=resource_monitor,
            memory_monitor=memory_monitor,
            unraid_client=unraid_client,
            unraid_system_monitor=unraid_system_monitor,
            unraid_array_monitor=unraid_array_monitor,
            unraid_notification_monitor=unraid_notification_monitor,
            image_update_monitor=image_update_monitor,
            auto_heal_config=auto_heal_config,
            ups_monitor=ups_monitor,
        ))

        # Alert queue depth
        if alert_manager and hasattr(alert_manager, "queued_count"):
            queued = alert_manager.queued_count
            if queued > 0:
                lines.append(f"  Alert Queue: {queued} pending")

        # Crash tracker stats
        if monitor:
            active_loops = monitor.crash_tracker.get_active_crash_loops()
            if active_loops:
                lines.append("")
                lines.append("*Recent Crashes:*")
                for name, count in active_loops:
                    lines.append(f"  ⚠️ {name} ({count}x)")

        await safe_reply(message, "\n".join(lines))

    return handler
