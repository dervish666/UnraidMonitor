"""Tests for the NUT protocol client.

The happy paths run against a real asyncio TCP server speaking the documented
protocol, rather than a mocked socket: the parsing is the thing most likely to
be wrong, and a mock would just replay my own assumptions back at me.
"""

import asyncio

import pytest

from src.nut.client import (
    NutAuthError,
    NutClient,
    NutProtocolError,
    NutUnavailable,
    parse_line,
)


# ---------------------------------------------------------------------------
# Line parsing
# ---------------------------------------------------------------------------


def test_parse_line_bare_words():
    assert parse_line("BEGIN LIST UPS") == ["BEGIN", "LIST", "UPS"]


def test_parse_line_quoted_value():
    assert parse_line('VAR myups ups.status "OL CHRG"') == [
        "VAR", "myups", "ups.status", "OL CHRG",
    ]


def test_parse_line_escaped_quote_inside_value():
    assert parse_line(r'VAR u ups.model "13\" rack"') == ["VAR", "u", "ups.model", '13" rack']


def test_parse_line_escaped_backslash():
    assert parse_line(r'VAR u x "a\\b"') == ["VAR", "u", "x", r"a\b"]


def test_parse_line_empty_quoted_value_survives():
    # An empty description must stay a token, or the row shifts left and the
    # variable name gets read as its value.
    assert parse_line('UPS myups ""') == ["UPS", "myups", ""]


def test_parse_line_collapses_runs_of_whitespace():
    assert parse_line("VAR   u    x   \"1\"") == ["VAR", "u", "x", "1"]


# ---------------------------------------------------------------------------
# A minimal upsd
# ---------------------------------------------------------------------------


class FakeUpsd:
    """Speaks just enough of the protocol to exercise the client."""

    def __init__(self, ups=None, require_auth=False, err_on_list_var=None):
        self.ups = ups if ups is not None else {"myups": "Test UPS"}
        self.require_auth = require_auth
        self.err_on_list_var = err_on_list_var
        self.variables = {
            "ups.status": "OL CHRG",
            "ups.mfr": "APC",
            "ups.model": "Back-UPS 1500",
            "battery.charge": "100",
            "battery.runtime": "4320",
            "ups.load": "34",
            "input.voltage": "241.0",
        }
        self.commands: list[str] = []
        self._server = None
        self.port = 0

    async def start(self):
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def stop(self):
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(self, reader, writer):
        authed = not self.require_auth
        try:
            while True:
                raw = await reader.readline()
                if not raw:
                    break
                line = raw.decode().strip()
                self.commands.append(line)

                if line.startswith("USERNAME"):
                    writer.write(b"OK\n")
                elif line.startswith("PASSWORD"):
                    authed = True
                    writer.write(b"OK\n")
                elif line == "LOGOUT":
                    writer.write(b"OK Goodbye\n")
                    await writer.drain()
                    break
                elif line == "LIST UPS":
                    out = ["BEGIN LIST UPS"]
                    out += [f'UPS {name} "{desc}"' for name, desc in self.ups.items()]
                    out.append("END LIST UPS")
                    writer.write(("\n".join(out) + "\n").encode())
                elif line.startswith("LIST VAR"):
                    name = line.split()[-1]
                    if self.err_on_list_var:
                        writer.write(f"ERR {self.err_on_list_var}\n".encode())
                    elif not authed:
                        writer.write(b"ERR ACCESS-DENIED\n")
                    elif name not in self.ups:
                        writer.write(b"ERR UNKNOWN-UPS\n")
                    else:
                        out = [f"BEGIN LIST VAR {name}"]
                        out += [f'VAR {name} {k} "{v}"' for k, v in self.variables.items()]
                        out.append(f"END LIST VAR {name}")
                        writer.write(("\n".join(out) + "\n").encode())
                else:
                    writer.write(b"ERR UNKNOWN-COMMAND\n")
                await writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            pass
        finally:
            writer.close()


async def wait_for_command(server: "FakeUpsd", command: str, timeout: float = 1.0) -> bool:
    """Wait for the fake server to observe a command.

    LOGOUT is written and drained without waiting for the reply, so asserting
    on it straight after fetch() returns is a race, not a bug.
    """
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if command in server.commands:
            return True
        await asyncio.sleep(0.01)
    return command in server.commands


@pytest.fixture
async def upsd():
    server = await FakeUpsd().start()
    yield server
    await server.stop()


# ---------------------------------------------------------------------------
# Client behaviour
# ---------------------------------------------------------------------------


async def test_list_ups_returns_names_and_descriptions(upsd):
    client = NutClient(host="127.0.0.1", port=upsd.port)
    assert await client.list_ups() == {"myups": "Test UPS"}


async def test_fetch_auto_resolves_the_only_ups(upsd):
    client = NutClient(host="127.0.0.1", port=upsd.port)
    name, variables = await client.fetch()
    assert name == "myups"
    assert variables["ups.status"] == "OL CHRG"
    assert variables["battery.charge"] == "100"
    assert variables["ups.model"] == "Back-UPS 1500"


async def test_fetch_named_ups_skips_the_listing(upsd):
    client = NutClient(host="127.0.0.1", port=upsd.port)
    name, _ = await client.fetch("myups")
    assert name == "myups"
    assert "LIST UPS" not in upsd.commands


async def test_fetch_says_logout_before_closing(upsd):
    client = NutClient(host="127.0.0.1", port=upsd.port)
    await client.fetch("myups")
    assert await wait_for_command(upsd, "LOGOUT")


async def test_two_ups_devices_without_a_name_is_an_error():
    server = await FakeUpsd(ups={"a": "one", "b": "two"}).start()
    try:
        client = NutClient(host="127.0.0.1", port=server.port)
        with pytest.raises(NutProtocolError, match="nut.ups_name"):
            await client.fetch()
    finally:
        await server.stop()


async def test_no_ups_served_is_unavailable():
    server = await FakeUpsd(ups={}).start()
    try:
        client = NutClient(host="127.0.0.1", port=server.port)
        with pytest.raises(NutUnavailable):
            await client.fetch()
    finally:
        await server.stop()


async def test_credentials_are_sent_when_configured():
    server = await FakeUpsd(require_auth=True).start()
    try:
        client = NutClient(
            host="127.0.0.1", port=server.port, username="monuser", password="secret",
        )
        await client.fetch("myups")
        assert "USERNAME monuser" in server.commands
        assert "PASSWORD secret" in server.commands
    finally:
        await server.stop()


async def test_access_denied_raises_auth_error():
    server = await FakeUpsd(err_on_list_var="ACCESS-DENIED").start()
    try:
        client = NutClient(host="127.0.0.1", port=server.port)
        with pytest.raises(NutAuthError):
            await client.fetch("myups")
    finally:
        await server.stop()


async def test_driver_not_connected_is_unavailable_not_protocol_error():
    # This is the case that matters: upsd is up but knows nothing about the
    # UPS. It must not read as a successful empty poll.
    server = await FakeUpsd(err_on_list_var="DRIVER-NOT-CONNECTED").start()
    try:
        client = NutClient(host="127.0.0.1", port=server.port)
        with pytest.raises(NutUnavailable):
            await client.fetch("myups")
    finally:
        await server.stop()


async def test_stale_data_is_unavailable():
    server = await FakeUpsd(err_on_list_var="DATA-STALE").start()
    try:
        client = NutClient(host="127.0.0.1", port=server.port)
        with pytest.raises(NutUnavailable):
            await client.fetch("myups")
    finally:
        await server.stop()


async def test_unknown_ups_is_a_protocol_error():
    server = await FakeUpsd().start()
    try:
        client = NutClient(host="127.0.0.1", port=server.port)
        with pytest.raises(NutProtocolError):
            await client.fetch("nosuchups")
    finally:
        await server.stop()


async def test_nothing_listening_is_unavailable():
    # Port 1 is reserved and nothing sane binds it.
    client = NutClient(host="127.0.0.1", port=1, timeout=1.0)
    with pytest.raises(NutUnavailable):
        await client.fetch("myups")


async def test_target_string_is_host_and_port():
    assert NutClient(host="10.0.0.5", port=3493).target == "10.0.0.5:3493"


# ---------------------------------------------------------------------------
# The real client driving the real monitor
# ---------------------------------------------------------------------------


class _Recorder:
    def __init__(self):
        self.alerts = []

    async def __call__(self, title, message, alert_type):
        self.alerts.append((title, message, alert_type))


class _NoMutes:
    def is_ups_muted(self):
        return False


def _monitor_for(port):
    from src.config import NutConfig
    from src.nut.monitor import UpsMonitor

    recorder = _Recorder()
    monitor = UpsMonitor(
        client=NutClient(host="127.0.0.1", port=port, timeout=2.0),
        config=NutConfig(enabled=True, host="127.0.0.1", port=port),
        on_alert=recorder,
        mute_manager=_NoMutes(),
    )
    return monitor, recorder


async def test_monitor_reads_a_real_server_and_stays_quiet_when_healthy(upsd):
    monitor, recorder = _monitor_for(upsd.port)
    variables = await monitor.check_once()
    assert variables["ups.status"] == "OL CHRG"
    assert monitor.is_available
    assert monitor.ups_name == "myups"
    assert recorder.alerts == []


async def test_monitor_alerts_when_a_real_server_reports_mains_loss(upsd):
    monitor, recorder = _monitor_for(upsd.port)
    await monitor.check_once()
    upsd.variables["ups.status"] = "OB DISCHRG"
    upsd.variables["battery.charge"] = "76"
    await monitor.check_once()
    assert [a[0] for a in recorder.alerts] == ["UPS On Battery"]
    # alert_type drives the mute buttons, so it has to be "ups" and not "server".
    assert recorder.alerts[0][2] == "ups"
    assert "Battery: 76%" in recorder.alerts[0][1]


async def test_snapshot_from_a_real_server_renders_as_available(upsd):
    from src.bot.ups_command import format_ups

    monitor, _ = _monitor_for(upsd.port)
    await monitor.check_once()
    text = format_ups(await monitor.get_snapshot())
    assert "APC Back-UPS 1500" in text
    assert "Unavailable" not in text


async def test_a_server_that_goes_away_reads_as_unavailable_not_healthy():
    from src.bot.ups_command import format_ups

    server = await FakeUpsd().start()
    monitor, _ = _monitor_for(server.port)
    await monitor.check_once()
    assert monitor.is_available

    await server.stop()
    snapshot = await monitor.get_snapshot(force=True)
    assert snapshot["available"] is False
    text = format_ups(snapshot)
    assert "Unavailable" in text
    assert "✅" not in text
