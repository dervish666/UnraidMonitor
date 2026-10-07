"""ProviderRegistry — central orchestrator for multi-provider LLM model selection."""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
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
    "claude-sonnet-4-5": "sonnet",
    "claude-sonnet-4-5-20250929": "sonnet",
}

_FEATURE_DEFAULTS: dict[str, str] = {
    "nl_processor": "sonnet",
    "diagnostic": "haiku",
    "pattern_analyzer": "haiku",
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

# Format of model_selection.json. Files without a "version" key were written
# before v0.22.1, which saved resolved IDs ("claude-opus-4-8") in place of the
# family the user typed ("opus") and so froze the bot on whatever model was
# newest that day. A legacy file has its claude-* IDs converted back to family
# names on load. From version 2 on, a full ID in the file is a deliberate pin.
_PERSISTENCE_VERSION = 2


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
        config_path: str | None = None,
        ollama_default_model: str = "qwen2.5:7b",
        discovered_anthropic_models: list[str] | None = None,
        model_display_names: dict[str, str] | None = None,
        provider_problems: dict[str, str] | None = None,
    ) -> None:
        # Store raw clients
        self._anthropic_client = anthropic_client
        self._openai_client = openai_client
        self._ollama_client = ollama_client
        self._ollama_models: list[ModelInfo] = ollama_models or []
        self._ollama_default_model = ollama_default_model

        # Instance copy of model families (avoids mutating module-level dict)
        self._model_families: dict[str, str] = dict(_MODEL_FAMILIES)

        # API-discovered Anthropic model IDs (populated at startup)
        self._discovered_anthropic: set[str] = set(discovered_anthropic_models or [])
        if self._discovered_anthropic:
            self._update_families_from_discovered()

        # Human names from the provider ("Claude Sonnet 5.5"), keyed by model ID
        self._display_names: dict[str, str] = dict(model_display_names or {})

        # Providers whose client exists but which refused us at startup
        # (provider -> sentence such as "Anthropic rejected the API key")
        self._provider_problems: dict[str, str] = dict(provider_problems or {})

        # Per-feature model overrides (feature_name -> what the user chose,
        # a family name or a full ID). Resolved at use time, never stored resolved.
        self._feature_models: dict[str, str] = dict(feature_models or {})

        # Persistence paths
        self._data_dir = data_dir or "data"
        self._config_path = config_path

        # Provider instance cache keyed by (provider_name, model_name)
        self._provider_cache: dict[tuple[str, str], LLMProvider] = {}

        # Build Ollama model lookup for O(1) access
        self._ollama_tool_support: dict[str, bool] = {
            m.id: m.supports_tools for m in self._ollama_models
        }

        # Determine default model/provider. _default_model_input is what the
        # user chose (and what gets persisted); _default_model_name is its
        # resolution against this run's discovered models.
        self._default_provider_name: str | None = None
        self._default_model_name: str | None = None
        self._default_model_input: str | None = None

        # Try to load persisted selection first (may also merge feature overrides)
        persisted = self._load_persisted_selection()
        if persisted and self._has_provider(persisted[0]):
            self._set_default(persisted[0], persisted[1])
        elif default_model:
            resolved = self._resolve_model(default_model)
            provider_name = self._detect_provider(resolved)
            if provider_name:
                self._set_default(provider_name, default_model)
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
        route = self._route(feature)
        if route is None:
            return None
        return self._create_provider(*route)

    def resolved_model(self, feature: str = "default") -> tuple[str, str] | None:
        """Return ``(provider_name, model_id)`` that *feature* would use right now."""
        return self._route(feature)

    def describe_models(self) -> str:
        """One line naming the concrete model each feature resolves to, for the startup log."""
        parts: list[str] = []
        for feature in ("default", *_FEATURE_DEFAULTS):
            route = self._route(feature)
            if route is None:
                parts.append(f"{feature}=none")
                continue
            chosen = (
                self._feature_models.get(feature, self._default_model_input)
                if feature != "default"
                else self._default_model_input
            )
            source = f" (from '{chosen}')" if chosen and chosen != route[1] else ""
            parts.append(f"{feature}={route[0]}/{route[1]}{source}")
        return ", ".join(parts)

    def display_name(self, model_id: str) -> str:
        """Human name for a concrete model ID, e.g. ``Claude Sonnet 5.5``.

        Uses the name the provider reported at discovery, else derives one from
        a ``claude-<family>-<major>[-<minor>][-<date>]`` ID, else the ID itself.
        """
        if model_id in self._display_names:
            return self._display_names[model_id]
        match = re.fullmatch(r"claude-([a-z]+)-(\d+)(?:-(\d{1,2}))?(?:-\d{8})?", model_id)
        if match:
            family, major, minor = match.groups()
            version = f"{major}.{minor}" if minor else major
            return f"Claude {family.capitalize()} {version}"
        return model_id

    @property
    def provider_problems(self) -> dict[str, str]:
        """Providers that are configured but refused us, e.g. a rejected API key."""
        return dict(self._provider_problems)

    def resolve_model(self, model_id: str) -> str:
        """Resolve a family name or retired alias to the concrete ID it means today."""
        return self._resolve_model(model_id)

    def set_model(self, provider_name: str, model_name: str) -> None:
        """Switch the global default model and persist to disk.

        Accepts family names (``"sonnet"``) or full IDs (``"claude-sonnet-4-6"``).
        The name is persisted as given, so a family keeps tracking the newest model.
        """
        self._set_default(provider_name, model_name)
        self._provider_cache.clear()
        self._persist_selection()
        self._persist_to_config(default_model=model_name)

    def set_feature_model(self, feature: str, model_name: str) -> str:
        """Set a per-feature model override and persist. Returns the resolved ID.

        The name is stored as given (``"sonnet"`` stays ``"sonnet"``) and resolved
        each time it is used.
        """
        self._feature_models[feature] = model_name
        self._provider_cache.clear()
        self._persist_selection()
        self._persist_to_config(feature=feature, feature_model=model_name)
        return self._resolve_model(model_name)

    def clear_feature_model(self, feature: str) -> bool:
        """Remove a per-feature override so it falls back to the global default."""
        if feature in self._feature_models:
            del self._feature_models[feature]
            self._persist_selection()
            default = _FEATURE_DEFAULTS.get(feature)
            if default:
                self._persist_to_config(feature=feature, feature_model=default)
            return True
        return False

    def get_feature_models(self) -> dict[str, str]:
        """Return a copy of the per-feature overrides as chosen (family names or full IDs)."""
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
        if model_id in self._model_families:
            resolved = self._model_families[model_id]
            logger.info("Resolved family '%s' to %s", model_id, resolved)
            return resolved

        # Retired model aliases — recurse to resolve the replacement (which may
        # itself be a family name like "sonnet")
        replacement = _MODEL_ALIASES.get(model_id)
        if replacement:
            logger.warning("Model %s is retired, using %s instead", model_id, replacement)
            return self._resolve_model(replacement)

        return model_id

    def _update_families_from_discovered(self) -> None:
        """Update family defaults using API-discovered models.

        For each family, finds the latest available model by sorting
        discovered IDs that match the family prefix.
        """
        for family in list(self._model_families.keys()):
            prefix = f"claude-{family}-"
            matches = sorted(
                (m for m in self._discovered_anthropic if m.startswith(prefix)),
                key=self._model_sort_key,
                reverse=True,
            )
            if matches:
                alias = next((m for m in matches if not re.search(r"-\d{8}$", m)), None)
                best = alias or matches[0]
                if best != self._model_families[family]:
                    logger.info(
                        "Updated '%s' family: %s -> %s (from API)",
                        family, self._model_families[family], best,
                    )
                    self._model_families[family] = best

    @staticmethod
    def _model_sort_key(model_id: str) -> tuple[int, int, str]:
        """Extract (major, minor, full_id) for sorting model versions.

        The date suffix is stripped first: read as a version number it made
        claude-sonnet-4-20250514 outrank claude-sonnet-4-5, and a single-number
        ID such as claude-sonnet-5 matched nothing and sorted below both.
        """
        base = re.sub(r"-\d{8}$", "", model_id)
        nums = [int(n) for n in re.findall(r"-(\d{1,3})(?=-|$)", base)]
        major = nums[0] if nums else 0
        minor = nums[1] if len(nums) > 1 else 0
        return (major, minor, model_id)

    # ------------------------------------------------------------------
    # Provider auto-detection
    # ------------------------------------------------------------------

    def _set_default(self, provider_name: str, model_input: str) -> None:
        """Record the global default as chosen and as resolved for this run."""
        self._default_provider_name = provider_name
        self._default_model_input = model_input
        self._default_model_name = self._resolve_model(model_input)

    def _route(self, feature: str) -> tuple[str, str] | None:
        """``(provider, model_id)`` for *feature*: its override if usable, else the default."""
        if feature != "default" and feature in self._feature_models:
            override_model = self._resolve_model(self._feature_models[feature])
            override_provider_name = self._detect_provider(override_model)
            if override_provider_name:
                return (override_provider_name, override_model)

        if self._default_provider_name and self._default_model_name:
            return (self._default_provider_name, self._default_model_name)
        return None

    def _auto_select_provider(self) -> None:
        """Pick the first available provider: anthropic > openai > ollama."""
        if self._anthropic_client is not None:
            self._set_default("anthropic", "sonnet")
        elif self._openai_client is not None:
            self._set_default("openai", _OPENAI_MODELS[0].id)
        elif self._ollama_client is not None and self._ollama_models:
            self._set_default("ollama", self._ollama_default_model)

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
        if model_name in self._model_families:
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
        """Return a cached provider or instantiate a new one."""
        cache_key = (provider_name, model_name)
        cached = self._provider_cache.get(cache_key)
        if cached is not None:
            return cached

        provider: LLMProvider | None = None
        if provider_name == "anthropic" and self._anthropic_client is not None:
            provider = AnthropicProvider(client=self._anthropic_client, model=model_name)
        elif provider_name == "openai" and self._openai_client is not None:
            provider = OpenAIProvider(client=self._openai_client, model=model_name)
        elif provider_name == "ollama" and self._ollama_client is not None:
            supports_tools = self._ollama_tool_support.get(model_name, False)
            provider = OllamaProvider(
                client=self._ollama_client,
                model=model_name,
                supports_tools=supports_tools,
            )

        if provider is not None:
            self._provider_cache[cache_key] = provider
        return provider

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
            if not isinstance(data, dict):
                raise ValueError(f"expected a JSON object, got {type(data).__name__}")

            if data.get("version") is None and self._migrate_legacy_selection(data):
                self._write_selection(data)

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
        except (json.JSONDecodeError, OSError, KeyError, ValueError) as exc:
            logger.warning("Failed to load persisted model selection: %s", exc)

        return None

    def _legacy_family(self, model_id: str) -> str | None:
        """Family name for a concrete claude-* ID (``claude-opus-4-8`` -> ``opus``)."""
        match = _FAMILY_PREFIX.match(model_id)
        if match and match.group(1) in self._model_families:
            return match.group(1)
        return None

    def _migrate_legacy_selection(self, data: dict[str, Any]) -> bool:
        """Turn a pre-v2 file's resolved claude-* IDs back into family names, in place.

        Pre-v2 code saved the resolved ID rather than what the user typed, so a
        full ID in a legacy file cannot be told from a deliberate pin. Treating
        it as the family is the reading that matches how /model was used.
        Returns True if anything changed and the file should be rewritten.
        """
        changed = False

        model = data.get("model")
        if isinstance(model, str):
            family = self._legacy_family(model)
            if family:
                logger.warning(
                    "Migrated saved default model %s -> '%s' (legacy %s stored a "
                    "resolved ID; the family now tracks the newest model)",
                    model, family, _PERSISTENCE_FILENAME,
                )
                data["model"] = family
                changed = True

        features = data.get("features")
        if isinstance(features, dict):
            for feat, feat_model in list(features.items()):
                if not isinstance(feat_model, str):
                    continue
                family = self._legacy_family(feat_model)
                if family:
                    logger.warning(
                        "Migrated saved %s model %s -> '%s' (legacy %s stored a "
                        "resolved ID; the family now tracks the newest model)",
                        feat, feat_model, family, _PERSISTENCE_FILENAME,
                    )
                    features[feat] = family
                    changed = True

        if changed:
            data["version"] = _PERSISTENCE_VERSION
        return changed

    def _persist_selection(self) -> None:
        """Write the default and per-feature overrides to JSON, as the user chose them."""
        data: dict[str, Any] = {
            "version": _PERSISTENCE_VERSION,
            "provider": self._default_provider_name or "",
            "model": self._default_model_input or "",
        }
        if self._feature_models:
            data["features"] = dict(self._feature_models)
        self._write_selection(data)

    def _write_selection(self, data: dict[str, Any]) -> None:
        """Atomically write *data* to the selection file."""
        path = self._persistence_path()

        # Atomic write, matching version_store/base_mute_manager: a crash
        # mid-write must not leave a half-written selection behind.
        try:
            parent = Path(path).parent
            parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(parent), prefix=".tmp_model_", suffix=".json")
            try:
                os.fchmod(fd, 0o644)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
                os.replace(tmp, path)
            except Exception:
                try:
                    os.unlink(tmp)
                except OSError as unlink_exc:
                    logger.warning("Could not remove temp file %s: %s", tmp, unlink_exc)
                raise
        except OSError as exc:
            logger.error("Failed to persist model selection: %s", exc)

    def _persist_to_config(
        self,
        *,
        default_model: str | None = None,
        feature: str | None = None,
        feature_model: str | None = None,
    ) -> None:
        """Write model changes back to config.yaml so the file stays in sync."""
        if not self._config_path:
            return

        from src.config import load_yaml_config

        path = Path(self._config_path)
        if not path.exists():
            return

        try:
            data = load_yaml_config(str(path))
            ai = data.setdefault("ai", {})

            if default_model is not None:
                ai["default_model"] = default_model
            if feature is not None and feature_model is not None:
                models = ai.setdefault("models", {})
                models[feature] = feature_model

            from src.config import atomic_yaml_write
            atomic_yaml_write(data, path)
        except Exception as exc:
            logger.error("Failed to persist model to config.yaml: %s", exc)
