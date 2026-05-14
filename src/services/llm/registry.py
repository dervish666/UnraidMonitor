"""ProviderRegistry — central orchestrator for multi-provider LLM model selection."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.services.llm.anthropic_provider import AnthropicProvider
from src.services.llm.ollama_provider import OllamaProvider
from src.services.llm.openai_provider import OpenAIProvider
from src.services.llm.provider import LLMProvider, ModelInfo

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Model families — user-facing shorthand resolved to concrete IDs
# ---------------------------------------------------------------------------

_MODEL_FAMILIES: dict[str, str] = {
    "sonnet": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5-20251001",
    "opus": "claude-opus-4-6",
}

_FAMILY_PREFIX = re.compile(r"^claude-(\w+)-")

_MODEL_ALIASES: dict[str, str] = {
    "claude-sonnet-4-5": "claude-sonnet-4-6",
    "claude-sonnet-4-5-20250929": "claude-sonnet-4-6",
}

# ---------------------------------------------------------------------------
# Well-known models per provider (shown in /model UI)
# ---------------------------------------------------------------------------

_ANTHROPIC_FAMILY_MODELS: list[ModelInfo] = [
    ModelInfo(id="sonnet", name="Claude Sonnet (latest)", provider="anthropic"),
    ModelInfo(id="haiku", name="Claude Haiku (latest)", provider="anthropic"),
    ModelInfo(id="opus", name="Claude Opus (latest)", provider="anthropic"),
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

    Supports model **family names** (``"sonnet"``, ``"haiku"``, ``"opus"``) in
    addition to full model IDs.  Family names are resolved to concrete model IDs
    using API-discovered models when available, falling back to hardcoded defaults.
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
        discovered_anthropic_models: list[str] | None = None,
    ) -> None:
        # Store raw clients
        self._anthropic_client = anthropic_client
        self._openai_client = openai_client
        self._ollama_client = ollama_client
        self._ollama_models: list[ModelInfo] = ollama_models or []
        self._ollama_default_model = ollama_default_model

        # API-discovered Anthropic model IDs (populated at startup)
        self._discovered_anthropic: set[str] = set(discovered_anthropic_models or [])
        if self._discovered_anthropic:
            self._update_families_from_discovered()

        # Per-feature model overrides (feature_name -> model_id)
        self._feature_models: dict[str, str] = dict(feature_models or {})

        # Persistence path
        self._data_dir = data_dir or "data"

        # Determine default model/provider
        self._default_provider_name: str | None = None
        self._default_model_name: str | None = None

        # Try to load persisted selection first (may also merge feature overrides)
        persisted = self._load_persisted_selection()
        if persisted and self._has_provider(persisted[0]):
            self._default_provider_name = persisted[0]
            self._default_model_name = self._resolve_model(persisted[1])
        elif default_model:
            resolved = self._resolve_model(default_model)
            provider_name = self._detect_provider(resolved)
            if provider_name:
                self._default_provider_name = provider_name
                self._default_model_name = resolved
            else:
                self._auto_select_provider()
        else:
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
            override_model = self._resolve_model(self._feature_models[feature])
            override_provider_name = self._detect_provider(override_model)
            if override_provider_name:
                provider = self._create_provider(override_provider_name, override_model)
                if provider is not None:
                    return provider

        # Global default
        if self._default_provider_name and self._default_model_name:
            return self._create_provider(
                self._default_provider_name, self._default_model_name
            )

        return None

    def set_model(self, provider_name: str, model_name: str) -> None:
        """Switch the global default model and persist to disk.

        Accepts family names (``"sonnet"``) or full IDs (``"claude-sonnet-4-6"``).
        """
        resolved = self._resolve_model(model_name)
        self._default_provider_name = provider_name
        self._default_model_name = resolved
        self._persist_selection(provider_name, model_name)

    def set_feature_model(self, feature: str, model_name: str) -> str:
        """Set a per-feature model override and persist. Returns the resolved ID."""
        resolved = self._resolve_model(model_name)
        self._feature_models[feature] = resolved
        self._persist_selection(
            self._default_provider_name or "",
            self._default_model_name or "",
        )
        return resolved

    def clear_feature_model(self, feature: str) -> bool:
        """Remove a per-feature override so it falls back to the global default."""
        if feature in self._feature_models:
            del self._feature_models[feature]
            self._persist_selection(
                self._default_provider_name or "",
                self._default_model_name or "",
            )
            return True
        return False

    def get_feature_models(self) -> dict[str, str]:
        """Return a copy of the current per-feature model overrides."""
        return dict(self._feature_models)

    def get_available_providers(self) -> list[ProviderInfo]:
        """Return info about all configured providers and their models."""
        providers: list[ProviderInfo] = []

        if self._anthropic_client is not None:
            providers.append(
                ProviderInfo(
                    name="anthropic",
                    display_name="Anthropic",
                    available_models=list(_ANTHROPIC_FAMILY_MODELS),
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
    # Model family resolution
    # ------------------------------------------------------------------

    def _resolve_model(self, model_id: str) -> str:
        """Resolve family names, aliases, and retired models to concrete IDs."""
        # Family names (sonnet, haiku, opus)
        if model_id in _MODEL_FAMILIES:
            resolved = _MODEL_FAMILIES[model_id]
            logger.info("Resolved family '%s' to %s", model_id, resolved)
            return resolved

        # Retired model aliases
        replacement = _MODEL_ALIASES.get(model_id)
        if replacement:
            logger.warning("Model %s is retired, using %s instead", model_id, replacement)
            return replacement

        return model_id

    def _update_families_from_discovered(self) -> None:
        """Update family defaults using API-discovered models.

        For each family, finds the latest available model by sorting
        discovered IDs that match the family prefix.
        """
        for family in list(_MODEL_FAMILIES.keys()):
            prefix = f"claude-{family}-"
            matches = sorted(
                (m for m in self._discovered_anthropic if m.startswith(prefix)),
                key=self._model_sort_key,
                reverse=True,
            )
            if matches:
                # Prefer the alias (no date suffix) over dated versions
                alias = next((m for m in matches if not re.search(r"-\d{8}$", m)), None)
                best = alias or matches[0]
                if best != _MODEL_FAMILIES[family]:
                    logger.info(
                        "Updated '%s' family: %s -> %s (from API)",
                        family, _MODEL_FAMILIES[family], best,
                    )
                    _MODEL_FAMILIES[family] = best

    @staticmethod
    def _model_sort_key(model_id: str) -> tuple[int, int, str]:
        """Extract (major, minor, full_id) for sorting model versions."""
        m = re.search(r"-(\d+)-(\d+)", model_id)
        if m:
            return (int(m.group(1)), int(m.group(2)), model_id)
        return (0, 0, model_id)

    # ------------------------------------------------------------------
    # Provider auto-detection
    # ------------------------------------------------------------------

    def _auto_select_provider(self) -> None:
        """Pick the first available provider: anthropic > openai > ollama."""
        if self._anthropic_client is not None:
            self._default_provider_name = "anthropic"
            self._default_model_name = self._resolve_model("sonnet")
        elif self._openai_client is not None:
            self._default_provider_name = "openai"
            self._default_model_name = _OPENAI_MODELS[0].id
        elif self._ollama_client is not None and self._ollama_models:
            self._default_provider_name = "ollama"
            self._default_model_name = self._ollama_default_model

    def _detect_provider(self, model_name: str) -> str | None:
        """Detect which provider should serve *model_name*.

        Rules:
        1. Family names (sonnet, haiku, opus) -> anthropic
        2. ``claude-*`` -> anthropic
        3. ``gpt-*``, ``o1*``, ``o3*``, ``o4*`` -> openai
        4. Known ollama model -> ollama
        5. Unknown -> ollama (if available), else anthropic (if available)
        6. None if nothing available
        """
        # Family names
        if model_name in _MODEL_FAMILIES:
            if self._anthropic_client is not None:
                return "anthropic"
            return None

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
        """Load ``(provider, model)`` from JSON, or ``None`` if unavailable.

        Also merges any persisted per-feature overrides into ``_feature_models``.
        """
        path = self._persistence_path()
        if not path.exists():
            return None

        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)

            # Merge persisted per-feature overrides (take precedence over config)
            features = data.get("features")
            if isinstance(features, dict):
                for feat, model in features.items():
                    if isinstance(feat, str) and isinstance(model, str):
                        self._feature_models[feat] = model

            provider = data.get("provider")
            model = data.get("model")
            if isinstance(provider, str) and isinstance(model, str):
                return (provider, model)
        except (json.JSONDecodeError, OSError, KeyError) as exc:
            logger.warning("Failed to load persisted model selection: %s", exc)

        return None

    def _persist_selection(self, provider_name: str, model_name: str) -> None:
        """Write model selection and per-feature overrides to JSON."""
        path = self._persistence_path()
        path.parent.mkdir(parents=True, exist_ok=True)

        data: dict[str, Any] = {
            "provider": provider_name,
            "model": model_name,
        }
        if self._feature_models:
            data["features"] = dict(self._feature_models)

        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except OSError as exc:
            logger.error("Failed to persist model selection: %s", exc)
