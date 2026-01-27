# tests/test_nl_tools.py
import pytest
from unittest.mock import Mock, MagicMock
from datetime import datetime, timezone

from src.services.nl_tools import (
    get_tool_definitions,
    is_action_tool,
    is_read_only_tool,
    READ_ONLY_TOOLS,
    ACTION_TOOLS,
    NLToolExecutor,
)
from src.models import ContainerInfo


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

    def test_server_tools_exist(self):
        """Test that server-related tools are defined."""
        tools = get_tool_definitions()
        tool_names = [t["name"] for t in tools]

        assert "get_server_stats" in tool_names
        assert "get_array_status" in tool_names

    def test_tools_with_required_name_param(self):
        """Test that tools requiring a name have it marked as required."""
        tools = get_tool_definitions()

        # Tools that require the 'name' parameter
        tools_requiring_name = [
            "get_container_status",
            "get_container_logs",
            "restart_container",
            "stop_container",
            "start_container",
            "pull_container",
        ]

        for tool_name in tools_requiring_name:
            tool = next(t for t in tools if t["name"] == tool_name)
            assert "name" in tool["input_schema"]["required"], f"{tool_name} should require 'name'"

    def test_tools_with_optional_name_param(self):
        """Test that some tools have name as optional."""
        tools = get_tool_definitions()

        # Tools where 'name' is optional
        tools_optional_name = ["get_resource_usage", "get_recent_errors"]

        for tool_name in tools_optional_name:
            tool = next(t for t in tools if t["name"] == tool_name)
            # name should be in properties but not required
            assert "name" in tool["input_schema"]["properties"]
            assert "name" not in tool["input_schema"].get("required", [])

    def test_tools_with_no_params(self):
        """Test that some tools have no required parameters."""
        tools = get_tool_definitions()

        # Tools with no required params
        no_param_tools = ["get_container_list", "get_server_stats", "get_array_status"]

        for tool_name in no_param_tools:
            tool = next(t for t in tools if t["name"] == tool_name)
            # Either empty required list or no required key
            required = tool["input_schema"].get("required", [])
            assert len(required) == 0, f"{tool_name} should have no required params"

    def test_tool_descriptions_are_meaningful(self):
        """Test that all tools have non-empty descriptions."""
        tools = get_tool_definitions()

        for tool in tools:
            assert len(tool["description"]) > 10, f"{tool['name']} needs a meaningful description"

    def test_input_schema_properties_have_types(self):
        """Test that all properties in input_schema have type definitions."""
        tools = get_tool_definitions()

        for tool in tools:
            properties = tool["input_schema"].get("properties", {})
            for prop_name, prop_def in properties.items():
                assert "type" in prop_def, f"{tool['name']}.{prop_name} needs a type"


class TestToolCategories:
    def test_is_action_tool_returns_true_for_actions(self):
        """Test that action tools are correctly identified."""
        assert is_action_tool("restart_container") is True
        assert is_action_tool("stop_container") is True
        assert is_action_tool("start_container") is True
        assert is_action_tool("pull_container") is True

    def test_is_action_tool_returns_false_for_read_only(self):
        """Test that read-only tools are not identified as actions."""
        assert is_action_tool("get_container_list") is False
        assert is_action_tool("get_container_status") is False
        assert is_action_tool("get_container_logs") is False

    def test_is_read_only_tool_returns_true_for_read_only(self):
        """Test that read-only tools are correctly identified."""
        assert is_read_only_tool("get_container_list") is True
        assert is_read_only_tool("get_container_status") is True
        assert is_read_only_tool("get_container_logs") is True
        assert is_read_only_tool("get_resource_usage") is True
        assert is_read_only_tool("get_server_stats") is True
        assert is_read_only_tool("get_array_status") is True
        assert is_read_only_tool("get_recent_errors") is True

    def test_is_read_only_tool_returns_false_for_actions(self):
        """Test that action tools are not identified as read-only."""
        assert is_read_only_tool("restart_container") is False
        assert is_read_only_tool("stop_container") is False

    def test_unknown_tool_returns_false(self):
        """Test that unknown tools return False for both checks."""
        assert is_action_tool("unknown_tool") is False
        assert is_read_only_tool("unknown_tool") is False

    def test_all_tools_are_categorized(self):
        """Test that every tool in definitions is in exactly one category."""
        tools = get_tool_definitions()
        tool_names = {t["name"] for t in tools}

        # All tools should be in exactly one category
        all_categorized = READ_ONLY_TOOLS | ACTION_TOOLS
        assert tool_names == all_categorized, "All tools should be categorized"

        # No overlap between categories
        assert READ_ONLY_TOOLS & ACTION_TOOLS == set(), "Categories should not overlap"


# Fixtures for NLToolExecutor tests
@pytest.fixture
def mock_state():
    state = Mock()
    state.get_all.return_value = [
        ContainerInfo(
            name="plex",
            status="running",
            health="healthy",
            image="plexinc/pms-docker",
            started_at=datetime.now(timezone.utc),
        ),
        ContainerInfo(
            name="radarr",
            status="running",
            health=None,
            image="linuxserver/radarr",
            started_at=datetime.now(timezone.utc),
        ),
        ContainerInfo(
            name="sonarr",
            status="exited",
            health=None,
            image="linuxserver/sonarr",
            started_at=None,
        ),
    ]
    state.find_by_name.return_value = [
        ContainerInfo(
            name="plex",
            status="running",
            health="healthy",
            image="plexinc/pms-docker",
            started_at=datetime.now(timezone.utc),
        ),
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
            ContainerInfo(
                name="radarr",
                status="running",
                health=None,
                image="img",
                started_at=None,
            ),
            ContainerInfo(
                name="radarr-sync",
                status="running",
                health=None,
                image="img",
                started_at=None,
            ),
        ]
        result = await executor.execute("get_container_status", {"name": "rad"})
        assert "multiple" in result.lower() or (
            "radarr" in result and "radarr-sync" in result
        )

    @pytest.mark.asyncio
    async def test_get_container_logs(self, executor):
        result = await executor.execute(
            "get_container_logs", {"name": "plex", "lines": 10}
        )
        assert "Server started" in result or "Connection failed" in result

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self, executor):
        result = await executor.execute("unknown_tool", {})
        assert "unknown" in result.lower() or "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_get_container_logs_truncates_long_output(self, executor, mock_docker):
        """Test that long logs are truncated."""
        # Create a log that exceeds 3000 characters
        long_log = b"A" * 4000
        mock_docker.containers.get.return_value.logs.return_value = long_log
        result = await executor.execute(
            "get_container_logs", {"name": "plex", "lines": 100}
        )
        assert "truncated" in result.lower()

    @pytest.mark.asyncio
    async def test_get_container_logs_empty(self, executor, mock_docker):
        """Test handling of empty logs."""
        mock_docker.containers.get.return_value.logs.return_value = b""
        result = await executor.execute(
            "get_container_logs", {"name": "plex", "lines": 10}
        )
        assert "no recent logs" in result.lower()

    @pytest.mark.asyncio
    async def test_get_container_logs_limits_lines(self, executor, mock_docker):
        """Test that lines parameter is capped at 200."""
        await executor.execute("get_container_logs", {"name": "plex", "lines": 500})
        # Should cap at 200
        mock_docker.containers.get.return_value.logs.assert_called_with(
            tail=200, timestamps=False
        )

    @pytest.mark.asyncio
    async def test_get_resource_usage_not_available(self, executor):
        """Test resource usage when monitor not configured."""
        result = await executor.execute("get_resource_usage", {})
        assert "not available" in result.lower()

    @pytest.mark.asyncio
    async def test_get_server_stats_not_configured(self, executor):
        """Test server stats when unraid not configured."""
        result = await executor.execute("get_server_stats", {})
        assert "not configured" in result.lower()

    @pytest.mark.asyncio
    async def test_get_array_status_not_configured(self, executor):
        """Test array status when unraid not configured."""
        result = await executor.execute("get_array_status", {})
        assert "not configured" in result.lower()

    @pytest.mark.asyncio
    async def test_get_recent_errors_not_available(self, executor):
        """Test recent errors when buffer not configured."""
        result = await executor.execute("get_recent_errors", {})
        assert "not available" in result.lower()

    @pytest.mark.asyncio
    async def test_get_container_status_with_health(self, executor, mock_state):
        """Test that health status is included in output."""
        mock_state.find_by_name.return_value = [
            ContainerInfo(
                name="plex",
                status="running",
                health="healthy",
                image="plexinc/pms-docker",
                started_at=datetime.now(timezone.utc),
            ),
        ]
        result = await executor.execute("get_container_status", {"name": "plex"})
        assert "healthy" in result.lower()

    @pytest.mark.asyncio
    async def test_get_container_list_grouped_by_status(self, executor):
        """Test that containers are grouped by running/stopped status."""
        result = await executor.execute("get_container_list", {})
        # Should have Running section with plex and radarr
        assert "Running" in result
        # Should have Stopped section with sonarr
        assert "Stopped" in result
