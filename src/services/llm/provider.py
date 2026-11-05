"""LLM provider protocol and shared data types."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

_usage_logger = logging.getLogger("src.services.llm.usage")


def usage_counts(obj: Any, names: tuple[str, ...]) -> dict[str, int]:
    """Read integer token counts off an SDK usage object, skipping absent ones."""
    out: dict[str, int] = {}
    for name in names:
        value = getattr(obj, name, None)
        if isinstance(value, int) and not isinstance(value, bool):
            out[name] = value
    return out


@dataclass
class ToolCall:
    """A normalized tool call from any LLM provider."""
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class LLMResponse:
    """Normalized response from any LLM provider."""
    text: str
    stop_reason: str  # "end", "tool_use", "max_tokens"
    tool_calls: list[ToolCall] | None = None
    # Token counts as the provider reported them: input, output, and for
    # Anthropic the prompt-cache read/write split. Empty when not reported.
    usage: dict[str, int] = field(default_factory=dict)


def log_usage(provider: str, model: str, usage: dict[str, int]) -> None:
    """One INFO line per LLM call, so spend is visible in the container log."""
    if usage:
        parts = " ".join(f"{k}={v}" for k, v in usage.items())
        _usage_logger.info(f"LLM call {provider}/{model}: {parts}")
    else:
        _usage_logger.info(f"LLM call {provider}/{model}: usage not reported")


@dataclass
class ModelInfo:
    """Information about an available model."""
    id: str
    name: str
    provider: str
    supports_tools: bool = True


@runtime_checkable
class LLMProvider(Protocol):
    """Protocol for LLM providers."""

    async def chat(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        max_tokens: int = 1024,
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse: ...

    @property
    def supports_tools(self) -> bool: ...

    @property
    def model_name(self) -> str: ...

    @property
    def provider_name(self) -> str: ...
