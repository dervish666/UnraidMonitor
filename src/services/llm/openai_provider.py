"""OpenAI LLM provider wrapping AsyncOpenAI with function calling translation."""

from __future__ import annotations

import json
import logging
from typing import Any

from src.services.llm.provider import LLMResponse, ToolCall

logger = logging.getLogger(__name__)

# Map OpenAI finish reasons to normalized values.
_STOP_REASON_MAP: dict[str, str] = {
    "stop": "end",
    "tool_calls": "tool_use",
    "length": "max_tokens",
    "content_filter": "end",
}


class OpenAIProvider:
    """LLM provider backed by OpenAI's Chat Completions API.

    Wraps ``openai.AsyncOpenAI`` with:
    - System prompt prepended as a ``{"role": "system", ...}`` message
    - Tool definition translation from normalized (Anthropic-style) to OpenAI function format
    - Stop-reason normalization
    - Tool-result and assistant message translation between formats

    Also serves as the base for OllamaProvider since Ollama exposes an
    OpenAI-compatible API.
    """

    def __init__(
        self,
        client: Any,
        model: str,
        supports_tools: bool = True,
    ) -> None:
        self._client = client
        self._model = model
        self._supports_tools = supports_tools

    # -- Protocol properties --------------------------------------------------

    @property
    def supports_tools(self) -> bool:
        return self._supports_tools

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def provider_name(self) -> str:
        return "openai"

    # -- Core API -------------------------------------------------------------

    async def chat(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        max_tokens: int = 1024,
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        """Send a chat request to the OpenAI Chat Completions API.

        Args:
            messages: Conversation messages in the normalized format.
            system: Optional system prompt text.
            max_tokens: Maximum tokens in the response.
            tools: Optional list of tool definitions in normalized format.

        Returns:
            Normalized LLMResponse.
        """
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "messages": self._translate_messages(messages, system),
        }

        # Only pass tools if supported and provided
        if tools and self._supports_tools:
            kwargs["tools"] = self._translate_tools(tools)

        response = await self._client.chat.completions.create(**kwargs)
        return self._parse_response(response)

    # -- Internal helpers -----------------------------------------------------

    @staticmethod
    def _translate_messages(
        messages: list[dict[str, Any]],
        system: str | None = None,
    ) -> list[dict[str, Any]]:
        """Translate normalized messages to OpenAI format.

        Transformations:
        - Prepend system prompt as ``{"role": "system", ...}`` if provided
        - Convert ``tool_result`` messages to ``{"role": "tool", "tool_call_id": ..., ...}``
        - Convert assistant messages with Anthropic-style content blocks (list of dicts)
          to OpenAI format with ``tool_calls`` field
        """
        result: list[dict[str, Any]] = []

        # Prepend system prompt
        if system is not None:
            result.append({"role": "system", "content": system})

        for msg in messages:
            role = msg.get("role")

            if role == "tool_result":
                # Translate tool_result to OpenAI's tool role
                result.append({
                    "role": "tool",
                    "tool_call_id": msg["tool_use_id"],
                    "content": msg["content"],
                })

            elif role == "assistant" and isinstance(msg.get("content"), list):
                # Translate Anthropic-style content blocks to OpenAI format
                result.append(_translate_assistant_content_blocks(msg))

            elif role == "assistant" and _has_normalized_tool_calls(msg):
                # NLProcessor's shape: {"content": str, "tool_calls": [{id, name, input}]}.
                # Passing it through unchanged sent tool calls with no type and no
                # function.arguments, which Ollama and OpenAI reject (issue #7).
                result.append(_translate_normalized_tool_calls(msg))

            else:
                # Pass through user, assistant (string content), etc.
                result.append(msg)

        return result

    @staticmethod
    def _translate_tools(
        tools: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Translate normalized tool definitions to OpenAI function format.

        Normalized format (Anthropic-style)::

            {"name": "...", "description": "...", "input_schema": {...}}

        OpenAI format::

            {"type": "function", "function": {"name": "...", "description": "...", "parameters": {...}}}
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": tool["input_schema"],
                },
            }
            for tool in tools
        ]

    @staticmethod
    def _parse_response(response: Any) -> LLMResponse:
        """Convert an OpenAI ChatCompletion response to a normalized LLMResponse."""
        if not response.choices:
            return LLMResponse(text="", stop_reason="end", tool_calls=None)

        choice = response.choices[0]
        message = choice.message

        text = message.content or ""

        tool_calls: list[ToolCall] = []
        if message.tool_calls:
            for tc in message.tool_calls:
                try:
                    input_data = json.loads(tc.function.arguments)
                except (json.JSONDecodeError, TypeError):
                    logger.warning(
                        f"Skipping tool call with invalid arguments: {tc.function.name}"
                    )
                    continue
                tool_calls.append(
                    ToolCall(
                        id=tc.id,
                        name=tc.function.name,
                        input=input_data,
                    )
                )

        stop_reason = _STOP_REASON_MAP.get(
            choice.finish_reason, choice.finish_reason
        )

        return LLMResponse(
            text=text,
            stop_reason=stop_reason,
            tool_calls=tool_calls if tool_calls else None,
        )


def _has_normalized_tool_calls(msg: dict[str, Any]) -> bool:
    """True if ``tool_calls`` is the normalized ``{id, name, input}`` shape.

    Tool calls already in OpenAI shape (with a ``function`` key) are left alone.
    """
    tool_calls = msg.get("tool_calls")
    return (
        isinstance(tool_calls, list)
        and bool(tool_calls)
        and all(isinstance(tc, dict) and "function" not in tc for tc in tool_calls)
    )


def _translate_normalized_tool_calls(msg: dict[str, Any]) -> dict[str, Any]:
    """Translate a normalized assistant tool-call message to OpenAI format.

    Normalized format (what NLProcessor builds)::

        {"role": "assistant", "content": "...", "tool_calls": [
            {"id": "...", "name": "...", "input": {...}},
        ]}

    OpenAI format::

        {"role": "assistant", "content": "..." | None, "tool_calls": [
            {"id": "...", "type": "function", "function": {"name": "...", "arguments": "..."}},
        ]}
    """
    return {
        "role": "assistant",
        "content": msg.get("content") or None,
        "tool_calls": [
            {
                "id": tc["id"],
                "type": "function",
                "function": {
                    "name": tc["name"],
                    "arguments": json.dumps(tc.get("input") or {}),
                },
            }
            for tc in msg["tool_calls"]
        ],
    }


def _translate_assistant_content_blocks(msg: dict[str, Any]) -> dict[str, Any]:
    """Translate an assistant message with Anthropic-style content blocks to OpenAI format.

    Anthropic format::

        {"role": "assistant", "content": [
            {"type": "text", "text": "..."},
            {"type": "tool_use", "id": "...", "name": "...", "input": {...}},
        ]}

    OpenAI format::

        {"role": "assistant", "content": "...", "tool_calls": [
            {"id": "...", "type": "function", "function": {"name": "...", "arguments": "..."}},
        ]}
    """
    content_blocks = msg["content"]
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []

    for block in content_blocks:
        if block.get("type") == "text":
            text_parts.append(block["text"])
        elif block.get("type") == "tool_use":
            tool_calls.append({
                "id": block["id"],
                "type": "function",
                "function": {
                    "name": block["name"],
                    "arguments": json.dumps(block.get("input", {})),
                },
            })

    result: dict[str, Any] = {"role": "assistant"}

    if tool_calls:
        # When there are tool calls, content can be the text or None
        result["content"] = "\n".join(text_parts) if text_parts else None
        result["tool_calls"] = tool_calls
    else:
        # Text-only content blocks
        result["content"] = "\n".join(text_parts)

    return result
