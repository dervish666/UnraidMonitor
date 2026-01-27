# Natural Language Chat Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add conversational natural language interface so users can ask questions like "what's wrong with plex?" instead of using commands.

**Architecture:** Non-command messages route to `NLProcessor` which uses Claude's tool-use API to gather data via `NLTools`, then responds conversationally. Action tools trigger the existing confirmation flow.

**Tech Stack:** anthropic SDK (already installed), aiogram 3.x, Docker SDK

---

## Task 1: Conversation Memory Data Structure

**Files:**
- Create: `src/services/nl_processor.py`
- Test: `tests/test_nl_processor.py`

**Step 1: Write the failing test for ConversationMemory**

```python
# tests/test_nl_processor.py
import pytest
from datetime import datetime, timedelta
from src.services.nl_processor import ConversationMemory


class TestConversationMemory:
    def test_add_exchange_stores_messages(self):
        memory = ConversationMemory(user_id=123)
        memory.add_exchange("what's wrong?", "everything is fine")

        assert len(memory.messages) == 2
        assert memory.messages[0]["role"] == "user"
        assert memory.messages[0]["content"] == "what's wrong?"
        assert memory.messages[1]["role"] == "assistant"
        assert memory.messages[1]["content"] == "everything is fine"

    def test_add_exchange_trims_to_max(self):
        memory = ConversationMemory(user_id=123, max_exchanges=2)

        memory.add_exchange("q1", "a1")
        memory.add_exchange("q2", "a2")
        memory.add_exchange("q3", "a3")  # Should push out q1/a1

        assert len(memory.messages) == 4  # 2 exchanges = 4 messages
        assert memory.messages[0]["content"] == "q2"

    def test_get_messages_returns_copy(self):
        memory = ConversationMemory(user_id=123)
        memory.add_exchange("q", "a")

        messages = memory.get_messages()
        messages.append({"role": "user", "content": "injected"})

        assert len(memory.messages) == 2  # Original unchanged

    def test_clear_removes_all_messages(self):
        memory = ConversationMemory(user_id=123)
        memory.add_exchange("q", "a")
        memory.clear()

        assert len(memory.messages) == 0

    def test_pending_action_initially_none(self):
        memory = ConversationMemory(user_id=123)
        assert memory.pending_action is None

    def test_set_and_get_pending_action(self):
        memory = ConversationMemory(user_id=123)
        memory.pending_action = {"action": "restart", "container": "plex"}

        assert memory.pending_action == {"action": "restart", "container": "plex"}

    def test_clear_also_clears_pending_action(self):
        memory = ConversationMemory(user_id=123)
        memory.pending_action = {"action": "restart", "container": "plex"}
        memory.clear()

        assert memory.pending_action is None
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_nl_processor.py -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'src.services.nl_processor'"

**Step 3: Write minimal implementation**

```python
# src/services/nl_processor.py
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class ConversationMemory:
    """Stores conversation history for a single user."""

    user_id: int
    max_exchanges: int = 5
    messages: list[dict[str, str]] = field(default_factory=list)
    last_activity: datetime = field(default_factory=datetime.now)
    pending_action: dict[str, Any] | None = None

    def add_exchange(self, user_message: str, assistant_message: str) -> None:
        """Add a user/assistant exchange, trimming old messages if needed."""
        self.messages.append({"role": "user", "content": user_message})
        self.messages.append({"role": "assistant", "content": assistant_message})
        self.last_activity = datetime.now()

        # Trim to max_exchanges (each exchange = 2 messages)
        max_messages = self.max_exchanges * 2
        if len(self.messages) > max_messages:
            self.messages = self.messages[-max_messages:]

    def get_messages(self) -> list[dict[str, str]]:
        """Return a copy of messages for use in API calls."""
        return self.messages.copy()

    def clear(self) -> None:
        """Clear all messages and pending action."""
        self.messages = []
        self.pending_action = None
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_nl_processor.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/services/nl_processor.py tests/test_nl_processor.py
git commit -m "feat(nl): add ConversationMemory data structure"
```

---

## Task 2: Memory Store Manager

**Files:**
- Modify: `src/services/nl_processor.py`
- Test: `tests/test_nl_processor.py`

**Step 1: Write the failing test for MemoryStore**

```python
# Add to tests/test_nl_processor.py
from src.services.nl_processor import ConversationMemory, MemoryStore


class TestMemoryStore:
    def test_get_or_create_creates_new_memory(self):
        store = MemoryStore()
        memory = store.get_or_create(123)

        assert memory.user_id == 123
        assert len(memory.messages) == 0

    def test_get_or_create_returns_existing_memory(self):
        store = MemoryStore()
        memory1 = store.get_or_create(123)
        memory1.add_exchange("q", "a")

        memory2 = store.get_or_create(123)

        assert memory2 is memory1
        assert len(memory2.messages) == 2

    def test_get_returns_none_for_unknown_user(self):
        store = MemoryStore()
        assert store.get(999) is None

    def test_clear_user_removes_memory(self):
        store = MemoryStore()
        store.get_or_create(123)
        store.clear_user(123)

        assert store.get(123) is None
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_nl_processor.py::TestMemoryStore -v`
Expected: FAIL with "cannot import name 'MemoryStore'"

**Step 3: Write minimal implementation**

```python
# Add to src/services/nl_processor.py

class MemoryStore:
    """Stores conversation memories for all users."""

    def __init__(self, max_exchanges: int = 5):
        self._memories: dict[int, ConversationMemory] = {}
        self._max_exchanges = max_exchanges

    def get_or_create(self, user_id: int) -> ConversationMemory:
        """Get existing memory or create new one for user."""
        if user_id not in self._memories:
            self._memories[user_id] = ConversationMemory(
                user_id=user_id,
                max_exchanges=self._max_exchanges,
            )
        return self._memories[user_id]

    def get(self, user_id: int) -> ConversationMemory | None:
        """Get memory for user if it exists."""
        return self._memories.get(user_id)

    def clear_user(self, user_id: int) -> None:
        """Remove memory for a user."""
        self._memories.pop(user_id, None)
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_nl_processor.py::TestMemoryStore -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/services/nl_processor.py tests/test_nl_processor.py
git commit -m "feat(nl): add MemoryStore for managing user memories"
```

---

## Task 3: Tool Definitions Schema

**Files:**
- Create: `src/services/nl_tools.py`
- Test: `tests/test_nl_tools.py`

**Step 1: Write the failing test for tool definitions**

```python
# tests/test_nl_tools.py
import pytest
from src.services.nl_tools import get_tool_definitions


class TestToolDefinitions:
    def test_get_tool_definitions_returns_list(self):
        tools = get_tool_definitions()
        assert isinstance(tools, list)
        assert len(tools) > 0

    def test_all_tools_have_required_fields(self):
        tools = get_tool_definitions()
        for tool in tools:
            assert "name" in tool
            assert "description" in tool
            assert "input_schema" in tool
            assert tool["input_schema"]["type"] == "object"

    def test_read_only_tools_exist(self):
        tools = get_tool_definitions()
        tool_names = [t["name"] for t in tools]

        assert "get_container_list" in tool_names
        assert "get_container_status" in tool_names
        assert "get_container_logs" in tool_names
        assert "get_resource_usage" in tool_names
        assert "get_recent_errors" in tool_names

    def test_action_tools_exist(self):
        tools = get_tool_definitions()
        tool_names = [t["name"] for t in tools]

        assert "restart_container" in tool_names
        assert "stop_container" in tool_names
        assert "start_container" in tool_names
        assert "pull_container" in tool_names

    def test_get_container_logs_has_optional_lines_param(self):
        tools = get_tool_definitions()
        logs_tool = next(t for t in tools if t["name"] == "get_container_logs")

        schema = logs_tool["input_schema"]
        assert "name" in schema["required"]
        assert "lines" not in schema.get("required", [])
        assert "lines" in schema["properties"]
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_nl_tools.py -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'src.services.nl_tools'"

**Step 3: Write minimal implementation**

```python
# src/services/nl_tools.py
"""Tool definitions and implementations for natural language processing."""


def get_tool_definitions() -> list[dict]:
    """Return Claude tool definitions for NL processor."""
    return [
        # Read-only tools
        {
            "name": "get_container_list",
            "description": "Get a list of all containers with their current status (running/stopped).",
            "input_schema": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
        {
            "name": "get_container_status",
            "description": "Get detailed status for a specific container including uptime, image, restart count, and health.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Container name (partial matching supported, e.g., 'plex' or 'rad' for 'radarr')",
                    },
                },
                "required": ["name"],
            },
        },
        {
            "name": "get_container_logs",
            "description": "Get recent log lines from a container.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Container name (partial matching supported)",
                    },
                    "lines": {
                        "type": "integer",
                        "description": "Number of log lines to retrieve (default: 50, max: 200)",
                        "default": 50,
                    },
                },
                "required": ["name"],
            },
        },
        {
            "name": "get_resource_usage",
            "description": "Get CPU and memory usage for containers. If no name given, returns usage for all containers.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Container name (optional - omit for all containers)",
                    },
                },
                "required": [],
            },
        },
        {
            "name": "get_server_stats",
            "description": "Get Unraid server stats including CPU, memory, and temperatures.",
            "input_schema": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
        {
            "name": "get_array_status",
            "description": "Get Unraid array status including disk health and usage.",
            "input_schema": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
        {
            "name": "get_recent_errors",
            "description": "Get recent logged errors. If no name given, returns errors for all watched containers.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Container name (optional - omit for all containers)",
                    },
                },
                "required": [],
            },
        },
        # Action tools
        {
            "name": "restart_container",
            "description": "Restart a container. Requires user confirmation before executing.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Container name (partial matching supported)",
                    },
                },
                "required": ["name"],
            },
        },
        {
            "name": "stop_container",
            "description": "Stop a running container. Requires user confirmation before executing.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Container name (partial matching supported)",
                    },
                },
                "required": ["name"],
            },
        },
        {
            "name": "start_container",
            "description": "Start a stopped container. Executes immediately (safe operation).",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Container name (partial matching supported)",
                    },
                },
                "required": ["name"],
            },
        },
        {
            "name": "pull_container",
            "description": "Pull the latest image and recreate the container. Requires user confirmation.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Container name (partial matching supported)",
                    },
                },
                "required": ["name"],
            },
        },
    ]
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_nl_tools.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/services/nl_tools.py tests/test_nl_tools.py
git commit -m "feat(nl): add tool definitions for Claude API"
```

---

## Task 4: Tool Executor - Read-only Tools

**Files:**
- Modify: `src/services/nl_tools.py`
- Test: `tests/test_nl_tools.py`

**Step 1: Write failing tests for read-only tool execution**

```python
# Add to tests/test_nl_tools.py
from unittest.mock import Mock, MagicMock
from src.services.nl_tools import NLToolExecutor
from src.models import ContainerInfo
from datetime import datetime, timezone


@pytest.fixture
def mock_state():
    state = Mock()
    state.get_all.return_value = [
        ContainerInfo(name="plex", status="running", health="healthy", image="plexinc/pms-docker", started_at=datetime.now(timezone.utc)),
        ContainerInfo(name="radarr", status="running", health=None, image="linuxserver/radarr", started_at=datetime.now(timezone.utc)),
        ContainerInfo(name="sonarr", status="exited", health=None, image="linuxserver/sonarr", started_at=None),
    ]
    state.find_by_name.return_value = [
        ContainerInfo(name="plex", status="running", health="healthy", image="plexinc/pms-docker", started_at=datetime.now(timezone.utc)),
    ]
    return state


@pytest.fixture
def mock_docker():
    docker = Mock()
    container = Mock()
    container.logs.return_value = b"[INFO] Server started\n[ERROR] Connection failed\n"
    docker.containers.get.return_value = container
    return docker


@pytest.fixture
def executor(mock_state, mock_docker):
    return NLToolExecutor(
        state=mock_state,
        docker_client=mock_docker,
        protected_containers=["mariadb"],
    )


class TestNLToolExecutor:
    @pytest.mark.asyncio
    async def test_get_container_list(self, executor):
        result = await executor.execute("get_container_list", {})

        assert "plex" in result
        assert "running" in result.lower()
        assert "radarr" in result
        assert "sonarr" in result

    @pytest.mark.asyncio
    async def test_get_container_status_found(self, executor):
        result = await executor.execute("get_container_status", {"name": "plex"})

        assert "plex" in result
        assert "running" in result.lower()

    @pytest.mark.asyncio
    async def test_get_container_status_not_found(self, executor, mock_state):
        mock_state.find_by_name.return_value = []
        result = await executor.execute("get_container_status", {"name": "notexist"})

        assert "not found" in result.lower() or "no container" in result.lower()

    @pytest.mark.asyncio
    async def test_get_container_status_ambiguous(self, executor, mock_state):
        mock_state.find_by_name.return_value = [
            ContainerInfo(name="radarr", status="running", health=None, image="img", started_at=None),
            ContainerInfo(name="radarr-sync", status="running", health=None, image="img", started_at=None),
        ]
        result = await executor.execute("get_container_status", {"name": "rad"})

        assert "multiple" in result.lower() or "radarr" in result and "radarr-sync" in result

    @pytest.mark.asyncio
    async def test_get_container_logs(self, executor):
        result = await executor.execute("get_container_logs", {"name": "plex", "lines": 10})

        assert "Server started" in result or "Connection failed" in result

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self, executor):
        result = await executor.execute("unknown_tool", {})

        assert "unknown" in result.lower() or "not found" in result.lower()
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_nl_tools.py::TestNLToolExecutor -v`
Expected: FAIL with "cannot import name 'NLToolExecutor'"

**Step 3: Write minimal implementation**

```python
# Add to src/services/nl_tools.py
from typing import Any
import docker

from src.state import ContainerStateManager
from src.models import ContainerInfo


class NLToolExecutor:
    """Executes NL tools using existing service code."""

    def __init__(
        self,
        state: ContainerStateManager,
        docker_client: docker.DockerClient,
        protected_containers: list[str] | None = None,
        controller: Any | None = None,
        resource_monitor: Any | None = None,
        recent_errors_buffer: Any | None = None,
        unraid_system_monitor: Any | None = None,
    ):
        self._state = state
        self._docker = docker_client
        self._protected = set(protected_containers or [])
        self._controller = controller
        self._resource_monitor = resource_monitor
        self._recent_errors = recent_errors_buffer
        self._unraid = unraid_system_monitor

    async def execute(self, tool_name: str, args: dict[str, Any]) -> str:
        """Execute a tool and return the result as a string."""
        handler = getattr(self, f"_tool_{tool_name}", None)
        if handler is None:
            return f"Unknown tool: {tool_name}"
        return await handler(args)

    def _resolve_container(self, name: str) -> ContainerInfo | str:
        """Resolve partial container name. Returns ContainerInfo or error string."""
        matches = self._state.find_by_name(name)
        if not matches:
            return f"No container found matching '{name}'"
        if len(matches) > 1:
            names = ", ".join(c.name for c in matches)
            return f"Multiple containers match '{name}': {names}. Please be more specific."
        return matches[0]

    async def _tool_get_container_list(self, args: dict) -> str:
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

    async def _tool_get_container_status(self, args: dict) -> str:
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

    async def _tool_get_container_logs(self, args: dict) -> str:
        """Get recent logs from a container."""
        name = args.get("name", "")
        lines = min(args.get("lines", 50), 200)  # Cap at 200

        resolved = self._resolve_container(name)
        if isinstance(resolved, str):
            return resolved

        try:
            container = self._docker.containers.get(resolved.name)
            log_bytes = container.logs(tail=lines, timestamps=False)
            logs = log_bytes.decode("utf-8", errors="replace")

            if not logs.strip():
                return f"No recent logs for {resolved.name}"

            # Truncate if too long
            if len(logs) > 3000:
                logs = logs[-3000:]
                return f"... (truncated)\n{logs}"

            return f"Logs for {resolved.name}:\n{logs}"
        except docker.errors.NotFound:
            return f"Container '{resolved.name}' not found"
        except Exception as e:
            return f"Error getting logs: {e}"

    async def _tool_get_resource_usage(self, args: dict) -> str:
        """Get CPU/memory usage for containers."""
        if self._resource_monitor is None:
            return "Resource monitoring not available."

        name = args.get("name")
        # TODO: Implement when wiring up resource monitor
        return "Resource usage data not yet implemented."

    async def _tool_get_server_stats(self, args: dict) -> str:
        """Get Unraid server stats."""
        if self._unraid is None:
            return "Unraid monitoring not configured."
        # TODO: Implement when wiring up unraid monitor
        return "Server stats not yet implemented."

    async def _tool_get_array_status(self, args: dict) -> str:
        """Get Unraid array status."""
        if self._unraid is None:
            return "Unraid monitoring not configured."
        # TODO: Implement when wiring up unraid monitor
        return "Array status not yet implemented."

    async def _tool_get_recent_errors(self, args: dict) -> str:
        """Get recent errors from log watching."""
        if self._recent_errors is None:
            return "Error tracking not available."

        name = args.get("name")
        # TODO: Implement when wiring up recent errors buffer
        return "Recent errors not yet implemented."
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_nl_tools.py::TestNLToolExecutor -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/services/nl_tools.py tests/test_nl_tools.py
git commit -m "feat(nl): add NLToolExecutor for read-only tools"
```

---

## Task 5: Tool Executor - Action Tools

**Files:**
- Modify: `src/services/nl_tools.py`
- Test: `tests/test_nl_tools.py`

**Step 1: Write failing tests for action tools**

```python
# Add to tests/test_nl_tools.py

class TestNLToolExecutorActions:
    @pytest.fixture
    def mock_controller(self):
        controller = Mock()
        controller.is_protected.return_value = False
        controller.start = Mock(return_value="✅ plex started")
        return controller

    @pytest.fixture
    def executor_with_controller(self, mock_state, mock_docker, mock_controller):
        return NLToolExecutor(
            state=mock_state,
            docker_client=mock_docker,
            protected_containers=["mariadb"],
            controller=mock_controller,
        )

    @pytest.mark.asyncio
    async def test_restart_returns_confirmation_needed(self, executor_with_controller):
        result = await executor_with_controller.execute("restart_container", {"name": "plex"})

        assert "confirm" in result.lower() or "confirmation" in result.lower()

    @pytest.mark.asyncio
    async def test_restart_protected_returns_error(self, executor_with_controller, mock_state):
        mock_state.find_by_name.return_value = [
            ContainerInfo(name="mariadb", status="running", health=None, image="img", started_at=None),
        ]
        result = await executor_with_controller.execute("restart_container", {"name": "mariadb"})

        assert "protected" in result.lower() or "cannot" in result.lower()

    @pytest.mark.asyncio
    async def test_start_executes_immediately(self, executor_with_controller, mock_controller):
        mock_controller.start = Mock(return_value="✅ plex started")
        # Make start async
        import asyncio
        mock_controller.start = Mock(return_value=asyncio.coroutine(lambda: "✅ plex started")())

        result = await executor_with_controller.execute("start_container", {"name": "plex"})

        # start_container should execute immediately, not require confirmation
        assert "started" in result.lower() or "already running" in result.lower() or "confirm" not in result.lower()

    @pytest.mark.asyncio
    async def test_stop_returns_confirmation_needed(self, executor_with_controller):
        result = await executor_with_controller.execute("stop_container", {"name": "plex"})

        assert "confirm" in result.lower()

    @pytest.mark.asyncio
    async def test_pull_returns_confirmation_needed(self, executor_with_controller):
        result = await executor_with_controller.execute("pull_container", {"name": "plex"})

        assert "confirm" in result.lower()
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_nl_tools.py::TestNLToolExecutorActions -v`
Expected: FAIL

**Step 3: Write implementation for action tools**

```python
# Add these methods to NLToolExecutor class in src/services/nl_tools.py

    async def _tool_restart_container(self, args: dict) -> str:
        """Request container restart (requires confirmation)."""
        name = args.get("name", "")
        resolved = self._resolve_container(name)
        if isinstance(resolved, str):
            return resolved

        if resolved.name in self._protected:
            return f"Cannot restart {resolved.name} - it's a protected container."

        # Return confirmation request (actual restart happens after user confirms)
        return f"CONFIRMATION_NEEDED:restart:{resolved.name}"

    async def _tool_stop_container(self, args: dict) -> str:
        """Request container stop (requires confirmation)."""
        name = args.get("name", "")
        resolved = self._resolve_container(name)
        if isinstance(resolved, str):
            return resolved

        if resolved.name in self._protected:
            return f"Cannot stop {resolved.name} - it's a protected container."

        return f"CONFIRMATION_NEEDED:stop:{resolved.name}"

    async def _tool_start_container(self, args: dict) -> str:
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

    async def _tool_pull_container(self, args: dict) -> str:
        """Request container pull/update (requires confirmation)."""
        name = args.get("name", "")
        resolved = self._resolve_container(name)
        if isinstance(resolved, str):
            return resolved

        if resolved.name in self._protected:
            return f"Cannot update {resolved.name} - it's a protected container."

        return f"CONFIRMATION_NEEDED:pull:{resolved.name}"
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_nl_tools.py::TestNLToolExecutorActions -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/services/nl_tools.py tests/test_nl_tools.py
git commit -m "feat(nl): add action tools with confirmation flow"
```

---

## Task 6: NL Processor - Claude API Integration

**Files:**
- Modify: `src/services/nl_processor.py`
- Test: `tests/test_nl_processor.py`

**Step 1: Write failing tests for NLProcessor**

```python
# Add to tests/test_nl_processor.py
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from src.services.nl_processor import NLProcessor


class TestNLProcessor:
    @pytest.fixture
    def mock_anthropic(self):
        client = Mock()
        # Mock a simple response with no tool use
        response = Mock()
        response.stop_reason = "end_turn"
        response.content = [Mock(type="text", text="Everything looks fine!")]
        client.messages.create = Mock(return_value=response)
        return client

    @pytest.fixture
    def mock_executor(self):
        executor = AsyncMock()
        executor.execute = AsyncMock(return_value="Container: plex\nStatus: running")
        return executor

    @pytest.fixture
    def processor(self, mock_anthropic, mock_executor):
        return NLProcessor(
            anthropic_client=mock_anthropic,
            tool_executor=mock_executor,
        )

    @pytest.mark.asyncio
    async def test_process_simple_query(self, processor):
        result = await processor.process(user_id=123, message="how's everything?")

        assert result.response is not None
        assert len(result.response) > 0

    @pytest.mark.asyncio
    async def test_process_stores_in_memory(self, processor):
        await processor.process(user_id=123, message="check plex")

        memory = processor.memory_store.get(123)
        assert memory is not None
        assert len(memory.messages) == 2  # user + assistant

    @pytest.mark.asyncio
    async def test_process_uses_conversation_history(self, processor, mock_anthropic):
        # First message
        await processor.process(user_id=123, message="check plex")
        # Second message (should include history)
        await processor.process(user_id=123, message="restart it")

        # Check that the second call included history
        calls = mock_anthropic.messages.create.call_args_list
        assert len(calls) == 2
        # Second call should have more messages (history + new)
        second_call_messages = calls[1][1]["messages"]
        assert len(second_call_messages) >= 2

    @pytest.mark.asyncio
    async def test_process_returns_pending_action_for_confirmation(self, processor, mock_anthropic, mock_executor):
        # Mock tool use response
        tool_use_block = Mock(type="tool_use", id="123", name="restart_container", input={"name": "plex"})
        response1 = Mock(stop_reason="tool_use", content=[tool_use_block])

        # Mock executor returning confirmation needed
        mock_executor.execute = AsyncMock(return_value="CONFIRMATION_NEEDED:restart:plex")

        # Mock final response
        response2 = Mock(stop_reason="end_turn", content=[Mock(type="text", text="I can restart plex for you.")])
        mock_anthropic.messages.create = Mock(side_effect=[response1, response2])

        result = await processor.process(user_id=123, message="restart plex")

        assert result.pending_action is not None
        assert result.pending_action["action"] == "restart"
        assert result.pending_action["container"] == "plex"

    @pytest.mark.asyncio
    async def test_process_without_anthropic_returns_error(self):
        processor = NLProcessor(anthropic_client=None, tool_executor=Mock())
        result = await processor.process(user_id=123, message="hello")

        assert "not configured" in result.response.lower() or "not available" in result.response.lower()
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_nl_processor.py::TestNLProcessor -v`
Expected: FAIL with "cannot import name 'NLProcessor'"

**Step 3: Write implementation**

```python
# Add to src/services/nl_processor.py
import logging
from dataclasses import dataclass
from typing import Any

from src.services.nl_tools import get_tool_definitions

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an assistant for monitoring an Unraid server. You help users understand what's happening with their Docker containers and server, and can take actions to fix problems.

## Your capabilities
- Check container status, logs, and resource usage
- View server stats (CPU, memory, temperatures)
- Check array and disk health
- Restart, stop, start, or pull containers (with user confirmation)

## Guidelines
- Be concise. Users are on mobile Telegram.
- When investigating issues, gather relevant data before responding.
- For "what's wrong" questions: check status, recent errors, and logs.
- For performance questions: check resource usage first.
- Suggest actions when appropriate, but explain why.
- If a container is protected, explain you can't control it.
- If you can't help, suggest relevant /commands.

## Container name matching
Partial names work: "plex", "rad" for "radarr", etc."""


@dataclass
class ProcessResult:
    """Result from processing a natural language message."""
    response: str
    pending_action: dict[str, Any] | None = None


class NLProcessor:
    """Processes natural language messages using Claude API with tools."""

    def __init__(
        self,
        anthropic_client: Any | None,
        tool_executor: Any,
        model: str = "claude-sonnet-4-20250514",
    ):
        self._anthropic = anthropic_client
        self._executor = tool_executor
        self._model = model
        self.memory_store = MemoryStore()

    async def process(self, user_id: int, message: str) -> ProcessResult:
        """Process a natural language message and return a response."""
        if self._anthropic is None:
            return ProcessResult(
                response="Sorry, natural language processing is not configured. Please use /commands instead."
            )

        memory = self.memory_store.get_or_create(user_id)

        # Clear any pending action when new message arrives
        memory.pending_action = None

        # Build messages with history
        messages = memory.get_messages()
        messages.append({"role": "user", "content": message})

        try:
            response_text, pending_action = await self._call_claude(messages)

            # Store the exchange
            memory.add_exchange(message, response_text)

            # Store pending action if any
            if pending_action:
                memory.pending_action = pending_action

            return ProcessResult(response=response_text, pending_action=pending_action)

        except Exception as e:
            logger.error(f"NL processing error: {e}")
            return ProcessResult(
                response="Sorry, I couldn't process that right now. Try using /commands instead."
            )

    async def _call_claude(self, messages: list[dict]) -> tuple[str, dict | None]:
        """Call Claude API with tool support. Returns (response_text, pending_action)."""
        tools = get_tool_definitions()
        pending_action = None

        # Initial API call
        response = self._anthropic.messages.create(
            model=self._model,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=tools,
            messages=messages,
        )

        # Handle tool use loop
        while response.stop_reason == "tool_use":
            # Extract tool calls
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    tool_name = block.name
                    tool_input = block.input

                    # Execute the tool
                    result = await self._executor.execute(tool_name, tool_input)

                    # Check for confirmation needed
                    if result.startswith("CONFIRMATION_NEEDED:"):
                        _, action, container = result.split(":", 2)
                        pending_action = {"action": action, "container": container}
                        result = f"Confirmation needed to {action} {container}."

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })

            # Continue conversation with tool results
            messages = messages + [
                {"role": "assistant", "content": response.content},
                {"role": "user", "content": tool_results},
            ]

            response = self._anthropic.messages.create(
                model=self._model,
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                tools=tools,
                messages=messages,
            )

        # Extract final text response
        text_parts = [block.text for block in response.content if block.type == "text"]
        response_text = "\n".join(text_parts) if text_parts else "I couldn't generate a response."

        return response_text, pending_action
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_nl_processor.py::TestNLProcessor -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/services/nl_processor.py tests/test_nl_processor.py
git commit -m "feat(nl): add NLProcessor with Claude API integration"
```

---

## Task 7: Telegram Handler for Non-Commands

**Files:**
- Create: `src/bot/nl_handler.py`
- Test: `tests/test_nl_handler.py`

**Step 1: Write failing tests**

```python
# tests/test_nl_handler.py
import pytest
from unittest.mock import Mock, AsyncMock, MagicMock
from aiogram.types import Message, User, Chat
from src.bot.nl_handler import create_nl_handler, NLFilter


@pytest.fixture
def mock_message():
    message = Mock(spec=Message)
    message.text = "what's wrong with plex?"
    message.from_user = Mock(spec=User)
    message.from_user.id = 123
    message.chat = Mock(spec=Chat)
    message.chat.id = 456
    message.answer = AsyncMock()
    message.reply = AsyncMock()
    return message


@pytest.fixture
def mock_processor():
    processor = Mock()
    processor.process = AsyncMock()
    return processor


class TestNLFilter:
    @pytest.mark.asyncio
    async def test_filter_passes_non_command_text(self, mock_message):
        filter = NLFilter()
        mock_message.text = "what's wrong with plex?"

        result = await filter(mock_message)

        assert result is True

    @pytest.mark.asyncio
    async def test_filter_rejects_commands(self, mock_message):
        filter = NLFilter()
        mock_message.text = "/status"

        result = await filter(mock_message)

        assert result is False

    @pytest.mark.asyncio
    async def test_filter_rejects_empty_text(self, mock_message):
        filter = NLFilter()
        mock_message.text = None

        result = await filter(mock_message)

        assert result is False

    @pytest.mark.asyncio
    async def test_filter_rejects_whitespace_only(self, mock_message):
        filter = NLFilter()
        mock_message.text = "   "

        result = await filter(mock_message)

        assert result is False


class TestNLHandler:
    @pytest.mark.asyncio
    async def test_handler_calls_processor(self, mock_message, mock_processor):
        from src.services.nl_processor import ProcessResult
        mock_processor.process.return_value = ProcessResult(response="All good!")

        handler = create_nl_handler(mock_processor)
        await handler(mock_message)

        mock_processor.process.assert_called_once_with(
            user_id=123,
            message="what's wrong with plex?",
        )

    @pytest.mark.asyncio
    async def test_handler_sends_response(self, mock_message, mock_processor):
        from src.services.nl_processor import ProcessResult
        mock_processor.process.return_value = ProcessResult(response="Everything is fine!")

        handler = create_nl_handler(mock_processor)
        await handler(mock_message)

        mock_message.answer.assert_called()
        call_text = mock_message.answer.call_args[0][0]
        assert "Everything is fine" in call_text

    @pytest.mark.asyncio
    async def test_handler_adds_confirmation_buttons_when_pending(self, mock_message, mock_processor):
        from src.services.nl_processor import ProcessResult
        mock_processor.process.return_value = ProcessResult(
            response="I can restart plex for you.",
            pending_action={"action": "restart", "container": "plex"},
        )

        handler = create_nl_handler(mock_processor)
        await handler(mock_message)

        # Check that reply_markup was passed
        call_kwargs = mock_message.answer.call_args[1]
        assert "reply_markup" in call_kwargs
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_nl_handler.py -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'src.bot.nl_handler'"

**Step 3: Write implementation**

```python
# src/bot/nl_handler.py
"""Natural language message handler for Telegram bot."""
import logging
from typing import Any

from aiogram import BaseMiddleware
from aiogram.filters import BaseFilter
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

logger = logging.getLogger(__name__)


class NLFilter(BaseFilter):
    """Filter that matches non-command text messages."""

    async def __call__(self, message: Message) -> bool:
        if message.text is None:
            return False
        text = message.text.strip()
        if not text:
            return False
        if text.startswith("/"):
            return False
        return True


def create_nl_handler(processor: Any):
    """Create a message handler for natural language queries.

    Args:
        processor: NLProcessor instance

    Returns:
        Async handler function for aiogram
    """
    async def handler(message: Message) -> None:
        if message.text is None or message.from_user is None:
            return

        user_id = message.from_user.id
        text = message.text.strip()

        logger.debug(f"NL query from {user_id}: {text[:50]}...")

        result = await processor.process(user_id=user_id, message=text)

        # Build response
        reply_markup = None
        if result.pending_action:
            action = result.pending_action["action"]
            container = result.pending_action["container"]

            # Create confirmation buttons
            reply_markup = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Yes",
                        callback_data=f"nl_confirm:{action}:{container}",
                    ),
                    InlineKeyboardButton(
                        text="❌ No",
                        callback_data="nl_cancel",
                    ),
                ]
            ])

        await message.answer(result.response, reply_markup=reply_markup)

    return handler
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_nl_handler.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/bot/nl_handler.py tests/test_nl_handler.py
git commit -m "feat(nl): add Telegram handler for non-command messages"
```

---

## Task 8: Confirmation Callback Handler

**Files:**
- Modify: `src/bot/nl_handler.py`
- Test: `tests/test_nl_handler.py`

**Step 1: Write failing tests for confirmation callbacks**

```python
# Add to tests/test_nl_handler.py
from aiogram.types import CallbackQuery
from src.bot.nl_handler import create_nl_confirm_callback, create_nl_cancel_callback


@pytest.fixture
def mock_callback():
    callback = Mock(spec=CallbackQuery)
    callback.from_user = Mock(spec=User)
    callback.from_user.id = 123
    callback.message = Mock(spec=Message)
    callback.message.edit_text = AsyncMock()
    callback.answer = AsyncMock()
    callback.data = "nl_confirm:restart:plex"
    return callback


class TestNLConfirmCallback:
    @pytest.mark.asyncio
    async def test_confirm_executes_action(self, mock_callback, mock_processor):
        mock_controller = Mock()
        mock_controller.restart = AsyncMock(return_value="✅ plex restarted")

        handler = create_nl_confirm_callback(mock_processor, mock_controller)
        await handler(mock_callback)

        mock_controller.restart.assert_called_once_with("plex")

    @pytest.mark.asyncio
    async def test_confirm_updates_message(self, mock_callback, mock_processor):
        mock_controller = Mock()
        mock_controller.restart = AsyncMock(return_value="✅ plex restarted")

        handler = create_nl_confirm_callback(mock_processor, mock_controller)
        await handler(mock_callback)

        mock_callback.message.edit_text.assert_called()
        call_text = mock_callback.message.edit_text.call_args[0][0]
        assert "restarted" in call_text.lower()

    @pytest.mark.asyncio
    async def test_confirm_clears_pending_action(self, mock_callback, mock_processor):
        mock_controller = Mock()
        mock_controller.restart = AsyncMock(return_value="✅ plex restarted")

        # Set up pending action in memory
        from src.services.nl_processor import MemoryStore
        mock_processor.memory_store = MemoryStore()
        memory = mock_processor.memory_store.get_or_create(123)
        memory.pending_action = {"action": "restart", "container": "plex"}

        handler = create_nl_confirm_callback(mock_processor, mock_controller)
        await handler(mock_callback)

        assert memory.pending_action is None


class TestNLCancelCallback:
    @pytest.mark.asyncio
    async def test_cancel_updates_message(self, mock_callback, mock_processor):
        mock_callback.data = "nl_cancel"

        handler = create_nl_cancel_callback(mock_processor)
        await handler(mock_callback)

        mock_callback.message.edit_text.assert_called()
        call_text = mock_callback.message.edit_text.call_args[0][0]
        assert "cancel" in call_text.lower()
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_nl_handler.py::TestNLConfirmCallback -v`
Expected: FAIL

**Step 3: Write implementation**

```python
# Add to src/bot/nl_handler.py

def create_nl_confirm_callback(processor: Any, controller: Any):
    """Create callback handler for NL confirmation buttons.

    Args:
        processor: NLProcessor instance (for memory access)
        controller: ContainerController instance

    Returns:
        Async callback handler for aiogram
    """
    async def handler(callback: Any) -> None:
        if callback.data is None or callback.from_user is None:
            return

        # Parse callback data: nl_confirm:action:container
        parts = callback.data.split(":", 2)
        if len(parts) != 3:
            await callback.answer("Invalid action")
            return

        _, action, container = parts
        user_id = callback.from_user.id

        # Clear pending action from memory
        memory = processor.memory_store.get(user_id)
        if memory:
            memory.pending_action = None

        # Execute the action
        result = None
        if action == "restart":
            result = await controller.restart(container)
        elif action == "stop":
            result = await controller.stop(container)
        elif action == "start":
            result = await controller.start(container)
        elif action == "pull":
            result = await controller.pull_and_recreate(container)
        else:
            result = f"Unknown action: {action}"

        # Update the message
        await callback.message.edit_text(result)
        await callback.answer()

    return handler


def create_nl_cancel_callback(processor: Any):
    """Create callback handler for NL cancel buttons.

    Args:
        processor: NLProcessor instance (for memory access)

    Returns:
        Async callback handler for aiogram
    """
    async def handler(callback: Any) -> None:
        if callback.from_user is None:
            return

        user_id = callback.from_user.id

        # Clear pending action from memory
        memory = processor.memory_store.get(user_id)
        if memory:
            memory.pending_action = None

        await callback.message.edit_text("Action cancelled.")
        await callback.answer()

    return handler
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_nl_handler.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/bot/nl_handler.py tests/test_nl_handler.py
git commit -m "feat(nl): add confirmation callback handlers"
```

---

## Task 9: Wire Up in Main and Telegram Bot

**Files:**
- Modify: `src/bot/telegram_bot.py`
- Modify: `src/main.py`

**Step 1: Modify telegram_bot.py to accept NL handler registration**

```python
# In src/bot/telegram_bot.py, modify register_commands to accept and register NL handler
# Add at the end of register_commands function, before the return statement:

    # Register natural language handler (must be last - catches all non-commands)
    if nl_processor is not None and controller is not None:
        from src.bot.nl_handler import NLFilter, create_nl_handler, create_nl_confirm_callback, create_nl_cancel_callback

        # Register NL confirmation callbacks
        dp.callback_query.register(
            create_nl_confirm_callback(nl_processor, controller),
            F.data.startswith("nl_confirm:"),
        )
        dp.callback_query.register(
            create_nl_cancel_callback(nl_processor),
            F.data == "nl_cancel",
        )

        # Register NL message handler (catches all non-command text)
        dp.message.register(
            create_nl_handler(nl_processor),
            NLFilter(),
        )
```

**Step 2: Add nl_processor parameter to register_commands signature**

Add `nl_processor: Any | None = None,` to the `register_commands` function parameters.

**Step 3: Modify main.py to create and pass NL processor**

```python
# In src/main.py, after creating pattern_analyzer, add:

    # Initialize NL processor if Anthropic is configured
    nl_processor = None
    if anthropic_client:
        from src.services.nl_processor import NLProcessor
        from src.services.nl_tools import NLToolExecutor

        # NL tool executor will be created after we have all dependencies
        logger.info("Natural language processing enabled")
```

Then after creating the controller (inside register_commands), initialize NLToolExecutor and NLProcessor:

```python
    # In main.py, before calling register_commands:

    # Create NL processor if enabled
    nl_processor = None
    if anthropic_client and monitor._client:
        from src.services.nl_processor import NLProcessor
        from src.services.nl_tools import NLToolExecutor

        nl_executor = NLToolExecutor(
            state=state,
            docker_client=monitor._client,
            protected_containers=config.protected_containers,
            controller=None,  # Will be set after register_commands
            resource_monitor=resource_monitor,
            recent_errors_buffer=recent_errors_buffer,
            unraid_system_monitor=unraid_system_monitor,
        )
        nl_processor = NLProcessor(
            anthropic_client=anthropic_client,
            tool_executor=nl_executor,
        )
```

**Step 4: Update register_commands call to pass nl_processor**

```python
    # Pass nl_processor to register_commands
    confirmation, diagnostic_service = register_commands(
        dp,
        state,
        docker_client=monitor._client,
        protected_containers=config.protected_containers,
        anthropic_client=anthropic_client,
        resource_monitor=resource_monitor,
        ignore_manager=ignore_manager,
        recent_errors_buffer=recent_errors_buffer,
        mute_manager=mute_manager,
        unraid_system_monitor=unraid_system_monitor,
        server_mute_manager=server_mute_manager,
        array_mute_manager=array_mute_manager,
        memory_monitor=memory_monitor,
        pattern_analyzer=pattern_analyzer,
        nl_processor=nl_processor,  # Add this
    )

    # Set controller on NL executor after register_commands creates it
    if nl_processor and confirmation:
        # Get the controller that was created inside register_commands
        # We need to refactor slightly - for now, create a second controller
        from src.services.container_control import ContainerController
        nl_controller = ContainerController(monitor._client, config.protected_containers)
        nl_processor._executor._controller = nl_controller
```

**Step 5: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: All tests PASS

**Step 6: Commit**

```bash
git add src/bot/telegram_bot.py src/main.py
git commit -m "feat(nl): wire up NL processor in main application"
```

---

## Task 10: Integration Tests

**Files:**
- Create: `tests/test_nl_integration.py`

**Step 1: Write integration tests**

```python
# tests/test_nl_integration.py
"""Integration tests for natural language chat feature."""
import pytest
from unittest.mock import Mock, AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from src.services.nl_processor import NLProcessor, MemoryStore
from src.services.nl_tools import NLToolExecutor, get_tool_definitions
from src.state import ContainerStateManager
from src.models import ContainerInfo


@pytest.fixture
def state():
    state = ContainerStateManager()
    state.update(ContainerInfo(
        name="plex",
        status="running",
        health="healthy",
        image="plexinc/pms-docker:latest",
        started_at=datetime.now(timezone.utc),
    ))
    state.update(ContainerInfo(
        name="radarr",
        status="running",
        health=None,
        image="linuxserver/radarr:latest",
        started_at=datetime.now(timezone.utc),
    ))
    state.update(ContainerInfo(
        name="mariadb",
        status="running",
        health="healthy",
        image="mariadb:10",
        started_at=datetime.now(timezone.utc),
    ))
    return state


@pytest.fixture
def mock_docker():
    docker = Mock()
    container = Mock()
    container.logs.return_value = b"[INFO] Server started\n[ERROR] Connection timeout\n"
    docker.containers.get.return_value = container
    return docker


@pytest.fixture
def executor(state, mock_docker):
    return NLToolExecutor(
        state=state,
        docker_client=mock_docker,
        protected_containers=["mariadb"],
    )


class TestNLIntegration:
    @pytest.mark.asyncio
    async def test_end_to_end_status_query(self, executor):
        """Test a complete flow: user asks about container status."""
        # Mock Anthropic client with tool use
        mock_anthropic = Mock()

        # First response: tool use
        tool_use_block = Mock(type="tool_use", id="123", name="get_container_status", input={"name": "plex"})
        response1 = Mock(stop_reason="tool_use", content=[tool_use_block])

        # Second response: final text
        text_block = Mock(type="text", text="Plex is running and healthy.")
        response2 = Mock(stop_reason="end_turn", content=[text_block])

        mock_anthropic.messages.create = Mock(side_effect=[response1, response2])

        processor = NLProcessor(
            anthropic_client=mock_anthropic,
            tool_executor=executor,
        )

        result = await processor.process(user_id=123, message="how's plex doing?")

        assert "plex" in result.response.lower() or "running" in result.response.lower()

    @pytest.mark.asyncio
    async def test_followup_uses_context(self, executor):
        """Test that follow-up questions use conversation context."""
        mock_anthropic = Mock()

        # Simple text responses for simplicity
        text_response = Mock(stop_reason="end_turn", content=[Mock(type="text", text="OK")])
        mock_anthropic.messages.create = Mock(return_value=text_response)

        processor = NLProcessor(
            anthropic_client=mock_anthropic,
            tool_executor=executor,
        )

        # First query
        await processor.process(user_id=123, message="check plex")

        # Second query (follow-up)
        await processor.process(user_id=123, message="what about its logs?")

        # Check that second call included history
        calls = mock_anthropic.messages.create.call_args_list
        second_call_messages = calls[1][1]["messages"]
        # Should have: previous user msg, previous assistant msg, new user msg
        assert len(second_call_messages) >= 3

    @pytest.mark.asyncio
    async def test_protected_container_rejection(self, executor):
        """Test that protected containers cannot be controlled."""
        result = await executor.execute("restart_container", {"name": "mariadb"})

        assert "protected" in result.lower() or "cannot" in result.lower()

    @pytest.mark.asyncio
    async def test_action_returns_confirmation(self, executor):
        """Test that actions return confirmation needed."""
        result = await executor.execute("restart_container", {"name": "plex"})

        assert result.startswith("CONFIRMATION_NEEDED:")

    @pytest.mark.asyncio
    async def test_start_executes_immediately(self, executor):
        """Test that start doesn't require confirmation."""
        mock_controller = Mock()
        mock_controller.start = AsyncMock(return_value="✅ plex started")
        executor._controller = mock_controller

        result = await executor.execute("start_container", {"name": "plex"})

        assert "CONFIRMATION_NEEDED" not in result
        mock_controller.start.assert_called_once()
```

**Step 2: Run integration tests**

Run: `python -m pytest tests/test_nl_integration.py -v`
Expected: PASS

**Step 3: Commit**

```bash
git add tests/test_nl_integration.py
git commit -m "test(nl): add integration tests for NL chat feature"
```

---

## Task 11: Update Documentation

**Files:**
- Modify: `CLAUDE.md`
- Modify: `README.md`

**Step 1: Update CLAUDE.md**

Add to the "Key Modules" section:

```markdown
**`src/services/`** - Business logic services:
- `nl_processor.py` - Natural language processing with Claude API and conversation memory
- `nl_tools.py` - Tool definitions and executor for NL queries
- `container_control.py` - Container operations with safety features
- `diagnostic.py` - AI-powered log analysis

**`src/bot/`** - Telegram interface:
- `nl_handler.py` - Routes non-command messages to NL processor
```

**Step 2: Update README.md**

Add a new section after "Commands":

```markdown
## Natural Language Chat

Instead of using commands, you can ask questions naturally:

- "What's wrong with plex?"
- "Why is my server slow?"
- "Is anything crashing?"
- "Restart radarr" (will ask for confirmation)

The bot uses AI to understand your question, gather relevant data, and respond conversationally. Follow-up questions work too - say "restart it" after discussing a container.

**Note:** Requires `ANTHROPIC_API_KEY` to be configured.
```

**Step 3: Commit**

```bash
git add CLAUDE.md README.md
git commit -m "docs: add NL chat documentation"
```

---

## Task 12: Final Verification

**Step 1: Run full test suite**

Run: `python -m pytest tests/ -v --tb=short`
Expected: All tests PASS

**Step 2: Run type checking**

Run: `mypy src/services/nl_processor.py src/services/nl_tools.py src/bot/nl_handler.py`
Expected: No errors (or only minor ones from untyped dependencies)

**Step 3: Run linting**

Run: `ruff check src/services/nl_processor.py src/services/nl_tools.py src/bot/nl_handler.py`
Expected: No errors

**Step 4: Manual test (if possible)**

Start the bot and test:
1. Send "how's everything?" - should get status summary
2. Send "what's wrong with plex?" - should check status and logs
3. Send "restart it" - should ask for confirmation
4. Click Yes - should restart

**Step 5: Final commit**

```bash
git add -A
git commit -m "feat(nl): complete natural language chat implementation"
```

---

Plan complete and saved to `docs/plans/2026-01-27-natural-language-chat.md`.

**Two execution options:**

1. **Subagent-Driven (this session)** - I dispatch fresh subagent per task, review between tasks, fast iteration

2. **Parallel Session (separate)** - Open new session in the worktree with executing-plans, batch execution with checkpoints

Which approach?
