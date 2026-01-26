"""Unraid API client wrapper with connection management.

Implements direct GraphQL queries to bypass unraid-api library's broken
header handling (library doesn't include apollo-require-preflight header).
"""

import logging
import ssl
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

# GraphQL queries (from unraid-api library)
SYSTEM_METRICS_QUERY = """
    query {
        metrics {
            cpu { percentTotal }
            memory {
                total used free available percentTotal
                swapTotal swapUsed percentSwapTotal
            }
        }
        info {
            os { uptime }
            cpu { packages { temp totalPower } }
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
            parityCheckStatus {
                status
                progress
                running
                paused
                errors
                speed
            }
            boot {
                id name device size temp type
                fsSize fsUsed fsFree fsType
            }
            parities {
                id idx name device size status type temp
                isSpinning
            }
            disks {
                id idx name device size status type temp
                fsSize fsFree fsUsed fsType
                isSpinning
            }
            caches {
                id idx name device size status type temp
                fsSize fsFree fsUsed fsType
                isSpinning
            }
        }
    }
"""

VMS_QUERY = """
    query {
        vms {
            domains {
                id
                name
                state
            }
        }
    }
"""

UPS_QUERY = """
    query {
        upsDevices {
            id
            name
            model
            status
            battery {
                chargeLevel
                estimatedRuntime
                health
            }
            power {
                inputVoltage
                outputVoltage
                loadPercentage
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
                ssl_context = ssl.create_default_context()
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE
        else:
            ssl_context = False

        connector = aiohttp.TCPConnector(ssl=ssl_context)

        # Create session with required headers for Unraid's CSRF protection
        self._session = aiohttp.ClientSession(
            connector=connector,
            headers={
                "x-api-key": self._api_key,
                "Content-Type": "application/json",
                "apollo-require-preflight": "true",
            },
        )

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

    async def _execute_query(self, query: str) -> dict[str, Any]:
        """Execute a GraphQL query.

        Args:
            query: GraphQL query string.

        Returns:
            Query result data.

        Raises:
            UnraidConnectionError: If query fails.
        """
        self._ensure_connected()

        payload = {"query": query}

        try:
            async with self._session.post(self._base_url, json=payload) as response:
                if response.status != 200:
                    text = await response.text()
                    raise UnraidConnectionError(
                        f"GraphQL request failed: {response.status} - {text}"
                    )

                result = await response.json()

                if "errors" in result:
                    errors = result["errors"]
                    raise UnraidConnectionError(f"GraphQL errors: {errors}")

                return result.get("data", {})

        except aiohttp.ClientError as e:
            raise UnraidConnectionError(f"Connection failed: {e}")

    async def get_system_metrics(self) -> dict[str, Any]:
        """Get system metrics (CPU, memory, temp, uptime).

        Returns:
            Dict with cpu_percent, cpu_temperature, memory_percent, etc.
        """
        data = await self._execute_query(SYSTEM_METRICS_QUERY)

        metrics = data.get("metrics", {})
        info = data.get("info", {})

        cpu_percent = metrics.get("cpu", {}).get("percentTotal", 0)
        memory = metrics.get("memory", {})
        memory_percent = memory.get("percentTotal", 0)
        memory_used = memory.get("used", 0)
        memory_total = memory.get("total", 0)

        uptime = info.get("os", {}).get("uptime", "")

        # Get CPU temp from first package
        packages = info.get("cpu", {}).get("packages", [])
        cpu_temp = packages[0].get("temp", 0) if packages else 0

        return {
            "cpu_percent": cpu_percent,
            "cpu_temperature": cpu_temp,
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
        return data.get("array", {})

    async def get_vms(self) -> list[dict[str, Any]]:
        """Get list of virtual machines.

        Returns:
            List of VM dicts with name, id, state.
        """
        data = await self._execute_query(VMS_QUERY)
        vms = data.get("vms", {})
        return vms.get("domains", [])

    async def get_ups_status(self) -> list[dict[str, Any]]:
        """Get UPS status.

        Returns:
            List of UPS device dicts.
        """
        data = await self._execute_query(UPS_QUERY)
        return data.get("upsDevices", [])
