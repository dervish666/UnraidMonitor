"""AI-powered container diagnostics service."""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import docker

logger = logging.getLogger(__name__)


def _parse_docker_timestamp(ts: str) -> datetime | None:
    """Parse Docker timestamp string to datetime."""
    if not ts or ts == "0001-01-01T00:00:00Z":
        return None
    try:
        # Handle Docker's timestamp format
        ts = ts.replace("Z", "+00:00")
        if "." in ts:
            # Truncate nanoseconds to microseconds
            parts = ts.split(".")
            fraction = parts[1].split("+")[0].split("-")[0][:6]
            tz_part = (
                "+" + parts[1].split("+")[1]
                if "+" in parts[1]
                else "-" + parts[1].split("-")[1]
                if "-" in parts[1]
                else "+00:00"
            )
            ts = f"{parts[0]}.{fraction}{tz_part}"
        return datetime.fromisoformat(ts)
    except Exception:
        return None


@dataclass
class DiagnosticContext:
    """Context for a diagnostic request."""

    container_name: str
    logs: str
    exit_code: int | None
    image: str
    uptime_seconds: int | None
    restart_count: int
    brief_summary: str | None = None
    created_at: datetime | None = None

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now()


class DiagnosticService:
    """AI-powered container diagnostics."""

    def __init__(self, docker_client: docker.DockerClient, anthropic_client):
        self._docker = docker_client
        self._anthropic = anthropic_client
        self._pending: dict[int, DiagnosticContext] = {}

    def gather_context(self, container_name: str, lines: int = 50) -> DiagnosticContext | None:
        """Gather diagnostic context from a container.

        Args:
            container_name: Name of the container to diagnose.
            lines: Number of log lines to retrieve.

        Returns:
            DiagnosticContext with container info, or None if container not found.
        """
        try:
            container = self._docker.containers.get(container_name)
        except docker.errors.NotFound:
            return None

        # Get logs
        log_bytes = container.logs(tail=lines, timestamps=False)
        logs = log_bytes.decode("utf-8", errors="replace")

        # Get container state
        attrs = container.attrs
        state = attrs.get("State", {})
        exit_code = state.get("ExitCode")
        started_at = _parse_docker_timestamp(state.get("StartedAt", ""))
        restart_count = attrs.get("RestartCount", 0)

        # Calculate uptime
        uptime_seconds = None
        if started_at:
            now = datetime.now(timezone.utc)
            uptime_seconds = int((now - started_at).total_seconds())

        # Get image
        image_tags = container.image.tags
        image = image_tags[0] if image_tags else "unknown"

        return DiagnosticContext(
            container_name=container_name,
            logs=logs,
            exit_code=exit_code,
            image=image,
            uptime_seconds=uptime_seconds,
            restart_count=restart_count,
        )
