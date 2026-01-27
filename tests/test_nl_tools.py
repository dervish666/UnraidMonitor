# tests/test_nl_tools.py
import pytest
from src.services.nl_tools import (
    get_tool_definitions,
    is_action_tool,
    is_read_only_tool,
    READ_ONLY_TOOLS,
    ACTION_TOOLS,
)


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
