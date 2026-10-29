"""Shared Docker client wrapper for transparent reconnection.

All consumers hold a reference to this wrapper instead of the raw
``docker.DockerClient``.  When the event monitor reconnects, it calls
``replace()`` and every consumer transparently uses the new client.

Consumers are typed as ``docker.DockerClient`` and handed this wrapper behind
a ``type: ignore``, so mypy cannot see a missing attribute. Anything a
consumer reads off a client must be proxied here. ``info()`` and ``networks``
were once missing, so the host core count always came from the fallback and
every secondary-network reconnect after a /pull failed.
``tests/test_docker_client.py`` checks the wrapper against the real client.
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

    @property
    def networks(self) -> Any:
        return self._client.networks

    def info(self) -> dict[str, Any]:
        """Daemon-wide facts (``docker info``), e.g. ``NCPU`` for the host core count."""
        result: dict[str, Any] = self._client.info()
        return result

    def replace(self, new_client: docker.DockerClient) -> None:
        """Replace the underlying client (called on reconnect)."""
        self._client = new_client

    def close(self) -> None:
        self._client.close()
