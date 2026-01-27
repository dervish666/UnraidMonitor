"""Tool definitions for Claude API tool use in natural language chat."""

from typing import Any


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
