"""Anthropic LLM provider wrapping AsyncAnthropic with prompt caching."""

from __future__ import annotations

import logging
from typing import Any

from src.services.llm.provider import LLMResponse, ToolCall

logger = logging.getLogger(__name__)

# Map Anthropic stop reasons to normalized values.
_STOP_REASON_MAP: dict[str, str] = {
    "end_turn": "end",
    "tool_use": "tool_use",
    "max_tokens": "max_tokens",
    "stop_sequence": "end",
}


class AnthropicProvider:
    """LLM provider backed by Anthropic's Messages API.

    Wraps ``anthropic.AsyncAnthropic`` with:
    - Prompt caching (``cache_control``) on system prompts and the last tool definition
    - Stop-reason normalization
    - Tool-result message translation from the normalized format
    """

    def __init__(self, client: Any, model: str) -> None:
        self._client = client
        self._model = model

    # -- Protocol properties --------------------------------------------------

    @property
    def supports_tools(self) -> bool:
        return True

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def provider_name(self) -> str:
        return "anthropic"

    # -- Core API -------------------------------------------------------------

    async def chat(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        max_tokens: int = 1024,
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        """Send a chat request to the Anthropic Messages API.

        Args:
            messages: Conversation messages in the normalized format.
            system: Optional system prompt text.
            max_tokens: Maximum tokens in the response.
            tools: Optional list of tool definitions.

        Returns:
            Normalized LLMResponse.
        """
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "messages": self._translate_messages(messages),
        }

        # System prompt with cache_control for prompt caching
        if system is not None:
            kwargs["system"] = [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ]

        # Tools with cache_control on the last definition
        if tools:
            kwargs["tools"] = self._apply_tool_caching(tools)

        response = await self._client.messages.create(**kwargs)
        return self._parse_response(response)

    # -- Internal helpers -----------------------------------------------------

    @staticmethod
    def _translate_messages(
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Translate normalized messages to Anthropic format.

        The main transformation is converting ``tool_result`` messages from the
        normalized flat format::

            {"role": "tool_result", "tool_use_id": "...", "content": "..."}

        into Anthropic's nested ``user`` message with a ``tool_result`` content
        block::

            {"role": "user", "content": [{"type": "tool_result", ...}]}

        Consecutive tool_result messages are merged into a single ``user``
        message with multiple content blocks (Anthropic requirement).
        """
        result: list[dict[str, Any]] = []

        for msg in messages:
            if msg.get("role") == "tool_result":
                tool_block = {
                    "type": "tool_result",
                    "tool_use_id": msg["tool_use_id"],
                    "content": msg["content"],
                }
                # Merge consecutive tool_results into a single user message
                if result and result[-1].get("role") == "user" and isinstance(
                    result[-1].get("content"), list
                ) and all(
                    isinstance(b, dict) and b.get("type") == "tool_result"
                    for b in result[-1]["content"]
                ):
                    result[-1]["content"].append(tool_block)
                else:
                    result.append({"role": "user", "content": [tool_block]})
            elif msg.get("role") == "assistant" and "tool_calls" in msg:
                content: list[dict[str, Any]] = []
                text = msg.get("content", "")
                if text:
                    content.append({"type": "text", "text": text})
                for tc in msg["tool_calls"]:
                    content.append({
                        "type": "tool_use",
                        "id": tc["id"],
                        "name": tc["name"],
                        "input": tc["input"],
                    })
                result.append({"role": "assistant", "content": content})
            else:
                result.append(msg)

        return result

    @staticmethod
    def _apply_tool_caching(
        tools: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Return a copy of *tools* with ``cache_control`` on the last entry.

        The original list is never mutated.
        """
        if not tools:
            return tools

        cached = list(tools)  # shallow copy of the list
        cached[-1] = {**cached[-1], "cache_control": {"type": "ephemeral"}}
        return cached

    @staticmethod
    def _parse_response(response: Any) -> LLMResponse:
        """Convert an Anthropic response object to a normalized LLMResponse."""
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []

        for block in (response.content or []):
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(id=block.id, name=block.name, input=block.input)
                )

        stop_reason = _STOP_REASON_MAP.get(
            response.stop_reason, response.stop_reason
        )

        return LLMResponse(
            text="\n".join(text_parts),
            stop_reason=stop_reason,
            tool_calls=tool_calls if tool_calls else None,
        )
