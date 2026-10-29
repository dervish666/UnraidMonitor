# tests/test_nl_openai_tool_loop.py
"""NLProcessor driving a real OpenAIProvider/OllamaProvider (only the HTTP client is
mocked), so the message history NLProcessor builds is checked against what the
OpenAI-compatible API actually receives. Issue #7: each half had tests, but the
shape NLProcessor sent was never fed through the provider's translator, and the
second request in every tool loop was rejected with 400."""
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.llm.ollama_provider import OllamaProvider
from src.services.llm.openai_provider import OpenAIProvider
from src.services.nl_processor import NLProcessor


def _completion(content: str | None, finish_reason: str, tool_calls: list | None = None):
    message = MagicMock()
    message.content = content
    message.tool_calls = tool_calls
    choice = MagicMock()
    choice.message = message
    choice.finish_reason = finish_reason
    response = MagicMock()
    response.choices = [choice]
    return response


def _api_tool_call(call_id: str, name: str, arguments: dict) -> MagicMock:
    tc = MagicMock()
    tc.id = call_id
    tc.type = "function"
    tc.function = MagicMock()
    tc.function.name = name
    tc.function.arguments = json.dumps(arguments)
    return tc


@pytest.mark.parametrize("provider_cls", [OpenAIProvider, OllamaProvider])
async def test_second_request_carries_openai_shaped_tool_history(provider_cls):
    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=[
        _completion(
            None,
            "tool_calls",
            [_api_tool_call("call_1", "get_container_status", {"name": "plex"})],
        ),
        _completion("Plex is running.", "stop"),
    ])
    provider = provider_cls(client=client, model="qwen3.5:4b", supports_tools=True)
    executor = MagicMock()
    executor.execute = AsyncMock(return_value="plex: running, healthy")
    processor = NLProcessor(provider=provider, tool_executor=executor)

    result = await processor.process(user_id=1, message="What is Plex's current status?")

    assert result.response == "Plex is running."
    executor.execute.assert_awaited_once_with("get_container_status", {"name": "plex"})
    assert client.chat.completions.create.await_count == 2

    first = client.chat.completions.create.await_args_list[0].kwargs
    assert first["tools"][0]["type"] == "function"

    second = client.chat.completions.create.await_args_list[1].kwargs["messages"]
    assert [m["role"] for m in second] == ["system", "user", "assistant", "tool"]

    assistant = second[2]
    assert assistant["content"] is None
    assert assistant["tool_calls"] == [{
        "id": "call_1",
        "type": "function",
        "function": {"name": "get_container_status", "arguments": '{"name": "plex"}'},
    }]
    # Every tool call must use the exact OpenAI keys, nothing normalized left over.
    for tc in assistant["tool_calls"]:
        assert set(tc) == {"id", "type", "function"}
        assert isinstance(tc["function"]["arguments"], str)

    assert second[3]["role"] == "tool"
    assert second[3]["tool_call_id"] == "call_1"
    assert "plex: running" in second[3]["content"]
