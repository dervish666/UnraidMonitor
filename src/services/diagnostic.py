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

    def __init__(
        self,
        docker_client: docker.DockerClient,
        anthropic_client,
        model: str = "claude-haiku-4-5-20251001",
        brief_max_tokens: int = 300,
        detail_max_tokens: int = 800,
        context_expiry_seconds: int = 600,
    ):
        self._docker = docker_client
        self._anthropic = anthropic_client
        self._model = model
        self._brief_max_tokens = brief_max_tokens
        self._detail_max_tokens = detail_max_tokens
        self._context_expiry_seconds = context_expiry_seconds
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

    async def analyze(self, context: DiagnosticContext) -> str:
        """Analyze container issue using Claude API.

        Args:
            context: DiagnosticContext with container info.

        Returns:
            Brief analysis summary.
        """
        if not self._anthropic:
            return "❌ Anthropic API not configured. Set ANTHROPIC_API_KEY in .env"

        uptime_str = self._format_uptime(context.uptime_seconds) if context.uptime_seconds else "unknown"

        prompt = f"""You are a Docker container diagnostics assistant. Analyze this container issue and provide a brief, actionable summary.

Container: {context.container_name}
Image: {context.image}
Exit Code: {context.exit_code}
Uptime before exit: {uptime_str}
Restart Count: {context.restart_count}

Last log lines:
```
{context.logs}
```

Respond with 2-3 sentences: What happened, the likely cause, and how to fix it. Be specific and actionable. If you see a clear command to run, include it."""

        try:
            message = self._anthropic.messages.create(
                model=self._model,
                max_tokens=self._brief_max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            return message.content[0].text
        except Exception as e:
            logger.error(f"Claude API error: {e}")
            return f"❌ Error analyzing container: {e}"

    def _format_uptime(self, seconds: int) -> str:
        """Format uptime in human-readable form."""
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        if hours > 0:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"

    def store_context(self, user_id: int, context: DiagnosticContext) -> None:
        """Store diagnostic context for potential follow-up.

        Args:
            user_id: Telegram user ID.
            context: DiagnosticContext to store.
        """
        self._pending[user_id] = context

    def has_pending(self, user_id: int) -> bool:
        """Check if user has pending diagnostic context.

        Args:
            user_id: Telegram user ID.

        Returns:
            True if user has pending context less than 10 minutes old.
        """
        context = self._pending.get(user_id)
        if context is None:
            return False

        # Check if context is stale
        if context.created_at:
            age = (datetime.now() - context.created_at).total_seconds()
            if age > self._context_expiry_seconds:
                del self._pending[user_id]
                return False

        return True

    async def get_details(self, user_id: int) -> str | None:
        """Get detailed analysis for user's pending context.

        Args:
            user_id: Telegram user ID.

        Returns:
            Detailed analysis or None if no pending context.
        """
        if not self.has_pending(user_id):
            return None

        context = self._pending.pop(user_id)

        if not self._anthropic:
            return "❌ Anthropic API not configured."

        prompt = f"""Based on your previous analysis, provide detailed help:

Container: {context.container_name}
Your brief analysis: {context.brief_summary}

Logs:
```
{context.logs}
```

Provide:
1. Detailed root cause analysis
2. Step-by-step fix instructions
3. How to prevent this in future

Be specific and actionable."""

        try:
            message = self._anthropic.messages.create(
                model=self._model,
                max_tokens=self._detail_max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            return message.content[0].text
        except Exception as e:
            logger.error(f"Claude API error: {e}")
            return f"❌ Error getting details: {e}"
