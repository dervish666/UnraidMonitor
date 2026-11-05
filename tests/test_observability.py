"""/health says what each monitor last saw, and setup mistakes read as mistakes."""

import time
from unittest.mock import AsyncMock, MagicMock

from aiohttp import web

from src.alerts.ignore_manager import IgnoreManager, ignore_key
from src.bot.health_command import build_status_lines, collect_status
from src.bot.manage_command import _build_ignore_detail_keyboard, manage_delete_ignore_callback
from src.bot.telegram_bot import unknown_command
from src.config import NutConfig
from src.nut.client import NutClient
from src.nut.monitor import UpsMonitor
from src.unraid.client import UnraidClientWrapper, UnraidConnectionError
from src.unraid.monitors.array_monitor import ArrayMonitor
from src.utils.poll_health import PollHealth, describe_poll
from tests.test_nut_client import FakeUpsd


class _Mutes:
    def is_ups_muted(self):
        return False

    def is_array_muted(self):
        return False


async def _ups_against(upsd: FakeUpsd, ups_name: str) -> UpsMonitor:
    sent = AsyncMock(return_value=True)
    return UpsMonitor(
        client=NutClient("127.0.0.1", port=upsd.port, timeout=2),
        config=NutConfig(enabled=True, host="127.0.0.1", ups_name=ups_name),
        on_alert=sent, mute_manager=_Mutes(),
    )


# --- NUT: a wrong UPS name is a setup mistake, not "no NUT server" ----------


async def test_unknown_ups_is_misconfiguration_in_health():
    upsd = await FakeUpsd().start()
    try:
        monitor = await _ups_against(upsd, "wrongname")
        monitor._running = True
        await monitor.check_once()
        await monitor.check_once()
    finally:
        await upsd.stop()

    assert not monitor.never_reached
    assert "UNKNOWN-UPS" in (monitor.misconfigured or "")
    [ups] = [i for i in collect_status(ups_monitor=monitor) if i.name == "UPS"]
    assert ups.state == "broken" and ups.in_summary
    assert "does not serve the configured UPS" in ups.detail


async def test_fixing_the_name_clears_misconfiguration():
    upsd = await FakeUpsd().start()
    try:
        monitor = await _ups_against(upsd, "wrongname")
        await monitor.check_once()
        monitor._config = NutConfig(enabled=True, host="127.0.0.1", ups_name="myups")
        await monitor.check_once()
    finally:
        await upsd.stop()
    assert monitor.misconfigured is None and monitor.is_available
    assert "last poll" in monitor.describe_poll()


async def test_no_server_stays_dormant():
    monitor = UpsMonitor(
        client=NutClient("127.0.0.1", port=1, timeout=0.5),
        config=NutConfig(enabled=True, host="127.0.0.1"),
        on_alert=AsyncMock(return_value=True), mute_manager=_Mutes(),
    )
    await monitor.check_once()
    assert monitor.never_reached and monitor.misconfigured is None


# --- Poll health wording ----------------------------------------------------


def test_describe_poll_sequence():
    health = PollHealth()
    assert health.describe() == "no poll yet"
    health.ok()
    now = time.time()
    assert health.describe(now=now + 120) == "last poll 2m ago"
    health.failed(OSError("refused"))
    health.failed(OSError("refused again"))
    assert health.describe(now=now + 7200) == "2 failed polls, last ok 2h ago: refused again"
    health.ok()
    assert health.consecutive_failures == 0 and health.last_error is None
    assert describe_poll(None, 1, "boom") == "1 failed poll, no poll yet: boom"


def test_health_line_shows_resource_monitor_failures():
    mon = MagicMock(is_running=True)
    mon.poll_health = PollHealth()
    mon.poll_health.failed(RuntimeError("socket gone"))
    lines = build_status_lines(resource_monitor=mon)
    assert any("Resources: ✅ Running (1 failed poll" in line and "socket gone" in line for line in lines)


# --- Unraid: a failing API is not "Connected" -------------------------------


async def _graphql_server(answers: list[web.Response]):
    async def handle(request: web.Request) -> web.Response:
        body = await request.json()
        if "hostname" in body["query"]:
            return web.json_response({"data": {"info": {"os": {"hostname": "Tower"}}}})
        return answers.pop(0)

    app = web.Application()
    app.router.add_post("/graphql", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    return runner, port


async def test_graphql_errors_clear_connection_health_until_a_query_succeeds():
    runner, port = await _graphql_server([
        web.json_response({"errors": [{"message": "Forbidden resource"}]}),
        web.json_response({"data": {"array": {"state": "STARTED"}}}),
    ])
    client = UnraidClientWrapper("127.0.0.1", "k" * 16, port=port)
    try:
        await client.connect()
        assert client.is_connected
        try:
            await client.get_array_status()
        except UnraidConnectionError:
            pass
        assert not client.is_connected
        assert "Forbidden resource" in (client.last_error or "")
        await client.get_array_status()
        assert client.is_connected and client.last_error is None
    finally:
        await client.disconnect()
        await runner.cleanup()


async def test_connect_rejects_a_graphql_error_answer():
    async def handle(request: web.Request) -> web.Response:
        return web.json_response({"errors": [{"message": "API key invalid"}]})

    app = web.Application()
    app.router.add_post("/graphql", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    client = UnraidClientWrapper("127.0.0.1", "k" * 16, port=port)
    try:
        await client.connect()
        assert not client.is_connected
        assert "API key invalid" in (client.last_error or "")
    finally:
        await client.disconnect()
        await runner.cleanup()


# --- Parity: a read-only check is not a rebuild -----------------------------


def _array(parity: dict, disk_status: str = "DISK_OK") -> dict:
    return {
        "state": "STARTED",
        "disks": [{"name": "disk1", "temp": 30, "status": disk_status}],
        "parities": [], "caches": [],
        "capacity": {"kilobytes": {"used": "1", "total": "10", "free": "9"}},
        "parityCheckStatus": parity,
    }


async def _array_monitor(statuses: list[dict]):
    alerts: list[dict] = []

    async def capture(**kwargs):
        alerts.append(kwargs)
        return True

    client = MagicMock(get_array_status=AsyncMock(side_effect=statuses))
    config = MagicMock(disk_temp_threshold=50, array_usage_threshold=85, poll_array_seconds=300)
    return ArrayMonitor(client, config, capture, _Mutes()), alerts


async def test_read_only_check_is_named_and_excuses_nothing():
    monitor, alerts = await _array_monitor([
        _array({"status": "RUNNING", "correcting": False, "progress": 10}, "DISK_INVALID"),
        _array({"status": "COMPLETED", "correcting": False, "errors": 0}),
    ])
    await monitor.check_once()
    titles = [a["title"] for a in alerts]
    assert "🔍 Parity Check Running" in titles
    assert "stays protected" in alerts[titles.index("🔍 Parity Check Running")]["message"]
    # Nothing is written during a read-only check, so DISK_INVALID is a fault.
    assert any("disk1" in a["message"] and "Parity" not in a["title"] for a in alerts)

    await monitor.check_once()
    assert alerts[-1]["title"] == "✅ Parity Check Complete"


async def test_correcting_or_unknown_operation_keeps_the_rebuild_wording():
    monitor, alerts = await _array_monitor([
        _array({"status": "RUNNING", "correcting": None}, "DISK_INVALID"),
    ])
    await monitor.check_once()
    assert [a["title"] for a in alerts] == ["🔄 Parity Operation Running"]
    assert "not** fully protected" in alerts[0]["message"]


# --- /manage ignores: a stale button never deletes the wrong pattern --------


async def test_stale_delete_button_removes_only_its_own_pattern(tmp_path):
    manager = IgnoreManager({}, json_path=str(tmp_path / "ignores.json"))
    for pattern in ("alpha timeout", "beta refused", "gamma reset"):
        manager.add_ignore("plex", pattern)
    _, keyboard = _build_ignore_detail_keyboard("plex", manager.get_runtime_ignores("plex"))
    buttons = [row[0].callback_data for row in keyboard.inline_keyboard[:-1]]
    handler = manage_delete_ignore_callback(manager)

    def tap(data):
        cb = AsyncMock()
        cb.data = data
        cb.message = None
        return cb

    first = tap(buttons[0])
    await handler(first)
    # The list on screen is now stale: under index keys, "gamma" would
    # have slid into slot 1 and been deleted by the "beta" button.
    second = tap(buttons[1])
    await handler(second)
    assert [p for _, p, _ in manager.get_runtime_ignores("plex")] == ["gamma reset"]

    again = tap(buttons[1])
    await handler(again)
    assert "already gone" in again.answer.call_args.args[0]
    assert [p for _, p, _ in manager.get_runtime_ignores("plex")] == ["gamma reset"]
    assert buttons[2].endswith(ignore_key("gamma reset"))


async def test_long_container_name_ignore_buttons_fit_and_resolve(tmp_path):
    name = "a-very-long-container-name-that-goes-on-and-on-forever-and-ever"
    manager = IgnoreManager({}, json_path=str(tmp_path / "ignores.json"))
    manager.add_ignore(name, "noisy line")
    _, keyboard = _build_ignore_detail_keyboard(name, manager.get_runtime_ignores(name))
    data = keyboard.inline_keyboard[0][0].callback_data
    assert len(data.encode()) <= 64
    cb = AsyncMock()
    cb.data = data
    cb.message = None
    await manage_delete_ignore_callback(manager)(cb)
    assert manager.get_runtime_ignores(name) == []


# --- Unknown slash commands --------------------------------------------------


async def test_unknown_command_points_at_help():
    message = MagicMock()
    message.text = "/stauts plex"
    message.answer = AsyncMock()
    await unknown_command(message)
    text = message.answer.call_args.args[0]
    assert "/stauts" in text and "/help" in text
