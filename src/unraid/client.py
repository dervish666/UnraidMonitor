"""Unraid API client wrapper with connection management."""

import logging
from typing import Any

from unraid_api import UnraidClient

logger = logging.getLogger(__name__)


class UnraidConnectionError(Exception):
    """Raised when Unraid client is not connected."""

    pass


class UnraidClientWrapper:
    """Wrapper around UnraidClient with connection management."""

    def __init__(
        self,
        host: str,
        api_key: str,
        port: int = 443,
        verify_ssl: bool = True,
    ):
        """Initialize the wrapper.

        Args:
            host: Unraid server hostname or IP.
            api_key: API key for authentication.
            port: HTTPS port (default 443).
            verify_ssl: Whether to verify SSL certificates (default True).
        """
        self._host = host
        self._api_key = api_key
        self._port = port
        self._verify_ssl = verify_ssl
        self._client: UnraidClient | None = None
        self._connected = False

    @property
    def is_connected(self) -> bool:
        """Check if client is connected."""
        return self._connected

    async def connect(self) -> None:
        """Establish connection to Unraid server."""
        self._client = UnraidClient(
            self._host,
            self._api_key,
            https_port=self._port,
            verify_ssl=self._verify_ssl,
        )
        await self._client.__aenter__()
        self._connected = True
        logger.info(f"Connected to Unraid server at {self._host}")

    async def disconnect(self) -> None:
        """Close connection to Unraid server."""
        if self._client and self._connected:
            await self._client.__aexit__(None, None, None)
            self._connected = False
            logger.info("Disconnected from Unraid server")

    def _ensure_connected(self) -> None:
        """Raise error if not connected."""
        if not self._connected or self._client is None:
            raise UnraidConnectionError("Not connected to Unraid server")

    async def get_system_metrics(self) -> dict[str, Any]:
        """Get system metrics (CPU, memory, temp, uptime).

        Returns:
            Dict with cpu_percent, cpu_temperature, memory_percent, etc.
        """
        self._ensure_connected()
        return await self._client.get_system_metrics()

    async def get_array_status(self) -> dict[str, Any]:
        """Get array status (disks, parity, capacity).

        Returns:
            Dict with state, capacity, disks, etc.
        """
        self._ensure_connected()
        return await self._client.get_array_status()

    async def get_vms(self) -> list[dict[str, Any]]:
        """Get list of virtual machines.

        Returns:
            List of VM dicts with name, id, state.
        """
        self._ensure_connected()
        return await self._client.get_vms()

    async def get_ups_status(self) -> list[dict[str, Any]]:
        """Get UPS status.

        Returns:
            List of UPS device dicts.
        """
        self._ensure_connected()
        return await self._client.get_ups_status()
