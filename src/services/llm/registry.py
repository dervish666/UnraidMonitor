"""ProviderRegistry — central orchestrator for multi-provider LLM model selection."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.services.llm.anthropic_provider import AnthropicProvider
from src.services.llm.ollama_provider import OllamaProvider
from src.services.llm.openai_provider import OpenAIProvider
from src.services.llm.provider import LLMProvider, ModelInfo

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Well-known models per provider
# ---------------------------------------------------------------------------

_ANTHROPIC_MODELS: list[ModelInfo] = [
    ModelInfo(id="claude-sonnet-4-5", name="Claude Sonnet 4.5", provider="anthropic"),
    ModelInfo(id="claude-haiku-4-5", name="Claude Haiku 4.5", provider="anthropic"),
]

_OPENAI_MODELS: list[ModelInfo] = [
    ModelInfo(id="gpt-4o", name="GPT-4o", provider="openai"),
    ModelInfo(id="gpt-4o-mini", name="GPT-4o Mini", provider="openai"),
    ModelInfo(id="gpt-4.1", name="GPT-4.1", provider="openai"),
    ModelInfo(id="gpt-4.1-mini", name="GPT-4.1 Mini", provider="openai"),
    ModelInfo(id="gpt-4.1-nano", name="GPT-4.1 Nano", provider="openai"),
]

_PERSISTENCE_FILENAME = "model_selection.json"


@dataclass
class ProviderInfo:
    """Summary of a configured provider and its available models."""

    name: str
    display_name: str
    available_models: list[ModelInfo] = field(default_factory=list)


class ProviderRegistry:
    """Manages available LLM providers, model selection, and per-feature overrides.

    The registry is the single source of truth for which LLM provider/model to
    use.  AI consumers call ``get_provider(feature=...)`` and receive a ready-to-use
    ``LLMProvider`` instance.

    Constructor accepts optional clients for each provider, plus a default model
    and per-feature override map.  On startup, loads any persisted model selection
    from ``data/model_selection.json`` (overrides the constructor default).
    """

    def __init__(
        self,
        *,
        anthropic_client: Any | None = None,
        openai_client: Any | None = None,
        ollama_client: Any | None = None,
        ollama_models: list[ModelInfo] | None = None,
        default_model: str | None = None,
        feature_models: dict[str, str] | None = None,
        data_dir: str | None = None,
        ollama_default_model: str = "qwen2.5:7b",
    ) -> None:
        # Store raw clients
        self._anthropic_client = anthropic_client
        self._openai_client = openai_client
        self._ollama_client = ollama_client
        self._ollama_models: list[ModelInfo] = ollama_models or []
        self._ollama_default_model = ollama_default_model

        # Per-feature model overrides (feature_name -> model_id)
        self._feature_models: dict[str, str] = feature_models or {}

        # Persistence path
        self._data_dir = data_dir or "data"

        # Determine default model/provider
        self._default_provider_name: str | None = None
        self._default_model_name: str | None = None

        # Try to load persisted selection first
        persisted = self._load_persisted_selection()
        if persisted and self._has_provider(persisted[0]):
            self._default_provider_name = persisted[0]
            self._default_model_name = persisted[1]
        elif default_model:
            provider_name = self._detect_provider(default_model)
            if provider_name:
                self._default_provider_name = provider_name
                self._default_model_name = default_model
            else:
                # default_model can't be served by any configured provider; auto-select
                self._auto_select_provider()
        else:
            # No default_model specified; auto-select the first available provider
            self._auto_select_provider()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_provider(self, feature: str = "default") -> LLMProvider | None:
        """Return the LLMProvider for a given feature.

        Checks per-feature overrides first, then falls back to the global
        default.  Returns ``None`` if no provider is configured.
        """
        # Check feature override
        if feature != "default" and feature in self._feature_models:
            override_model = self._feature_models[feature]
            override_provider_name = self._detect_provider(override_model)
            if override_provider_name:
                provider = self._create_provider(override_provider_name, override_model)
                if provider is not None:
                    return provider
            # Fall through to default if override can't be fulfilled

        # Global default
        if self._default_provider_name and self._default_model_name:
            return self._create_provider(
                self._default_provider_name, self._default_model_name
            )

        return None

    def set_model(self, provider_name: str, model_name: str) -> None:
        """Switch the global default model and persist to disk.

        Args:
            provider_name: One of ``"anthropic"``, ``"openai"``, ``"ollama"``.
            model_name: The model ID (e.g. ``"gpt-4o"``, ``"claude-sonnet-4-5"``).
        """
        self._default_provider_name = provider_name
        self._default_model_name = model_name
        self._persist_selection(provider_name, model_name)

    def get_available_providers(self) -> list[ProviderInfo]:
        """Return info about all configured providers and their models."""
        providers: list[ProviderInfo] = []

        if self._anthropic_client is not None:
            providers.append(
                ProviderInfo(
                    name="anthropic",
                    display_name="Anthropic",
                    available_models=list(_ANTHROPIC_MODELS),
                )
            )

        if self._openai_client is not None:
            providers.append(
                ProviderInfo(
                    name="openai",
                    display_name="OpenAI",
                    available_models=list(_OPENAI_MODELS),
                )
            )

        if self._ollama_client is not None:
            providers.append(
                ProviderInfo(
                    name="ollama",
                    display_name="Ollama",
                    available_models=list(self._ollama_models),
                )
            )

        return providers

    def get_current_model(self) -> tuple[str, str] | None:
        """Return ``(provider_name, model_name)`` for the global default, or ``None``."""
        if self._default_provider_name and self._default_model_name:
            return (self._default_provider_name, self._default_model_name)
        return None

    # ------------------------------------------------------------------
    # Provider auto-detection
    # ------------------------------------------------------------------

    def _auto_select_provider(self) -> None:
        """Pick the first available provider in priority order: anthropic > openai > ollama.

        For Anthropic and OpenAI, selects the first well-known model for that provider.
        For Ollama, uses ``self._ollama_default_model`` rather than the first
        arbitrarily-ordered discovered model.

        Modifies ``_default_provider_name`` and ``_default_model_name`` in place.
        If no provider is available both attributes remain ``None``.
        """
        if self._anthropic_client is not None:
            self._default_provider_name = "anthropic"
            self._default_model_name = _ANTHROPIC_MODELS[0].id
        elif self._openai_client is not None:
            self._default_provider_name = "openai"
            self._default_model_name = _OPENAI_MODELS[0].id
        elif self._ollama_client is not None and self._ollama_models:
            self._default_provider_name = "ollama"
            self._default_model_name = self._ollama_default_model

    def _detect_provider(self, model_name: str) -> str | None:
        """Detect which provider should serve *model_name*.

        Rules:
        1. ``claude-*`` -> anthropic
        2. ``gpt-*``, ``o1*``, ``o3*``, ``o4*`` -> openai
        3. Known ollama model -> ollama
        4. Unknown -> ollama (if available), else anthropic (if available)
        5. None if nothing available
        """
        # Anthropic models
        if model_name.startswith("claude-"):
            if self._anthropic_client is not None:
                return "anthropic"
            return None

        # OpenAI models
        if (
            model_name.startswith("gpt-")
            or model_name.startswith("o1")
            or model_name.startswith("o3")
            or model_name.startswith("o4")
        ):
            if self._openai_client is not None:
                return "openai"
            return None

        # Known ollama model
        ollama_ids = {m.id for m in self._ollama_models}
        if model_name in ollama_ids:
            if self._ollama_client is not None:
                return "ollama"
            return None

        # Unknown model — prefer ollama (local), then anthropic
        if self._ollama_client is not None:
            return "ollama"
        if self._anthropic_client is not None:
            return "anthropic"

        return None

    # ------------------------------------------------------------------
    # Provider instantiation
    # ------------------------------------------------------------------

    def _create_provider(
        self, provider_name: str, model_name: str
    ) -> LLMProvider | None:
        """Instantiate a provider for the given name and model."""
        if provider_name == "anthropic" and self._anthropic_client is not None:
            return AnthropicProvider(client=self._anthropic_client, model=model_name)

        if provider_name == "openai" and self._openai_client is not None:
            return OpenAIProvider(client=self._openai_client, model=model_name)

        if provider_name == "ollama" and self._ollama_client is not None:
            # Determine tool support from discovered models
            supports_tools = False
            for m in self._ollama_models:
                if m.id == model_name:
                    supports_tools = m.supports_tools
                    break
            return OllamaProvider(
                client=self._ollama_client,
                model=model_name,
                supports_tools=supports_tools,
            )

        return None

    def _has_provider(self, provider_name: str) -> bool:
        """Check if a provider's client is configured."""
        if provider_name == "anthropic":
            return self._anthropic_client is not None
        if provider_name == "openai":
            return self._openai_client is not None
        if provider_name == "ollama":
            return self._ollama_client is not None
        return False

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _persistence_path(self) -> Path:
        return Path(self._data_dir) / _PERSISTENCE_FILENAME

    def _load_persisted_selection(self) -> tuple[str, str] | None:
        """Load ``(provider, model)`` from JSON, or ``None`` if unavailable."""
        path = self._persistence_path()
        if not path.exists():
            return None

        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            provider = data.get("provider")
            model = data.get("model")
            if isinstance(provider, str) and isinstance(model, str):
                return (provider, model)
        except (json.JSONDecodeError, OSError, KeyError) as exc:
            logger.warning("Failed to load persisted model selection: %s", exc)

        return None

    def _persist_selection(self, provider_name: str, model_name: str) -> None:
        """Write model selection to ``data/model_selection.json``."""
        path = self._persistence_path()
        path.parent.mkdir(parents=True, exist_ok=True)

        data = {"provider": provider_name, "model": model_name}
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except OSError as exc:
            logger.error("Failed to persist model selection: %s", exc)
