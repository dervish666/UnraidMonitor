"""Unraid API client wrapper with connection management.

Implements direct GraphQL queries to bypass unraid-api library's broken
header handling (library doesn't include apollo-require-preflight header).
"""

import json
import logging
import ssl
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

# System metrics query - discovered via introspection
SYSTEM_METRICS_QUERY = """
    query {
        info {
            os { uptime hostname }
        }
        metrics {
            cpu { percentTotal }
            memory { total used free percentTotal }
        }
    }
"""

ARRAY_STATUS_QUERY = """
    query {
        array {
            state
            capacity {
                kilobytes { free used total }
                disks { free used total }
            }
            caches {
                name size temp status fsSize fsFree fsUsed
            }
            disks {
                name size temp status fsSize fsFree fsUsed
            }
            parities {
                name size temp status
            }
            parityCheckStatus {
                status progress speed errors correcting
            }
        }
    }
"""

NOTIFICATIONS_QUERY = """
    query($limit: Int!) {
        notifications {
            list(filter: {type: UNREAD, offset: 0, limit: $limit}) {
                id
                title
                subject
                description
                importance
                link
                timestamp
                formattedTimestamp
            }
        }
    }
"""


class UnraidConnectionError(Exception):
    """Raised when Unraid client is not connected."""

    pass


class UnraidClientWrapper:
    """Direct GraphQL client for Unraid API.

    Bypasses unraid-api library to properly set CSRF headers.
    """

    def __init__(
        self,
        host: str,
        api_key: str,
        port: int = 80,
        verify_ssl: bool = True,
        use_ssl: bool = False,
    ):
        """Initialize the wrapper.

        Args:
            host: Unraid server hostname or IP.
            api_key: API key for authentication.
            port: HTTP/HTTPS port (default 80).
            verify_ssl: Whether to verify SSL certificates (default True).
            use_ssl: Whether to use HTTPS (default False for Unraid).
        """
        self._host = host
        self._api_key = api_key
        self._port = port
        self._verify_ssl = verify_ssl
        self._use_ssl = use_ssl
        self._session: aiohttp.ClientSession | None = None
        self._connected = False

        # Build URL based on protocol
        protocol = "https" if use_ssl else "http"
        default_port = 443 if use_ssl else 80
        port_suffix = f":{port}" if port != default_port else ""
        self._base_url = f"{protocol}://{host}{port_suffix}/graphql"

    @property
    def is_connected(self) -> bool:
        """Check if client is connected."""
        return self._connected

    async def connect(self) -> None:
        """Establish connection to Unraid server."""
        # Create SSL context only if using SSL
        if self._use_ssl:
            if self._verify_ssl:
                ssl_context: ssl.SSLContext | bool = True
            else:
                logger.warning(
                    "SSL certificate verification disabled for Unraid connection to %s. "
                    "This is insecure and allows man-in-the-middle attacks. "
                    "Consider enabling verify_ssl in production.",
                    self._host,
                )
                ssl_context = ssl.create_default_context()
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE
        else:
            ssl_context = False

        connector = aiohttp.TCPConnector(ssl=ssl_context)

        # Create session with required headers for Unraid's CSRF protection
        self._session = aiohttp.ClientSession(
            connector=connector,
            timeout=aiohttp.ClientTimeout(total=15),
            headers={
                "x-api-key": self._api_key,
                "Content-Type": "application/json",
                "apollo-require-preflight": "true",
            },
        )

        # Verify connectivity with a simple test query before marking connected
        try:
            payload = {"query": "{ info { os { hostname } } }"}
            async with self._session.post(self._base_url, json=payload) as response:
                if response.status != 200:
                    text = self._safe_body(await response.text())
                    logger.error(f"Unraid connectivity test failed: {response.status} - {text}")
                    await self._session.close()
                    self._session = None
                    return
        except Exception as e:
            logger.error(f"Unraid connectivity test failed: {e}")
            if self._session:
                await self._session.close()
            self._session = None
            return

        self._connected = True
        logger.info(f"Connected to Unraid server at {self._host}")

    async def disconnect(self) -> None:
        """Close connection to Unraid server."""
        self._connected = False
        if self._session:
            await self._session.close()
            self._session = None
        logger.info("Disconnected from Unraid server")

    def _ensure_connected(self) -> None:
        """Raise error if not connected."""
        if not self._connected or self._session is None:
            raise UnraidConnectionError("Not connected to Unraid server")

    def _safe_body(self, text: str) -> str:
        """Redact the API key and truncate a response body for safe logging.

        Redaction happens before truncation so a key straddling the cut-off
        can't partially leak.
        """
        if self._api_key:
            text = text.replace(self._api_key, "[REDACTED]")
        return text[:200]

    async def _reconnect_if_needed(self) -> None:
        """Attempt to reconnect if the connection was lost."""
        if self._connected and self._session is not None:
            return
        logger.info("Unraid connection lost, attempting reconnect...")
        try:
            await self.connect()
        except Exception as e:
            logger.error(f"Unraid reconnect failed: {e}")

    async def _execute_query(
        self, query: str, variables: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Execute a GraphQL query, reconnecting once if needed.

        Args:
            query: GraphQL query string.
            variables: Optional GraphQL variables.

        Returns:
            Query result data.

        Raises:
            UnraidConnectionError: If query fails.
        """
        # Auto-reconnect if connection was lost
        if not self._connected or self._session is None:
            await self._reconnect_if_needed()
        self._ensure_connected()
        assert self._session is not None

        payload: dict[str, Any] = {"query": query}
        if variables:
            payload["variables"] = variables

        try:
            async with self._session.post(self._base_url, json=payload) as response:
                if response.status != 200:
                    text = self._safe_body(await response.text())
                    raise UnraidConnectionError(
                        f"GraphQL request failed: {response.status} - {text}"
                    )

                try:
                    result = await response.json()
                except json.JSONDecodeError as e:
                    text = self._safe_body(await response.text())
                    raise UnraidConnectionError(
                        f"Invalid JSON response: {e}. Response: {text}"
                    )

                if "errors" in result:
                    errors = result["errors"]
                    logger.error(f"GraphQL query returned errors: {errors}")
                    raise UnraidConnectionError(f"GraphQL errors: {errors}")

                data: dict[str, Any] = result.get("data", {})
                return data

        except aiohttp.ClientError as e:
            # Mark disconnected so next call will try to reconnect
            self._connected = False
            raise UnraidConnectionError(f"Connection failed: {e}")

    async def get_system_metrics(self) -> dict[str, Any]:
        """Get system metrics (CPU, memory, temp, uptime).

        Returns:
            Dict with cpu_percent, cpu_temperature, memory_percent, etc.
        """
        data = await self._execute_query(SYSTEM_METRICS_QUERY)

        info = data.get("info", {})
        metrics = data.get("metrics", {})

        uptime = info.get("os", {}).get("uptime", "")

        cpu_metrics = metrics.get("cpu", {})
        cpu_percent = cpu_metrics.get("percentTotal", 0)

        mem_metrics = metrics.get("memory", {})
        memory_percent = mem_metrics.get("percentTotal", 0)
        memory_used = mem_metrics.get("used", 0)
        memory_total = mem_metrics.get("total", 0)

        return {
            "cpu_percent": cpu_percent,
            "cpu_temperature": None,  # Not available in current Unraid GraphQL schema
            "memory_percent": memory_percent,
            "memory_used": memory_used,
            "memory_total": memory_total,
            "uptime": uptime,
        }

    async def get_array_status(self) -> dict[str, Any]:
        """Get array status (disks, parity, capacity).

        Returns:
            Dict with state, capacity, disks, etc.
        """
        data = await self._execute_query(ARRAY_STATUS_QUERY)
        result: dict[str, Any] = data.get("array", {})
        return result

    async def get_notifications(self, limit: int = 25) -> list[dict[str, Any]]:
        """Get unread notifications from Unraid's own notification feed.

        Args:
            limit: Maximum notifications to fetch, newest first.

        Returns:
            List of notification dicts (id, title, subject, description,
            importance, link, timestamp, formattedTimestamp).
        """
        data = await self._execute_query(NOTIFICATIONS_QUERY, {"limit": limit})
        notifications = data.get("notifications") or {}
        result: list[dict[str, Any]] = notifications.get("list") or []
        return result

