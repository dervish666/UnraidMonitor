"""Shared Docker client wrapper for transparent reconnection.

All consumers hold a reference to this wrapper instead of the raw
``docker.DockerClient``.  When the event monitor reconnects, it calls
``replace()`` and every consumer transparently uses the new client.

Consumers are typed as ``DockerClientLike``, the surface both this wrapper and
``docker.DockerClient`` provide, so mypy sees a consumer reaching for an
attribute the wrapper does not proxy. (They used to be typed as the raw client
and handed the wrapper behind a ``type: ignore``: ``info()`` and ``networks``
went missing that way, so the host core count always came from the fallback
and every secondary-network reconnect after a /pull failed.)
``tests/test_docker_client.py`` checks the wrapper against the real client.
"""

from __future__ import annotations

from typing import Any, Protocol

import docker


class DockerClientLike(Protocol):
    """What consumers may use from a Docker client."""

    @property
    def containers(self) -> Any: ...

    @property
    def images(self) -> Any: ...

    @property
    def networks(self) -> Any: ...

    def info(self) -> Any: ...


class SharedDockerClient:
    """Proxy that delegates to a replaceable Docker client.

    Built with ``None`` when Docker was unreachable at startup, so the
    Unraid and UPS side of the bot can run without it. Until the event
    monitor connects and calls ``replace()``, every Docker call raises
    ``DockerException`` (handled like any daemon outage) instead of the bot
    refusing to start.
    """

    def __init__(self, client: docker.DockerClient | None):
        self._client = client

    @property
    def connected(self) -> bool:
        return self._client is not None

    def _live(self) -> docker.DockerClient:
        if self._client is None:
            raise docker.errors.DockerException("Docker is not connected yet")
        return self._client

    @property
    def client(self) -> docker.DockerClient:
        return self._live()

    @property
    def containers(self) -> Any:
        return self._live().containers

    @property
    def images(self) -> Any:
        return self._live().images

    @property
    def networks(self) -> Any:
        return self._live().networks

    def info(self) -> dict[str, Any]:
        """Daemon-wide facts (``docker info``), e.g. ``NCPU`` for the host core count."""
        result: dict[str, Any] = self._live().info()
        return result

    def replace(self, new_client: docker.DockerClient) -> None:
        """Replace the underlying client (called on reconnect)."""
        self._client = new_client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
