"""AI-powered container diagnostics service."""

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

import docker
from typing import Any

from src.utils.api_errors import handle_llm_error
from src.utils.formatting import format_uptime
from src.utils.sanitize import sanitize_container_name, sanitize_for_prompt, sanitize_logs

logger = logging.getLogger(__name__)

_SECRET_ENV_PATTERN = re.compile(
    r"(?i)(key|secret|password|passwd|pwd|token|credential|auth)",
)


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
    except Exception as e:
        logger.debug(f"Failed to parse Docker timestamp '{ts}': {e}")
        return None


def _filter_env_vars(env_list: list[str]) -> list[str]:
    """Keep non-secret env vars that are useful for diagnosis."""
    filtered = []
    for item in env_list:
        name = item.split("=", 1)[0] if "=" in item else item
        if not _SECRET_ENV_PATTERN.search(name):
            filtered.append(item)
    return filtered


@dataclass
class DiagnosticContext:
    """Context for a diagnostic request."""

    container_name: str
    logs: str
    exit_code: int | None
    image: str
    uptime_seconds: int | None
    restart_count: int
    status: str = "unknown"
    running: bool = False
    oom_killed: bool = False
    state_error: str = ""
    volumes: list[str] = field(default_factory=list)
    env_vars: list[str] = field(default_factory=list)
    ports: list[str] = field(default_factory=list)
    restart_policy: str = ""
    health_status: str = ""
    alert_context: str = ""
    brief_summary: str | None = None
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.created_at is None:
            self.created_at = datetime.now()


class DiagnosticService:
    """AI-powered container diagnostics."""

    def __init__(
        self,
        docker_client: docker.DockerClient,
        provider: Any = None,
        registry: Any = None,
        feature: str = "diagnostic",
        brief_max_tokens: int = 500,
        detail_max_tokens: int = 1000,
        context_expiry_seconds: int = 600,
    ):
        self._docker = docker_client
        self._provider = provider
        self._registry = registry
        self._feature = feature
        self._brief_max_tokens = brief_max_tokens
        self._detail_max_tokens = detail_max_tokens
        self._context_expiry_seconds = context_expiry_seconds
        self._pending: dict[int, DiagnosticContext] = {}

    async def gather_context(
        self,
        container_name: str,
        lines: int = 50,
        alert_context: str = "",
    ) -> DiagnosticContext | None:
        """Gather diagnostic context from a container.

        Args:
            container_name: Name of the container to diagnose.
            lines: Number of log lines to retrieve.
            alert_context: Description of what triggered this diagnosis.

        Returns:
            DiagnosticContext with container info, or None if container not found.
        """
        try:
            container = await asyncio.to_thread(self._docker.containers.get, container_name)
        except docker.errors.NotFound:
            return None
        except docker.errors.DockerException as e:
            # Daemon hiccup (API error, socket timeout) — treat like not-found
            # so the caller reports "couldn't gather context" instead of crashing
            # after the user has already seen "Analyzing…".
            logger.warning(f"Failed to fetch container {container_name} for diagnosis: {e}")
            return None

        # Get logs — degrade to a placeholder rather than losing the whole
        # diagnosis if the log endpoint hangs or errors mid-gather.
        try:
            log_bytes = await asyncio.to_thread(container.logs, tail=lines, timestamps=False)
            logs = log_bytes.decode("utf-8", errors="replace")
        except Exception as e:
            logger.warning(f"Failed to fetch logs for {container_name} during diagnosis: {e}")
            logs = "(logs unavailable)"

        # Get container state
        attrs = container.attrs
        state = attrs.get("State", {})
        exit_code = state.get("ExitCode")
        status = state.get("Status", "unknown")
        running = state.get("Running", False)
        oom_killed = state.get("OOMKilled", False)
        state_error = state.get("Error", "")
        started_at = _parse_docker_timestamp(state.get("StartedAt", ""))
        restart_count = attrs.get("RestartCount", 0)

        # Calculate uptime
        uptime_seconds = None
        if started_at:
            now = datetime.now(timezone.utc)
            uptime_seconds = int((now - started_at).total_seconds())

        # Get image -- may have been removed after an update
        try:
            img = container.image
            if img is not None:
                image_tags = img.tags
                image = image_tags[0] if image_tags else "unknown"
            else:
                image = container.attrs.get("Config", {}).get("Image", "unknown")
        except Exception:
            image = container.attrs.get("Config", {}).get("Image", "unknown")

        # Docker configuration
        config = attrs.get("Config", {})
        host_config = attrs.get("HostConfig", {})

        # Volume mounts
        mounts = attrs.get("Mounts", [])
        volumes = [
            f"{m.get('Source', '?')} -> {m.get('Destination', '?')} ({m.get('Mode') or 'rw'})"
            for m in mounts
        ]

        # Env vars (filtered to exclude secrets)
        env_vars = _filter_env_vars(config.get("Env", []))

        # Port mappings
        port_bindings = host_config.get("PortBindings") or {}
        ports = []
        for container_port, bindings in port_bindings.items():
            if bindings:
                for b in bindings:
                    host_port = b.get("HostPort", "?")
                    ports.append(f"{host_port}->{container_port}")
            else:
                ports.append(container_port)

        # Restart policy
        restart_pol = host_config.get("RestartPolicy", {})
        restart_policy = restart_pol.get("Name", "no")
        max_retry = restart_pol.get("MaximumRetryCount", 0)
        if max_retry:
            restart_policy = f"{restart_policy} (max {max_retry})"

        # Health check status
        health = state.get("Health", {})
        health_status = health.get("Status", "")

        return DiagnosticContext(
            container_name=container_name,
            logs=logs,
            exit_code=exit_code,
            image=image,
            uptime_seconds=uptime_seconds,
            restart_count=restart_count,
            status=status,
            running=running,
            oom_killed=oom_killed,
            state_error=state_error,
            volumes=volumes,
            env_vars=env_vars,
            ports=ports,
            restart_policy=restart_policy,
            health_status=health_status,
            alert_context=alert_context,
        )

    def _resolve_provider(self) -> Any:
        """Resolve the current LLM provider, preferring registry for runtime changes."""
        if self._registry is not None:
            return self._registry.get_provider(self._feature)
        return self._provider

    def _build_analysis_prompt(self, context: DiagnosticContext) -> str:
        """Build the analysis prompt from diagnostic context."""
        safe_name = sanitize_container_name(context.container_name)
        safe_image = sanitize_container_name(context.image)
        safe_logs = sanitize_logs(context.logs)

        # Status section — make the actual state very clear
        if context.running:
            status_line = "Status: **RUNNING** (container is up and producing errors)"
            uptime_str = format_uptime(context.uptime_seconds) if context.uptime_seconds else "unknown"
            status_detail = f"Uptime: {uptime_str}"
        elif context.status == "exited":
            uptime_str = format_uptime(context.uptime_seconds) if context.uptime_seconds else "unknown"
            status_line = f"Status: **EXITED** (exit code {context.exit_code})"
            status_detail = f"Ran for: {uptime_str} before exiting"
        elif context.status == "restarting":
            status_line = "Status: **RESTARTING** (container is in a restart loop)"
            status_detail = f"Restart count: {context.restart_count}"
        else:
            status_line = f"Status: {context.status.upper()}"
            status_detail = ""

        sections = [
            f"Container: {safe_name}",
            f"Image: {safe_image}",
            status_line,
        ]
        if status_detail:
            sections.append(status_detail)
        if context.restart_count and context.status != "restarting":
            sections.append(f"Restart count: {context.restart_count}")
        if context.oom_killed:
            sections.append("⚠️ OOM Killed: Yes (ran out of memory)")
        if context.state_error:
            sections.append(f"Docker error: {context.state_error}")
        if context.health_status:
            sections.append(f"Health check: {context.health_status}")
        if context.restart_policy:
            sections.append(f"Restart policy: {context.restart_policy}")

        status_block = "\n".join(sections)

        # Alert context — values are hardcoded descriptions today, but sanitize
        # anyway so a future caller passing free text can't inject instructions.
        alert_block = ""
        if context.alert_context:
            alert_block = f"\nTriggered by: {sanitize_for_prompt(context.alert_context, max_length=200)}\n"

        # Docker configuration
        config_parts = []
        if context.volumes:
            vol_list = "\n".join(f"  {v}" for v in context.volumes)
            config_parts.append(f"Volume mounts:\n{vol_list}")
        if context.env_vars:
            env_list = "\n".join(f"  {e}" for e in context.env_vars[:30])
            config_parts.append(f"Environment (non-secret):\n{env_list}")
        if context.ports:
            config_parts.append(f"Ports: {', '.join(context.ports)}")
        config_block = "\n".join(config_parts)

        prompt = f"""You are a Docker container diagnostics expert for a home server (Unraid). Analyze this issue precisely.

## Container State
{status_block}
{alert_block}
## Docker Configuration
{config_block if config_block else "(no configuration data available)"}

## Recent Logs
```
{safe_logs}
```

## Instructions
- Base your diagnosis on the container's CURRENT STATUS above. If the container is RUNNING, this is an error during operation — do NOT say it shut down or exited.
- Check the Docker configuration (volumes, env vars, ports) BEFORE suggesting configuration changes. If the user already has the relevant volume mount or env var, do not suggest adding it — instead explain what else might be wrong.
- Focus on the specific errors in the logs, not generic possibilities.
- Respond with exactly three sections:
  **What happened:** 1-2 sentences on what the actual problem is.
  **Likely cause:** 1-2 sentences on the root cause.
  **How to fix it:** Specific, actionable steps. Include commands if applicable."""
        return prompt

    async def analyze(self, context: DiagnosticContext) -> str:
        """Analyze container issue using AI.

        Args:
            context: DiagnosticContext with container info.

        Returns:
            Brief analysis summary.
        """
        provider = self._resolve_provider()
        if not provider:
            return "❌ AI provider not configured. Set ANTHROPIC_API_KEY, OPENAI_API_KEY, or OLLAMA_HOST in .env"

        prompt = self._build_analysis_prompt(context)

        try:
            response = await provider.chat(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=self._brief_max_tokens,
            )
            result: str = response.text
            return result
        except Exception as e:
            error_result = handle_llm_error(e)
            logger.log(error_result.log_level, f"LLM API error in analyze: {e}")
            return f"❌ {error_result.user_message}"

    def store_context(self, user_id: int, context: DiagnosticContext) -> None:
        """Store diagnostic context for potential follow-up.

        Args:
            user_id: Telegram user ID.
            context: DiagnosticContext to store.
        """
        self._cleanup_stale()
        self._pending[user_id] = context

    def _cleanup_stale(self) -> None:
        """Remove expired entries from _pending."""
        now = datetime.now()
        stale = [
            uid for uid, ctx in self._pending.items()
            if ctx.created_at and (now - ctx.created_at).total_seconds() > self._context_expiry_seconds
        ]
        for uid in stale:
            del self._pending[uid]

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
        self._cleanup_stale()
        if not self.has_pending(user_id):
            return None

        context = self._pending.pop(user_id)

        provider = self._resolve_provider()
        if not provider:
            return "❌ AI provider not configured."

        # Sanitize user-controlled inputs to prevent prompt injection
        safe_name = sanitize_container_name(context.container_name)
        safe_logs = sanitize_logs(context.logs)
        safe_summary = sanitize_logs(context.brief_summary or "", max_length=2000)

        # Docker configuration for detailed analysis
        config_parts = []
        if context.volumes:
            vol_list = "\n".join(f"  {v}" for v in context.volumes)
            config_parts.append(f"Volume mounts:\n{vol_list}")
        if context.env_vars:
            env_list = "\n".join(f"  {e}" for e in context.env_vars[:30])
            config_parts.append(f"Environment (non-secret):\n{env_list}")
        if context.ports:
            config_parts.append(f"Ports: {', '.join(context.ports)}")
        config_block = "\n".join(config_parts)

        prompt = f"""Provide detailed diagnostic help for this container issue.

Container: {safe_name}
Status: {"RUNNING" if context.running else context.status.upper()}
Brief analysis: {safe_summary}

Docker Configuration:
{config_block if config_block else "(no configuration data available)"}

Logs:
```
{safe_logs}
```

Provide:
1. Detailed root cause analysis — explain exactly what's failing and why
2. Step-by-step fix instructions — check existing Docker config before suggesting changes
3. How to prevent this in future

Be specific and actionable. Reference the actual log lines and configuration."""

        try:
            response = await provider.chat(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=self._detail_max_tokens,
            )
            result: str = response.text
            return result
        except Exception as e:
            error_result = handle_llm_error(e)
            logger.log(error_result.log_level, f"Claude API error in get_details: {e}")
            return f"❌ {error_result.user_message}"
