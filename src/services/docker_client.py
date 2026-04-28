"""Shared Docker client wrapper for transparent reconnection.

All consumers hold a reference to this wrapper instead of the raw
``docker.DockerClient``.  When the event monitor reconnects, it calls
``replace()`` and every consumer transparently uses the new client.
"""

from __future__ import annotations

from typing import Any

import docker


class SharedDockerClient:
    """Proxy that delegates to a replaceable Docker client."""

    def __init__(self, client: docker.DockerClient):
        self._client = client

    @property
    def client(self) -> docker.DockerClient:
        return self._client

    @property
    def containers(self) -> Any:
        return self._client.containers

    @property
    def images(self) -> Any:
        return self._client.images

    def replace(self, new_client: docker.DockerClient) -> None:
        """Replace the underlying client (called on reconnect)."""
        self._client = new_client

    def close(self) -> None:
        self._client.close()
