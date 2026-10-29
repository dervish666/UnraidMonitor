# tests/test_openai_provider.py
"""Tests for OpenAI LLM provider."""
import json

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.services.llm.openai_provider import OpenAIProvider
from src.services.llm.provider import LLMProvider, LLMResponse, ToolCall


# -- Helpers to build mock OpenAI responses --


def _make_tool_call(call_id: str, name: str, arguments: dict) -> MagicMock:
    """Build a mock OpenAI function tool call object."""
    tc = MagicMock()
    tc.id = call_id
    tc.type = "function"
    tc.function = MagicMock()
    tc.function.name = name
    tc.function.arguments = json.dumps(arguments)
    return tc


def _make_message(
    content: str | None = "Hello",
    role: str = "assistant",
    tool_calls: list | None = None,
) -> MagicMock:
    """Build a mock OpenAI ChatCompletionMessage."""
    msg = MagicMock()
    msg.content = content
    msg.role = role
    msg.tool_calls = tool_calls
    return msg


def _make_choice(
    message: MagicMock | None = None,
    finish_reason: str = "stop",
) -> MagicMock:
    """Build a mock OpenAI Choice object."""
    choice = MagicMock()
    choice.message = message or _make_message()
    choice.finish_reason = finish_reason
    return choice


def _make_response(
    choices: list | None = None,
) -> MagicMock:
    """Build a mock OpenAI ChatCompletion response."""
    resp = MagicMock()
    resp.choices = choices or [_make_choice()]
    return resp


def _make_client(response: MagicMock) -> MagicMock:
    """Build a mock openai.AsyncOpenAI client."""
    client = MagicMock()
    client.chat = MagicMock()
    client.chat.completions = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=response)
    return client


# -- Tests --


class TestOpenAIProviderProperties:
    """Test provider_name, model_name, and supports_tools properties."""

    def test_provider_name(self):
        provider = OpenAIProvider(client=MagicMock(), model="gpt-4o")
        assert provider.provider_name == "openai"

    def test_model_name(self):
        provider = OpenAIProvider(client=MagicMock(), model="gpt-4o-mini")
        assert provider.model_name == "gpt-4o-mini"

    def test_supports_tools_default(self):
        provider = OpenAIProvider(client=MagicMock(), model="gpt-4o")
        assert provider.supports_tools is True

    def test_supports_tools_disabled(self):
        provider = OpenAIProvider(
            client=MagicMock(), model="gpt-4o", supports_tools=False
        )
        assert provider.supports_tools is False

    def test_satisfies_protocol(self):
        provider = OpenAIProvider(client=MagicMock(), model="gpt-4o")
        assert isinstance(provider, LLMProvider)


class TestOpenAIProviderSimpleChat:
    """Test simple text chat (no tools, no system prompt)."""

    async def test_simple_text_response(self):
        msg = _make_message(content="Hello, world!")
        response = _make_response([_make_choice(message=msg, finish_reason="stop")])
        client = _make_client(response)
        provider = OpenAIProvider(client=client, model="gpt-4o")

        result = await provider.chat(
            messages=[{"role": "user", "content": "Hi"}],
        )

        assert isinstance(result, LLMResponse)
        assert result.text == "Hello, world!"
        assert result.stop_reason == "end"
        assert result.tool_calls is None

    async def test_none_content_returns_empty_string(self):
        msg = _make_message(content=None)
        response = _make_response([_make_choice(message=msg, finish_reason="stop")])
        client = _make_client(response)
        provider = OpenAIProvider(client=client, model="gpt-4o")

        result = await provider.chat(
            messages=[{"role": "user", "content": "Hi"}],
        )

        assert result.text == ""

    async def test_messages_and_params_passed_to_client(self):
        response = _make_response()
        client = _make_client(response)
        provider = OpenAIProvider(client=client, model="gpt-4o")

        messages = [{"role": "user", "content": "Hello"}]
        await provider.chat(messages=messages, max_tokens=512)

        call_kwargs = client.chat.completions.create.call_args[1]
        assert call_kwargs["model"] == "gpt-4o"
        assert call_kwargs["max_tokens"] == 512
        # Messages should contain only the user message (no system since none given)
        assert call_kwargs["messages"] == messages


class TestOpenAIProviderSystemPrompt:
    """Test that system prompts are prepended as a system message."""

    async def test_system_prompt_prepended(self):
        response = _make_response()
        client = _make_client(response)
        provider = OpenAIProvider(client=client, model="gpt-4o")

        await provider.chat(
            messages=[{"role": "user", "content": "Hi"}],
            system="You are a helpful assistant.",
        )

        call_kwargs = client.chat.completions.create.call_args[1]
        sent_messages = call_kwargs["messages"]

        assert len(sent_messages) == 2
        assert sent_messages[0] == {
            "role": "system",
            "content": "You are a helpful assistant.",
        }
        assert sent_messages[1] == {"role": "user", "content": "Hi"}

    async def test_no_system_prompt_no_system_message(self):
        response = _make_response()
        client = _make_client(response)
        provider = OpenAIProvider(client=client, model="gpt-4o")

        await provider.chat(
            messages=[{"role": "user", "content": "Hi"}],
        )

        call_kwargs = client.chat.completions.create.call_args[1]
        sent_messages = call_kwargs["messages"]

        assert len(sent_messages) == 1
        assert sent_messages[0]["role"] == "user"


class TestOpenAIProviderToolDefinitions:
    """Test tool definition translation to OpenAI function format."""

    async def test_tools_translated_to_openai_format(self):
        response = _make_response()
        client = _make_client(response)
        provider = OpenAIProvider(client=client, model="gpt-4o")

        tools = [
            {
                "name": "get_status",
                "description": "Get container status",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Container name"},
                    },
                    "required": ["name"],
                },
            },
        ]

        await provider.chat(
            messages=[{"role": "user", "content": "Check status"}],
            tools=tools,
        )

        call_kwargs = client.chat.completions.create.call_args[1]
        sent_tools = call_kwargs["tools"]

        assert len(sent_tools) == 1
        assert sent_tools[0] == {
            "type": "function",
            "function": {
                "name": "get_status",
                "description": "Get container status",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Container name"},
                    },
                    "required": ["name"],
                },
            },
        }

    async def test_multiple_tools_translated(self):
        response = _make_response()
        client = _make_client(response)
        provider = OpenAIProvider(client=client, model="gpt-4o")

        tools = [
            {"name": "tool_a", "description": "A", "input_schema": {}},
            {"name": "tool_b", "description": "B", "input_schema": {"type": "object"}},
        ]

        await provider.chat(
            messages=[{"role": "user", "content": "Hi"}],
            tools=tools,
        )

        call_kwargs = client.chat.completions.create.call_args[1]
        sent_tools = call_kwargs["tools"]

        assert len(sent_tools) == 2
        assert sent_tools[0]["type"] == "function"
        assert sent_tools[0]["function"]["name"] == "tool_a"
        assert sent_tools[0]["function"]["parameters"] == {}
        assert sent_tools[1]["function"]["name"] == "tool_b"
        assert sent_tools[1]["function"]["parameters"] == {"type": "object"}

    async def test_no_tools_omits_tools_kwarg(self):
        response = _make_response()
        client = _make_client(response)
        provider = OpenAIProvider(client=client, model="gpt-4o")

        await provider.chat(
            messages=[{"role": "user", "content": "Hi"}],
        )

        call_kwargs = client.chat.completions.create.call_args[1]
        assert "tools" not in call_kwargs

    async def test_supports_tools_false_omits_tools(self):
        """When _supports_tools=False, tools are NOT passed to the API even if provided."""
        response = _make_response()
        client = _make_client(response)
        provider = OpenAIProvider(
            client=client, model="gpt-4o", supports_tools=False
        )

        tools = [{"name": "tool_a", "description": "A", "input_schema": {}}]

        await provider.chat(
            messages=[{"role": "user", "content": "Hi"}],
            tools=tools,
        )

        call_kwargs = client.chat.completions.create.call_args[1]
        assert "tools" not in call_kwargs

    async def test_original_tools_list_not_mutated(self):
        response = _make_response()
        client = _make_client(response)
        provider = OpenAIProvider(client=client, model="gpt-4o")

        tools = [
            {"name": "tool_a", "description": "A", "input_schema": {}},
        ]
        import copy
        original = copy.deepcopy(tools)

        await provider.chat(
            messages=[{"role": "user", "content": "Hi"}],
            tools=tools,
        )

        assert tools == original


class TestOpenAIProviderToolCallExtraction:
    """Test tool use response with tool_calls parsed."""

    async def test_tool_calls_extracted(self):
        tc = _make_tool_call("call_1", "get_status", {"name": "plex"})
        msg = _make_message(content="Let me check.", tool_calls=[tc])
        response = _make_response(
            [_make_choice(message=msg, finish_reason="tool_calls")]
        )
        client = _make_client(response)
        provider = OpenAIProvider(client=client, model="gpt-4o")

        result = await provider.chat(
            messages=[{"role": "user", "content": "How is plex?"}],
            tools=[
                {"name": "get_status", "description": "Get status", "input_schema": {}}
            ],
        )

        assert result.stop_reason == "tool_use"
        assert result.text == "Let me check."
        assert result.tool_calls is not None
        assert len(result.tool_calls) == 1

        tool_call = result.tool_calls[0]
        assert isinstance(tool_call, ToolCall)
        assert tool_call.id == "call_1"
        assert tool_call.name == "get_status"
        assert tool_call.input == {"name": "plex"}

    async def test_multiple_tool_calls(self):
        tc1 = _make_tool_call("call_1", "get_status", {"name": "plex"})
        tc2 = _make_tool_call("call_2", "get_logs", {"name": "radarr", "lines": 50})
        msg = _make_message(content=None, tool_calls=[tc1, tc2])
        response = _make_response(
            [_make_choice(message=msg, finish_reason="tool_calls")]
        )
        client = _make_client(response)
        provider = OpenAIProvider(client=client, model="gpt-4o")

        result = await provider.chat(
            messages=[{"role": "user", "content": "Check both"}],
            tools=[
                {"name": "get_status", "description": "Get status", "input_schema": {}},
                {"name": "get_logs", "description": "Get logs", "input_schema": {}},
            ],
        )

        assert result.stop_reason == "tool_use"
        assert result.text == ""
        assert len(result.tool_calls) == 2
        assert result.tool_calls[0].name == "get_status"
        assert result.tool_calls[0].input == {"name": "plex"}
        assert result.tool_calls[1].name == "get_logs"
        assert result.tool_calls[1].input == {"name": "radarr", "lines": 50}

    async def test_no_tool_calls_returns_none(self):
        msg = _make_message(content="Just text", tool_calls=None)
        response = _make_response(
            [_make_choice(message=msg, finish_reason="stop")]
        )
        client = _make_client(response)
        provider = OpenAIProvider(client=client, model="gpt-4o")

        result = await provider.chat(
            messages=[{"role": "user", "content": "Hi"}],
        )

        assert result.tool_calls is None


class TestOpenAIProviderToolResultTranslation:
    """Test that normalized tool_result messages are translated to OpenAI format."""

    async def test_tool_result_translated(self):
        response = _make_response()
        client = _make_client(response)
        provider = OpenAIProvider(client=client, model="gpt-4o")

        messages = [
            {"role": "user", "content": "Check plex"},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Let me check."},
                    {
                        "type": "tool_use",
                        "id": "call_1",
                        "name": "get_status",
                        "input": {"name": "plex"},
                    },
                ],
            },
            {
                "role": "tool_result",
                "tool_use_id": "call_1",
                "content": "plex is running",
            },
        ]

        await provider.chat(messages=messages)

        call_kwargs = client.chat.completions.create.call_args[1]
        sent_messages = call_kwargs["messages"]

        # Tool result should be translated to OpenAI format
        tool_msg = sent_messages[-1]
        assert tool_msg["role"] == "tool"
        assert tool_msg["tool_call_id"] == "call_1"
        assert tool_msg["content"] == "plex is running"

    async def test_multiple_tool_results_stay_separate(self):
        """OpenAI expects each tool result as a separate message (unlike Anthropic)."""
        response = _make_response()
        client = _make_client(response)
        provider = OpenAIProvider(client=client, model="gpt-4o")

        messages = [
            {"role": "user", "content": "Check both"},
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "call_1", "name": "get_status", "input": {}},
                    {"type": "tool_use", "id": "call_2", "name": "get_logs", "input": {}},
                ],
            },
            {"role": "tool_result", "tool_use_id": "call_1", "content": "running"},
            {"role": "tool_result", "tool_use_id": "call_2", "content": "no errors"},
        ]

        await provider.chat(messages=messages)

        call_kwargs = client.chat.completions.create.call_args[1]
        sent_messages = call_kwargs["messages"]

        # Each tool result should be a separate message
        tool_msgs = [m for m in sent_messages if m.get("role") == "tool"]
        assert len(tool_msgs) == 2
        assert tool_msgs[0]["tool_call_id"] == "call_1"
        assert tool_msgs[0]["content"] == "running"
        assert tool_msgs[1]["tool_call_id"] == "call_2"
        assert tool_msgs[1]["content"] == "no errors"


class TestOpenAIProviderAssistantMessageTranslation:
    """Test that assistant messages with Anthropic-style content blocks are translated."""

    async def test_assistant_tool_use_blocks_translated(self):
        """Anthropic-style content blocks with tool_use should be translated to OpenAI format."""
        response = _make_response()
        client = _make_client(response)
        provider = OpenAIProvider(client=client, model="gpt-4o")

        messages = [
            {"role": "user", "content": "Check plex"},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Let me check."},
                    {
                        "type": "tool_use",
                        "id": "call_1",
                        "name": "get_status",
                        "input": {"name": "plex"},
                    },
                ],
            },
            {"role": "tool_result", "tool_use_id": "call_1", "content": "running"},
        ]

        await provider.chat(messages=messages)

        call_kwargs = client.chat.completions.create.call_args[1]
        sent_messages = call_kwargs["messages"]

        # The assistant message should be translated to OpenAI format
        assistant_msg = sent_messages[1]
        assert assistant_msg["role"] == "assistant"
        assert assistant_msg["content"] == "Let me check."
        assert len(assistant_msg["tool_calls"]) == 1
        assert assistant_msg["tool_calls"][0]["id"] == "call_1"
        assert assistant_msg["tool_calls"][0]["type"] == "function"
        assert assistant_msg["tool_calls"][0]["function"]["name"] == "get_status"
        assert (
            assistant_msg["tool_calls"][0]["function"]["arguments"]
            == json.dumps({"name": "plex"})
        )

    async def test_assistant_text_only_content_blocks(self):
        """Assistant with only text blocks should just extract the text."""
        response = _make_response()
        client = _make_client(response)
        provider = OpenAIProvider(client=client, model="gpt-4o")

        messages = [
            {"role": "user", "content": "Hi"},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Hello there!"},
                ],
            },
            {"role": "user", "content": "How are you?"},
        ]

        await provider.chat(messages=messages)

        call_kwargs = client.chat.completions.create.call_args[1]
        sent_messages = call_kwargs["messages"]

        assistant_msg = sent_messages[1]
        assert assistant_msg["role"] == "assistant"
        assert assistant_msg["content"] == "Hello there!"
        assert "tool_calls" not in assistant_msg

    async def test_assistant_string_content_passes_through(self):
        """Assistant messages with plain string content should pass through."""
        response = _make_response()
        client = _make_client(response)
        provider = OpenAIProvider(client=client, model="gpt-4o")

        messages = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello!"},
        ]

        await provider.chat(messages=messages)

        call_kwargs = client.chat.completions.create.call_args[1]
        sent_messages = call_kwargs["messages"]

        assert sent_messages[1] == {"role": "assistant", "content": "Hello!"}

    async def test_assistant_multiple_tool_use_blocks(self):
        """Multiple tool_use blocks should all be translated."""
        response = _make_response()
        client = _make_client(response)
        provider = OpenAIProvider(client=client, model="gpt-4o")

        messages = [
            {"role": "user", "content": "Check both"},
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "call_1", "name": "get_status", "input": {}},
                    {"type": "tool_use", "id": "call_2", "name": "get_logs", "input": {}},
                ],
            },
            {"role": "tool_result", "tool_use_id": "call_1", "content": "running"},
            {"role": "tool_result", "tool_use_id": "call_2", "content": "ok"},
        ]

        await provider.chat(messages=messages)

        call_kwargs = client.chat.completions.create.call_args[1]
        sent_messages = call_kwargs["messages"]

        assistant_msg = sent_messages[1]
        assert len(assistant_msg["tool_calls"]) == 2
        assert assistant_msg["tool_calls"][0]["function"]["name"] == "get_status"
        assert assistant_msg["tool_calls"][1]["function"]["name"] == "get_logs"
        # Content should be empty or None when there are no text blocks
        assert assistant_msg.get("content") in (None, "")


class TestOpenAIProviderStopReasonMapping:
    """Test all stop reason mappings."""

    @pytest.mark.parametrize(
        "openai_reason,expected",
        [
            ("stop", "end"),
            ("tool_calls", "tool_use"),
            ("length", "max_tokens"),
            ("content_filter", "end"),
        ],
    )
    async def test_stop_reason_mapped(self, openai_reason, expected):
        msg = _make_message(content="ok")
        response = _make_response(
            [_make_choice(message=msg, finish_reason=openai_reason)]
        )
        client = _make_client(response)
        provider = OpenAIProvider(client=client, model="gpt-4o")

        result = await provider.chat(
            messages=[{"role": "user", "content": "Hi"}],
        )

        assert result.stop_reason == expected

    async def test_unknown_stop_reason_passed_through(self):
        msg = _make_message(content="ok")
        response = _make_response(
            [_make_choice(message=msg, finish_reason="something_new")]
        )
        client = _make_client(response)
        provider = OpenAIProvider(client=client, model="gpt-4o")

        result = await provider.chat(
            messages=[{"role": "user", "content": "Hi"}],
        )

        assert result.stop_reason == "something_new"


class TestOpenAIProviderParsingGuards:
    """Tests for defensive parsing of edge-case responses."""

    def test_empty_choices_returns_empty_response(self):
        response = MagicMock()
        response.choices = []

        result = OpenAIProvider._parse_response(response)

        assert result.text == ""
        assert result.stop_reason == "end"
        assert result.tool_calls is None

    async def test_malformed_tool_json_skipped(self):
        tc = MagicMock()
        tc.id = "call_1"
        tc.function = MagicMock()
        tc.function.name = "get_status"
        tc.function.arguments = "not valid json"

        msg = _make_message(content="text", tool_calls=[tc])
        response = _make_response(
            [_make_choice(message=msg, finish_reason="tool_calls")]
        )
        client = _make_client(response)
        provider = OpenAIProvider(client=client, model="gpt-4o")

        result = await provider.chat(
            messages=[{"role": "user", "content": "Hi"}],
        )

        # Malformed tool call skipped, text preserved
        assert result.text == "text"
        assert result.tool_calls is None

    async def test_mix_of_valid_and_malformed_tools(self):
        good_tc = _make_tool_call("call_1", "get_status", {"name": "plex"})
        bad_tc = MagicMock()
        bad_tc.id = "call_2"
        bad_tc.function = MagicMock()
        bad_tc.function.name = "bad_func"
        bad_tc.function.arguments = "{broken"

        msg = _make_message(content="", tool_calls=[good_tc, bad_tc])
        response = _make_response(
            [_make_choice(message=msg, finish_reason="tool_calls")]
        )
        client = _make_client(response)
        provider = OpenAIProvider(client=client, model="gpt-4o")

        result = await provider.chat(
            messages=[{"role": "user", "content": "Hi"}],
        )

        # Only the valid tool call should be returned
        assert result.tool_calls is not None
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "get_status"


class TestOpenAIProviderNormalizedToolCallTranslation:
    """NLProcessor's assistant shape ({content: str, tool_calls: [{id, name, input}]})
    must reach the API in OpenAI form. Issue #7: it was passed through untouched and
    Ollama rejected the follow-up with 400 "invalid tool call arguments"."""

    def test_normalized_tool_calls_get_type_and_json_arguments(self):
        messages = [
            {"role": "user", "content": "how is plex?"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "c1", "name": "get_container_status", "input": {"name": "plex"}},
                    {"id": "c2", "name": "get_container_logs", "input": {"name": "plex", "lines": 20}},
                ],
            },
            {"role": "tool_result", "tool_use_id": "c1", "content": "running"},
            {"role": "tool_result", "tool_use_id": "c2", "content": "no errors"},
        ]

        result = OpenAIProvider._translate_messages(messages, system="sys")

        assistant = result[2]
        assert assistant["role"] == "assistant"
        assert assistant["content"] is None
        assert assistant["tool_calls"] == [
            {
                "id": "c1",
                "type": "function",
                "function": {"name": "get_container_status", "arguments": '{"name": "plex"}'},
            },
            {
                "id": "c2",
                "type": "function",
                "function": {
                    "name": "get_container_logs",
                    "arguments": '{"name": "plex", "lines": 20}',
                },
            },
        ]
        assert json.loads(assistant["tool_calls"][1]["function"]["arguments"]) == {
            "name": "plex", "lines": 20,
        }
        assert result[3] == {"role": "tool", "tool_call_id": "c1", "content": "running"}
        assert result[4] == {"role": "tool", "tool_call_id": "c2", "content": "no errors"}

    def test_normalized_tool_calls_keep_assistant_text(self):
        messages = [{
            "role": "assistant",
            "content": "Checking now.",
            "tool_calls": [{"id": "c1", "name": "get_server_stats", "input": {}}],
        }]

        assistant = OpenAIProvider._translate_messages(messages)[0]

        assert assistant["content"] == "Checking now."
        assert assistant["tool_calls"][0]["function"]["arguments"] == "{}"

    def test_openai_shaped_tool_calls_pass_through_unchanged(self):
        msg = {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "c1",
                "type": "function",
                "function": {"name": "get_status", "arguments": '{"name": "plex"}'},
            }],
        }

        assert OpenAIProvider._translate_messages([msg]) == [msg]
