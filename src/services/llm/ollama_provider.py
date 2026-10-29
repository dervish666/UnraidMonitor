"""Ollama LLM provider extending OpenAIProvider with model discovery."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

from src.constants import (
    MODEL_DISCOVERY_TIMEOUT_SECONDS,
    OLLAMA_SHOW_CONCURRENCY,
    OLLAMA_SHOW_TIMEOUT_SECONDS,
)
from src.services.llm.openai_provider import OpenAIProvider
from src.services.llm.provider import ModelInfo

logger = logging.getLogger(__name__)

# Capabilities Ollama reported via /api/show, keyed on (host, model name,
# digest) so a re-pulled model is asked again. ``None`` means this Ollama
# answered but has no ``capabilities`` field (older versions), and the
# family guess applies. Failed lookups are not cached, so the next discovery
# retries them.
_capability_cache: dict[tuple[str, str, str], frozenset[str] | None] = {}

# Fallback only, for an Ollama too old to report capabilities or one whose
# /api/show failed. Prefixes because Ollama versions the family name: qwen2,
# qwen3, qwen35, gemma3 (issue #7). A prefix is a guess: gemma2 matches
# "gemma" but has no tool support, which is why /api/show is asked first.
_TOOL_CAPABLE_FAMILIES: frozenset[str] = frozenset({
    "llama",
    "mistral",
    "qwen",
    "command-r",
    "gemma",
})


def _family_supports_tools(details: dict[str, Any]) -> bool:
    """True if ``details.family`` or any of ``details.families`` is tool-capable."""
    names: list[str] = []
    family = details.get("family")
    if isinstance(family, str):
        names.append(family)
    families = details.get("families")
    if isinstance(families, list):
        names.extend(f for f in families if isinstance(f, str))
    return any(
        name.lower().startswith(prefix)
        for name in names
        for prefix in _TOOL_CAPABLE_FAMILIES
    )


class _ShowFailed(Exception):
    """An /api/show lookup that produced no answer (error, timeout, bad body)."""


async def _fetch_capabilities(
    base: str,
    model_id: str,
    session: aiohttp.ClientSession,
) -> frozenset[str] | None:
    """Ask ``POST {base}/api/show`` for a model's capabilities.

    Returns the capability names, or ``None`` when Ollama answered without a
    ``capabilities`` field. Raises ``_ShowFailed`` when there is no usable answer.
    """
    try:
        async with session.post(
            f"{base}/api/show",
            # "model" is the current field; "name" is what older servers read.
            json={"model": model_id, "name": model_id},
            timeout=aiohttp.ClientTimeout(total=OLLAMA_SHOW_TIMEOUT_SECONDS),
        ) as resp:
            if resp.status != 200:
                raise _ShowFailed(f"HTTP {resp.status}")
            body = await resp.json()
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
        raise _ShowFailed(f"{type(exc).__name__}: {exc}") from exc

    if not isinstance(body, dict):
        raise _ShowFailed("response is not a JSON object")
    caps = body.get("capabilities")
    if caps is None:
        return None
    if not isinstance(caps, list):
        raise _ShowFailed(f"capabilities is {type(caps).__name__}, not a list")
    return frozenset(c for c in caps if isinstance(c, str))


async def _lookup_capabilities(
    base: str,
    entries: list[tuple[str, str]],
    session: aiohttp.ClientSession,
) -> dict[str, frozenset[str] | None]:
    """Resolve capabilities for ``(model_id, digest)`` pairs, using the cache.

    Models whose lookup failed or ran past the shared budget are absent from
    the result; the caller falls back to the family guess for those.
    """
    found: dict[str, frozenset[str] | None] = {}
    todo: list[tuple[str, str]] = []
    for model_id, digest in entries:
        key = (base, model_id, digest)
        if key in _capability_cache:
            found[model_id] = _capability_cache[key]
        else:
            todo.append((model_id, digest))
    if not todo:
        return found

    gate = asyncio.Semaphore(OLLAMA_SHOW_CONCURRENCY)
    failures: dict[str, str] = {}

    async def one(model_id: str, digest: str) -> None:
        async with gate:
            try:
                caps = await _fetch_capabilities(base, model_id, session)
            except _ShowFailed as exc:
                failures[model_id] = str(exc)
                return
        _capability_cache[(base, model_id, digest)] = caps
        found[model_id] = caps

    tasks = [asyncio.ensure_future(one(m, d)) for m, d in todo]
    _, pending = await asyncio.wait(tasks, timeout=MODEL_DISCOVERY_TIMEOUT_SECONDS)
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
        for model_id, _ in todo:
            if model_id not in found and model_id not in failures:
                failures[model_id] = (
                    f"no answer within the {MODEL_DISCOVERY_TIMEOUT_SECONDS}s discovery budget"
                )

    for model_id, reason in failures.items():
        logger.debug("Ollama /api/show failed for %s: %s", model_id, reason)
    if failures and len(failures) == len(todo):
        logger.warning(
            "Ollama /api/show failed for all %d model(s) (e.g. %s: %s); "
            "guessing tool support from the model family instead",
            len(todo), *next(iter(failures.items())),
        )
    return found


class OllamaProvider(OpenAIProvider):
    """LLM provider for Ollama, which exposes an OpenAI-compatible API.

    Ollama serves models at ``{host}/v1/`` using the same Chat Completions
    format as OpenAI.  The only Ollama-specific functionality is model
    discovery via the native ``/api/tags`` endpoint.

    Usage::

        from openai import AsyncOpenAI

        client = AsyncOpenAI(base_url="http://localhost:11434/v1", api_key="ollama")
        provider = OllamaProvider(client=client, model="llama3.1:8b")
    """

    def __init__(
        self,
        client: Any,
        model: str,
        supports_tools: bool = False,
    ) -> None:
        super().__init__(client=client, model=model, supports_tools=supports_tools)

    # -- Protocol properties (override) ----------------------------------------

    @property
    def provider_name(self) -> str:
        return "ollama"

    # -- Ollama-specific model discovery ---------------------------------------

    @staticmethod
    async def discover_models(
        host: str,
        session: aiohttp.ClientSession,
    ) -> list[ModelInfo]:
        """Query an Ollama instance for available models.

        Calls ``GET {host}/api/tags`` and returns a list of ``ModelInfo``
        objects. Tool support comes from the ``capabilities`` that
        ``POST {host}/api/show`` reports for each model, cached per model
        digest. Where that lookup fails, or the server is too old to report
        capabilities, it is guessed from the model's family.

        Args:
            host: Ollama base URL, e.g. ``http://localhost:11434``.
            session: An ``aiohttp.ClientSession`` to use for the request.

        Returns:
            List of discovered models, or empty list on any error.
        """
        url = f"{host.rstrip('/')}/api/tags"

        try:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=MODEL_DISCOVERY_TIMEOUT_SECONDS)
            ) as resp:
                if resp.status != 200:
                    logger.warning(
                        "Ollama model discovery returned status %d", resp.status
                    )
                    return []

                data = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            logger.debug("Ollama model discovery failed: %s", exc)
            return []

        entries: list[tuple[str, dict[str, Any], str]] = []
        for entry in data.get("models", []):
            model_id = entry.get("name", entry.get("model", "unknown"))
            details = entry.get("details") or {}
            digest = entry.get("digest") or ""
            entries.append((model_id, details, digest if isinstance(digest, str) else ""))

        capabilities = await _lookup_capabilities(
            host.rstrip("/"), [(m, d) for m, _, d in entries], session
        )

        models: list[ModelInfo] = []
        for model_id, details, _ in entries:
            caps = capabilities.get(model_id)
            if caps is not None:
                supports_tools = "tools" in caps
            else:
                # No answer, or an Ollama too old to report capabilities.
                supports_tools = _family_supports_tools(details)

            models.append(
                ModelInfo(
                    id=model_id,
                    name=model_id,
                    provider="ollama",
                    supports_tools=supports_tools,
                )
            )

        return models
