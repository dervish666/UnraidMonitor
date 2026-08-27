"""Async client for the NUT (Network UPS Tools) network protocol.

Written against the published protocol rather than wrapping an existing
library: every Python NUT client on PyPI (PyNUT, nut2, pynut3) is GPLv3, and
this project ships MIT under a public Docker image. The protocol is a small
line-based plain-text exchange on TCP 3493, so a first-party client costs less
than the licence conflict would.

Reference: https://networkupstools.org/docs/developer-guide.chunked/ar01s08.html

One connection per poll, like upsc does. A 60-second poll does not justify
holding a socket open and handling half-dead connections.
"""

import asyncio
import logging

from src.constants import NUT_DEFAULT_PORT, NUT_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)


class NutError(Exception):
    """Base class for NUT client failures."""


class NutUnavailable(NutError):
    """upsd could not be reached, or the driver is not talking to the UPS.

    Distinct from "the UPS is fine": callers must render this as unavailable
    rather than treating an empty reading as good news.
    """


class NutAuthError(NutError):
    """upsd rejected the supplied credentials."""


class NutProtocolError(NutError):
    """upsd said something the protocol does not allow."""


# ups.status is an opaque string of space-separated tokens. Meanings are from
# docs/new-drivers.txt in the NUT source. ALARM is not raised by drivers
# directly any more but still appears, prepended, when an alarm is committed.
STATUS_MEANINGS: dict[str, str] = {
    "OL": "On line, mains present",
    "OB": "On battery, mains lost",
    "LB": "Low battery",
    "HB": "High battery",
    "RB": "Battery needs replacing",
    "CHRG": "Battery charging",
    "DISCHRG": "Battery discharging",
    "BYPASS": "Bypass active, no battery protection",
    "CAL": "Runtime calibration in progress",
    "OFF": "Offline, not supplying the load",
    "OVER": "Overloaded",
    "TRIM": "Trimming incoming voltage",
    "BOOST": "Boosting incoming voltage",
    "FSD": "Forced shutdown in progress",
    "ALARM": "UPS raised an alarm",
}


def parse_line(line: str) -> list[str]:
    """Split one protocol line into tokens.

    Values arrive double-quoted, with a backslash escaping a literal quote or
    backslash. Everything else is whitespace-separated bare words.
    """
    tokens: list[str] = []
    buf: list[str] = []
    in_quotes = False
    escaped = False
    have_token = False

    for ch in line:
        if escaped:
            buf.append(ch)
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == '"':
            if in_quotes:
                tokens.append("".join(buf))
                buf = []
                in_quotes = False
                have_token = False
            else:
                in_quotes = True
                have_token = True
        elif ch.isspace() and not in_quotes:
            if buf or have_token:
                tokens.append("".join(buf))
                buf = []
                have_token = False
        else:
            buf.append(ch)
            have_token = True

    if buf or have_token:
        tokens.append("".join(buf))
    return tokens


def _raise_for_error(line: str) -> None:
    """Turn an ERR response into the right exception."""
    if not line.startswith("ERR"):
        return
    parts = line.split(None, 2)
    code = parts[1] if len(parts) > 1 else "UNKNOWN"
    if code == "ACCESS-DENIED":
        raise NutAuthError("upsd refused the credentials (ACCESS-DENIED)")
    if code in ("DRIVER-NOT-CONNECTED", "DATA-STALE"):
        raise NutUnavailable(f"upsd cannot read the UPS ({code})")
    raise NutProtocolError(f"upsd returned ERR {code}")


class NutClient:
    """Reads UPS variables from a NUT server."""

    def __init__(
        self,
        host: str,
        port: int = NUT_DEFAULT_PORT,
        username: str | None = None,
        password: str | None = None,
        timeout: float = NUT_TIMEOUT_SECONDS,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._timeout = timeout

    @property
    def target(self) -> str:
        return f"{self._host}:{self._port}"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def list_ups(self) -> dict[str, str]:
        """Return {ups_name: description} for every UPS upsd serves."""
        reader, writer = await self._open()
        try:
            rows = await self._read_list(reader, writer, "LIST UPS", "UPS")
            return {row[1]: (row[2] if len(row) > 2 else "") for row in rows if len(row) > 1}
        finally:
            await self._close(writer)

    async def fetch(self, ups_name: str | None = None) -> tuple[str, dict[str, str]]:
        """Read every variable for one UPS.

        Args:
            ups_name: UPS to read. When None, and upsd serves exactly one, that
                one is used; more than one is ambiguous and raises.

        Returns:
            (resolved_ups_name, {variable: value}).

        Raises:
            NutUnavailable: upsd unreachable, or it cannot read the hardware.
            NutAuthError: credentials rejected.
            NutProtocolError: unexpected response, or an ambiguous UPS name.
        """
        reader, writer = await self._open()
        try:
            name = ups_name
            if not name:
                rows = await self._read_list(reader, writer, "LIST UPS", "UPS")
                names = [row[1] for row in rows if len(row) > 1]
                if not names:
                    raise NutUnavailable(f"{self.target} serves no UPS")
                if len(names) > 1:
                    raise NutProtocolError(
                        f"{self.target} serves {len(names)} UPS devices "
                        f"({', '.join(names)}); set nut.ups_name to pick one"
                    )
                name = names[0]

            rows = await self._read_list(reader, writer, f"LIST VAR {name}", "VAR")
            variables = {row[2]: row[3] for row in rows if len(row) > 3}
            if not variables:
                raise NutUnavailable(f"{self.target} returned no variables for {name}")
            return name, variables
        finally:
            await self._close(writer)

    # ------------------------------------------------------------------
    # Protocol plumbing
    # ------------------------------------------------------------------

    async def _open(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, self._port), timeout=self._timeout
            )
        except asyncio.TimeoutError as e:
            raise NutUnavailable(f"timed out connecting to {self.target}") from e
        except OSError as e:
            raise NutUnavailable(f"cannot reach {self.target}: {e}") from e

        if self._username:
            await self._expect_ok(reader, writer, f"USERNAME {self._username}")
            if self._password:
                await self._expect_ok(reader, writer, f"PASSWORD {self._password}")
        return reader, writer

    async def _close(self, writer: asyncio.StreamWriter) -> None:
        """Say LOGOUT and drop the socket. Never raises: the read already happened."""
        try:
            writer.write(b"LOGOUT\n")
            await asyncio.wait_for(writer.drain(), timeout=self._timeout)
        except Exception:
            pass
        try:
            writer.close()
            await asyncio.wait_for(writer.wait_closed(), timeout=self._timeout)
        except Exception:
            pass

    async def _send(self, writer: asyncio.StreamWriter, command: str) -> None:
        writer.write(f"{command}\n".encode())
        try:
            await asyncio.wait_for(writer.drain(), timeout=self._timeout)
        except asyncio.TimeoutError as e:
            raise NutUnavailable(f"timed out sending to {self.target}") from e

    async def _readline(self, reader: asyncio.StreamReader) -> str:
        try:
            raw = await asyncio.wait_for(reader.readline(), timeout=self._timeout)
        except asyncio.TimeoutError as e:
            raise NutUnavailable(f"timed out reading from {self.target}") from e
        if not raw:
            raise NutUnavailable(f"{self.target} closed the connection")
        return raw.decode("utf-8", errors="replace").rstrip("\r\n")

    async def _expect_ok(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, command: str
    ) -> None:
        await self._send(writer, command)
        line = await self._readline(reader)
        _raise_for_error(line)
        if not line.startswith("OK"):
            raise NutProtocolError(f"expected OK from {self.target}, got {line!r}")

    async def _read_list(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        command: str,
        row_keyword: str,
    ) -> list[list[str]]:
        """Run a LIST command and return its rows as token lists.

        Responses are bracketed by BEGIN LIST .../END LIST ..., so the end
        marker terminates the read rather than a byte count.
        """
        await self._send(writer, command)

        first = await self._readline(reader)
        _raise_for_error(first)
        if not first.startswith("BEGIN LIST"):
            raise NutProtocolError(
                f"expected BEGIN LIST from {self.target}, got {first!r}"
            )

        rows: list[list[str]] = []
        while True:
            line = await self._readline(reader)
            _raise_for_error(line)
            if line.startswith("END LIST"):
                return rows
            tokens = parse_line(line)
            if tokens and tokens[0] == row_keyword:
                rows.append(tokens)
            # Anything else inside the block is a protocol extension we skip
            # deliberately, rather than failing a poll over an unknown row.
