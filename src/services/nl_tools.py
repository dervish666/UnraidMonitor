"""Tool definitions for Claude API tool use in natural language chat."""

from typing import Any, TYPE_CHECKING

import docker

from src.state import ContainerStateManager
from src.models import ContainerInfo
from src.utils.sanitize import sanitize_logs

if TYPE_CHECKING:
    from src.services.container_control import ContainerController
    from src.monitors.resource_monitor import ResourceMonitor
    from src.alerts.recent_errors import RecentErrorsBuffer
    from src.unraid.monitors.system_monitor import UnraidSystemMonitor


def get_tool_definitions() -> list[dict[str, Any]]:
    """Return tool definitions for Claude API.

    These tools allow Claude to query container/server status and perform
    actions when the user asks questions in natural language.

    Returns:
        List of tool definitions following Claude's tool-use specification.
    """
    return [
        # Read-only tools
        {
            "name": "get_container_list",
            "description": "Get a list of all Docker containers with their current status (running, stopped, etc.). Use this to see what containers exist on the server.",
            "input_schema": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
        {
            "name": "get_container_status",
            "description": "Get detailed status information for a specific container including state, health, uptime, and restart count.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Container name or partial name to match.",
                    },
                },
                "required": ["name"],
            },
        },
        {
            "name": "get_container_logs",
            "description": "Get recent log output from a container. Useful for diagnosing issues or checking application output.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Container name or partial name to match.",
                    },
                    "lines": {
                        "type": "integer",
                        "description": "Number of log lines to retrieve. Defaults to 50.",
                    },
                },
                "required": ["name"],
            },
        },
        {
            "name": "get_resource_usage",
            "description": "Get CPU and memory usage statistics for containers. If no name is provided, returns stats for all running containers.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Optional container name to get stats for a specific container.",
                    },
                },
                "required": [],
            },
        },
        {
            "name": "get_server_stats",
            "description": "Get overall server statistics including CPU, memory, disk usage, and uptime.",
            "input_schema": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
        {
            "name": "get_array_status",
            "description": "Get Unraid array status including disk health, parity status, and storage capacity.",
            "input_schema": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
        {
            "name": "get_recent_errors",
            "description": "Get recent errors and warnings from container logs. If no name is provided, returns errors from all monitored containers.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Optional container name to filter errors for a specific container.",
                    },
                },
                "required": [],
            },
        },
        # Action tools (require confirmation)
        {
            "name": "restart_container",
            "description": "Restart a Docker container. This will stop and then start the container, which may cause brief downtime.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Container name or partial name to restart.",
                    },
                },
                "required": ["name"],
            },
        },
        {
            "name": "stop_container",
            "description": "Stop a running Docker container. The container will remain stopped until manually started.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Container name or partial name to stop.",
                    },
                },
                "required": ["name"],
            },
        },
        {
            "name": "start_container",
            "description": "Start a stopped Docker container.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Container name or partial name to start.",
                    },
                },
                "required": ["name"],
            },
        },
        {
            "name": "pull_container",
            "description": "Pull the latest image for a container. This downloads any updates but does not restart the container.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Container name or partial name whose image should be pulled.",
                    },
                },
                "required": ["name"],
            },
        },
    ]


# Tool categories for use in executor
READ_ONLY_TOOLS = {
    "get_container_list",
    "get_container_status",
    "get_container_logs",
    "get_resource_usage",
    "get_server_stats",
    "get_array_status",
    "get_recent_errors",
}

ACTION_TOOLS = {
    "restart_container",
    "stop_container",
    "start_container",
    "pull_container",
}


def is_action_tool(tool_name: str) -> bool:
    """Check if a tool requires confirmation before execution.

    Args:
        tool_name: Name of the tool to check.

    Returns:
        True if the tool modifies state and needs confirmation.
    """
    return tool_name in ACTION_TOOLS


def is_read_only_tool(tool_name: str) -> bool:
    """Check if a tool is read-only (no confirmation needed).

    Args:
        tool_name: Name of the tool to check.

    Returns:
        True if the tool only reads data.
    """
    return tool_name in READ_ONLY_TOOLS


class NLToolExecutor:
    """Executes NL tools using existing service code."""

    def __init__(
        self,
        state: ContainerStateManager,
        docker_client: docker.DockerClient,
        protected_containers: list[str] | None = None,
        controller: "ContainerController | None" = None,
        resource_monitor: "ResourceMonitor | None" = None,
        recent_errors_buffer: "RecentErrorsBuffer | None" = None,
        unraid_system_monitor: "UnraidSystemMonitor | None" = None,
        log_max_chars: int = 3000,
    ):
        """Initialize the tool executor.

        Args:
            state: Container state manager for querying container info.
            docker_client: Docker client for container operations.
            protected_containers: List of container names that cannot be modified.
            controller: Container controller for actions (restart, stop, etc.).
            resource_monitor: Resource monitor for CPU/memory stats.
            recent_errors_buffer: Buffer of recent errors from containers.
            unraid_system_monitor: Unraid system monitor for server stats.
        """
        self._state = state
        self._docker = docker_client
        self._protected = set(protected_containers or [])
        self._controller = controller
        self._resource_monitor = resource_monitor
        self._recent_errors = recent_errors_buffer
        self._unraid = unraid_system_monitor
        self._log_max_chars = log_max_chars

    async def execute(self, tool_name: str, args: dict[str, Any]) -> str:
        """Execute a tool and return the result as a string.

        Args:
            tool_name: Name of the tool to execute.
            args: Arguments to pass to the tool.

        Returns:
            Result of the tool execution as a string.
        """
        handler = getattr(self, f"_tool_{tool_name}", None)
        if handler is None:
            return f"Unknown tool: {tool_name}"
        return await handler(args)

    def _resolve_container(self, name: str) -> ContainerInfo | str:
        """Resolve partial container name. Returns ContainerInfo or error string.

        Args:
            name: Full or partial container name.

        Returns:
            ContainerInfo if exactly one match, error string otherwise.
        """
        matches = self._state.find_by_name(name)
        if not matches:
            return f"No container found matching '{name}'"
        if len(matches) > 1:
            names = ", ".join(c.name for c in matches)
            return f"Multiple containers match '{name}': {names}. Please be more specific."
        return matches[0]

    async def _tool_get_container_list(self, args: dict[str, Any]) -> str:
        """Get list of all containers with status."""
        containers = self._state.get_all()
        if not containers:
            return "No containers found."
        lines = []
        running = [c for c in containers if c.status == "running"]
        stopped = [c for c in containers if c.status != "running"]
        if running:
            lines.append(f"Running ({len(running)}):")
            for c in sorted(running, key=lambda x: x.name):
                health = f" [{c.health}]" if c.health else ""
                lines.append(f"  - {c.name}{health}")
        if stopped:
            lines.append(f"\nStopped ({len(stopped)}):")
            for c in sorted(stopped, key=lambda x: x.name):
                lines.append(f"  - {c.name}")
        return "\n".join(lines)

    async def _tool_get_container_status(self, args: dict[str, Any]) -> str:
        """Get detailed status for a specific container."""
        name = args.get("name", "")
        resolved = self._resolve_container(name)
        if isinstance(resolved, str):
            return resolved
        c = resolved
        lines = [f"Container: {c.name}"]
        lines.append(f"Status: {c.status}")
        if c.health:
            lines.append(f"Health: {c.health}")
        lines.append(f"Image: {c.image}")
        if c.uptime_seconds is not None:
            hours = c.uptime_seconds // 3600
            minutes = (c.uptime_seconds % 3600) // 60
            if hours > 0:
                lines.append(f"Uptime: {hours}h {minutes}m")
            else:
                lines.append(f"Uptime: {minutes}m")
        return "\n".join(lines)

    async def _tool_get_container_logs(self, args: dict[str, Any]) -> str:
        """Get recent logs from a container."""
        name = args.get("name", "")
        lines = min(args.get("lines", 50), 200)
        resolved = self._resolve_container(name)
        if isinstance(resolved, str):
            return resolved
        try:
            container = self._docker.containers.get(resolved.name)
            log_bytes = container.logs(tail=lines, timestamps=False)
            logs = log_bytes.decode("utf-8", errors="replace")
            if not logs.strip():
                return f"No recent logs for {resolved.name}"
            if len(logs) > self._log_max_chars:
                logs = logs[-self._log_max_chars:]
                logs = f"... (truncated)\n{logs}"
            # Sanitize logs to prevent prompt injection via tool results
            safe_logs = sanitize_logs(logs, max_length=self._log_max_chars)
            return f"Logs for {resolved.name}:\n{safe_logs}"
        except docker.errors.NotFound:
            return f"Container '{resolved.name}' not found"
        except Exception as e:
            return f"Error getting logs: {e}"

    async def _tool_get_resource_usage(self, args: dict[str, Any]) -> str:
        """Get CPU and memory usage for containers."""
        if self._resource_monitor is None:
            return "Resource monitoring not available."

        name = args.get("name")
        if name:
            # Get stats for specific container
            resolved = self._resolve_container(name)
            if isinstance(resolved, str):
                return resolved

            stats = await self._resource_monitor.get_container_stats(resolved.name)
            if stats is None:
                return f"Container '{resolved.name}' is not running or stats unavailable."

            return (
                f"Resource usage for {stats.name}:\n"
                f"  CPU: {stats.cpu_percent:.1f}%\n"
                f"  Memory: {stats.memory_percent:.1f}% "
                f"({stats.memory_display} / {stats.memory_limit_display})"
            )

        # Get stats for all running containers
        all_stats = await self._resource_monitor.get_all_stats()
        if not all_stats:
            return "No running containers found."

        # Sort by CPU usage descending
        all_stats.sort(key=lambda s: s.cpu_percent, reverse=True)

        lines = ["Resource usage (sorted by CPU):"]
        for stats in all_stats:
            lines.append(
                f"  {stats.name}: CPU {stats.cpu_percent:.1f}%, "
                f"Mem {stats.memory_percent:.1f}% ({stats.memory_display})"
            )
        return "\n".join(lines)

    async def _tool_get_server_stats(self, args: dict[str, Any]) -> str:
        """Get overall server statistics."""
        if self._unraid is None:
            return "Unraid monitoring not configured."

        metrics = await self._unraid.get_current_metrics()
        if metrics is None:
            return "Failed to retrieve server metrics."

        lines = ["Server Statistics:"]

        # CPU info
        cpu_percent = metrics.get("cpu_percent", 0)
        cpu_temp = metrics.get("cpu_temperature", 0)
        lines.append(f"  CPU: {cpu_percent:.1f}%")
        if cpu_temp:
            lines.append(f"  CPU Temperature: {cpu_temp:.1f}°C")

        # Memory info
        memory_percent = metrics.get("memory_percent", 0)
        memory_used = metrics.get("memory_used", 0)
        memory_total = metrics.get("memory_total", 0)
        if memory_total > 0:
            used_gb = memory_used / (1024**3)
            total_gb = memory_total / (1024**3)
            lines.append(f"  Memory: {memory_percent:.1f}% ({used_gb:.1f}GB / {total_gb:.1f}GB)")
        else:
            lines.append(f"  Memory: {memory_percent:.1f}%")

        # Uptime if available
        uptime = metrics.get("uptime")
        if uptime:
            lines.append(f"  Uptime: {uptime}")

        return "\n".join(lines)

    async def _tool_get_array_status(self, args: dict[str, Any]) -> str:
        """Get Unraid array status."""
        if self._unraid is None:
            return "Unraid monitoring not configured."

        status = await self._unraid.get_array_status()
        if status is None:
            return "Failed to retrieve array status."

        lines = ["Array Status:"]

        # Array state
        state = status.get("state", "unknown")
        lines.append(f"  State: {state}")

        # Parity info
        parity_status = status.get("parity_status")
        if parity_status:
            lines.append(f"  Parity: {parity_status}")

        parity_check = status.get("parity_check_progress")
        if parity_check is not None:
            lines.append(f"  Parity Check: {parity_check}% complete")

        # Disk info
        disks = status.get("disks", [])
        if disks:
            lines.append(f"  Disks: {len(disks)} total")
            unhealthy = [d for d in disks if d.get("status") != "healthy"]
            if unhealthy:
                lines.append(f"  Warning: {len(unhealthy)} disk(s) have issues:")
                for disk in unhealthy:
                    lines.append(f"    - {disk.get('name', 'unknown')}: {disk.get('status', 'unknown')}")
            else:
                lines.append("  All disks healthy")

        # Capacity info
        used = status.get("used_bytes", 0)
        total = status.get("total_bytes", 0)
        if total > 0:
            used_tb = used / (1024**4)
            total_tb = total / (1024**4)
            percent = (used / total) * 100
            lines.append(f"  Capacity: {used_tb:.1f}TB / {total_tb:.1f}TB ({percent:.1f}% used)")

        return "\n".join(lines)

    async def _tool_get_recent_errors(self, args: dict[str, Any]) -> str:
        """Get recent errors from container logs."""
        if self._recent_errors is None:
            return "Error tracking not available."

        name = args.get("name")
        if name:
            # Get errors for specific container
            resolved = self._resolve_container(name)
            if isinstance(resolved, str):
                return resolved

            errors = self._recent_errors.get_recent(resolved.name)
            if not errors:
                return f"No recent errors for {resolved.name}."

            lines = [f"Recent errors for {resolved.name} ({len(errors)} unique):"]
            for i, error in enumerate(errors[:10], 1):  # Limit to 10
                # Truncate long error messages
                error_display = error[:200] + "..." if len(error) > 200 else error
                lines.append(f"  {i}. {error_display}")
            if len(errors) > 10:
                lines.append(f"  ... and {len(errors) - 10} more")
            return "\n".join(lines)

        # Get errors for all containers
        all_containers = self._state.get_all()
        containers_with_errors = []

        for container in all_containers:
            errors = self._recent_errors.get_recent(container.name)
            if errors:
                containers_with_errors.append((container.name, errors))

        if not containers_with_errors:
            return "No recent errors detected across any containers."

        lines = ["Recent errors by container:"]
        for container_name, errors in sorted(containers_with_errors, key=lambda x: -len(x[1])):
            lines.append(f"\n{container_name} ({len(errors)} errors):")
            for error in errors[:3]:  # Show first 3 per container
                error_display = error[:100] + "..." if len(error) > 100 else error
                lines.append(f"  - {error_display}")
            if len(errors) > 3:
                lines.append(f"  ... and {len(errors) - 3} more")

        return "\n".join(lines)

    async def _tool_restart_container(self, args: dict[str, Any]) -> str:
        """Request container restart (requires confirmation)."""
        name = args.get("name", "")
        resolved = self._resolve_container(name)
        if isinstance(resolved, str):
            return resolved

        if resolved.name in self._protected:
            return f"Cannot restart {resolved.name} - it's a protected container."

        # Return confirmation request (actual restart happens after user confirms)
        return f"CONFIRMATION_NEEDED:restart:{resolved.name}"

    async def _tool_stop_container(self, args: dict[str, Any]) -> str:
        """Request container stop (requires confirmation)."""
        name = args.get("name", "")
        resolved = self._resolve_container(name)
        if isinstance(resolved, str):
            return resolved

        if resolved.name in self._protected:
            return f"Cannot stop {resolved.name} - it's a protected container."

        return f"CONFIRMATION_NEEDED:stop:{resolved.name}"

    async def _tool_start_container(self, args: dict[str, Any]) -> str:
        """Start a container (executes immediately - safe operation)."""
        name = args.get("name", "")
        resolved = self._resolve_container(name)
        if isinstance(resolved, str):
            return resolved

        if resolved.name in self._protected:
            return f"Cannot start {resolved.name} - it's a protected container."

        if self._controller is None:
            return "Container control not available."

        result = await self._controller.start(resolved.name)
        return result

    async def _tool_pull_container(self, args: dict[str, Any]) -> str:
        """Request container pull/update (requires confirmation)."""
        name = args.get("name", "")
        resolved = self._resolve_container(name)
        if isinstance(resolved, str):
            return resolved

        if resolved.name in self._protected:
            return f"Cannot update {resolved.name} - it's a protected container."

        return f"CONFIRMATION_NEEDED:pull:{resolved.name}"
