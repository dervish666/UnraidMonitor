"""Tests for ProviderRegistry — model selection, per-feature overrides, persistence."""

import json
from pathlib import Path
from unittest.mock import MagicMock

from src.services.llm.provider import ModelInfo
from src.services.llm.registry import ProviderInfo, ProviderRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_anthropic_client() -> MagicMock:
    """Return a mock that looks like an AsyncAnthropic client."""
    return MagicMock(name="anthropic_client")


def _make_openai_client() -> MagicMock:
    """Return a mock that looks like an AsyncOpenAI client."""
    return MagicMock(name="openai_client")


def _make_ollama_client() -> MagicMock:
    """Return a mock that looks like an AsyncOpenAI client pointed at Ollama."""
    return MagicMock(name="ollama_client")


def _sample_ollama_models() -> list[ModelInfo]:
    return [
        ModelInfo(id="llama3.1:8b", name="llama3.1:8b", provider="ollama", supports_tools=True),
        ModelInfo(id="mistral:7b", name="mistral:7b", provider="ollama", supports_tools=True),
    ]


# ---------------------------------------------------------------------------
# Construction & defaults
# ---------------------------------------------------------------------------

class TestRegistryConstruction:
    def test_no_providers_configured(self):
        reg = ProviderRegistry()
        assert reg.get_provider() is None
        assert reg.get_current_model() is None

    def test_anthropic_only(self):
        reg = ProviderRegistry(
            anthropic_client=_make_anthropic_client(),
            default_model="claude-sonnet-4-5",
        )
        provider = reg.get_provider()
        assert provider is not None
        assert provider.provider_name == "anthropic"
        assert provider.model_name == "claude-sonnet-4-5"

    def test_openai_only(self):
        reg = ProviderRegistry(
            openai_client=_make_openai_client(),
            default_model="gpt-4o",
        )
        provider = reg.get_provider()
        assert provider is not None
        assert provider.provider_name == "openai"
        assert provider.model_name == "gpt-4o"

    def test_ollama_only(self):
        reg = ProviderRegistry(
            ollama_client=_make_ollama_client(),
            ollama_models=_sample_ollama_models(),
            default_model="llama3.1:8b",
        )
        provider = reg.get_provider()
        assert provider is not None
        assert provider.provider_name == "ollama"
        assert provider.model_name == "llama3.1:8b"

    def test_default_model_auto_selects_anthropic_when_available(self):
        """With no explicit default_model, registry should pick anthropic if available."""
        reg = ProviderRegistry(anthropic_client=_make_anthropic_client())
        provider = reg.get_provider()
        assert provider is not None
        assert provider.provider_name == "anthropic"

    def test_default_model_auto_selects_openai_when_only_openai(self):
        """With no explicit default_model and only openai, registry should pick openai."""
        reg = ProviderRegistry(openai_client=_make_openai_client())
        provider = reg.get_provider()
        assert provider is not None
        assert provider.provider_name == "openai"

    def test_default_model_auto_selects_ollama_when_only_ollama(self):
        """With no explicit default_model and only ollama, registry should pick ollama
        and use the ollama_default_model (qwen2.5:7b by default)."""
        models = _sample_ollama_models()
        reg = ProviderRegistry(ollama_client=_make_ollama_client(), ollama_models=models)
        provider = reg.get_provider()
        assert provider is not None
        assert provider.provider_name == "ollama"
        assert provider.model_name == "qwen2.5:7b"

    def test_ollama_default_model_is_configurable(self):
        """ollama_default_model parameter controls which model is auto-selected for Ollama."""
        models = _sample_ollama_models()
        reg = ProviderRegistry(
            ollama_client=_make_ollama_client(),
            ollama_models=models,
            ollama_default_model="mistral:7b",
        )
        provider = reg.get_provider()
        assert provider is not None
        assert provider.provider_name == "ollama"
        assert provider.model_name == "mistral:7b"

    def test_claude_default_model_falls_back_to_ollama_when_only_ollama(self):
        """When default_model is a Claude model but Anthropic is not configured,
        the registry should fall back to Ollama rather than returning None."""
        models = _sample_ollama_models()
        reg = ProviderRegistry(
            ollama_client=_make_ollama_client(),
            ollama_models=models,
            default_model="claude-haiku-4-5",
        )
        provider = reg.get_provider()
        assert provider is not None
        assert provider.provider_name == "ollama"

    def test_claude_default_model_falls_back_to_openai_when_only_openai(self):
        """When default_model is a Claude model but only OpenAI is configured,
        the registry should fall back to OpenAI."""
        reg = ProviderRegistry(
            openai_client=_make_openai_client(),
            default_model="claude-haiku-4-5",
        )
        provider = reg.get_provider()
        assert provider is not None
        assert provider.provider_name == "openai"

    def test_ollama_no_models_discovered_no_auto_select(self):
        """If ollama_client exists but no models were discovered, ollama is not auto-selected."""
        reg = ProviderRegistry(
            ollama_client=_make_ollama_client(),
            ollama_models=[],
        )
        provider = reg.get_provider()
        assert provider is None


# ---------------------------------------------------------------------------
# get_provider with feature overrides
# ---------------------------------------------------------------------------

class TestFeatureOverrides:
    def test_feature_override_returns_different_model(self):
        reg = ProviderRegistry(
            anthropic_client=_make_anthropic_client(),
            openai_client=_make_openai_client(),
            default_model="claude-sonnet-4-5",
            feature_models={"diagnostic": "gpt-4o"},
        )
        default = reg.get_provider()
        diagnostic = reg.get_provider(feature="diagnostic")

        assert default is not None
        assert default.provider_name == "anthropic"

        assert diagnostic is not None
        assert diagnostic.provider_name == "openai"
        assert diagnostic.model_name == "gpt-4o"

    def test_feature_override_falls_back_to_default(self):
        reg = ProviderRegistry(
            anthropic_client=_make_anthropic_client(),
            default_model="claude-sonnet-4-5",
            feature_models={"diagnostic": "gpt-4o"},  # no openai client
        )
        # "nl_chat" has no override → falls back to default
        nl_provider = reg.get_provider(feature="nl_chat")
        assert nl_provider is not None
        assert nl_provider.provider_name == "anthropic"

    def test_feature_override_with_ollama(self):
        reg = ProviderRegistry(
            anthropic_client=_make_anthropic_client(),
            ollama_client=_make_ollama_client(),
            ollama_models=_sample_ollama_models(),
            default_model="claude-sonnet-4-5",
            feature_models={"diagnostic": "llama3.1:8b"},
        )
        diag = reg.get_provider(feature="diagnostic")
        assert diag is not None
        assert diag.provider_name == "ollama"
        assert diag.model_name == "llama3.1:8b"

    def test_feature_override_model_not_available_falls_back(self):
        """If a feature override references an unavailable model, return None for that model."""
        reg = ProviderRegistry(
            anthropic_client=_make_anthropic_client(),
            default_model="claude-sonnet-4-5",
            feature_models={"diagnostic": "gpt-4o"},  # no openai client
        )
        # diagnostic wants gpt-4o but no openai client → should fall back to default
        diag = reg.get_provider(feature="diagnostic")
        assert diag is not None
        assert diag.provider_name == "anthropic"


# ---------------------------------------------------------------------------
# get_available_providers
# ---------------------------------------------------------------------------

class TestGetAvailableProviders:
    def test_no_providers(self):
        reg = ProviderRegistry()
        assert reg.get_available_providers() == []

    def test_anthropic_only(self):
        reg = ProviderRegistry(anthropic_client=_make_anthropic_client())
        providers = reg.get_available_providers()
        assert len(providers) == 1
        assert providers[0].name == "anthropic"
        assert providers[0].display_name == "Anthropic"
        assert any(m.id == "claude-sonnet-4-5" for m in providers[0].available_models)

    def test_openai_only(self):
        reg = ProviderRegistry(openai_client=_make_openai_client())
        providers = reg.get_available_providers()
        assert len(providers) == 1
        assert providers[0].name == "openai"
        assert providers[0].display_name == "OpenAI"
        assert any(m.id == "gpt-4o" for m in providers[0].available_models)

    def test_ollama_only(self):
        models = _sample_ollama_models()
        reg = ProviderRegistry(ollama_client=_make_ollama_client(), ollama_models=models)
        providers = reg.get_available_providers()
        assert len(providers) == 1
        assert providers[0].name == "ollama"
        assert providers[0].display_name == "Ollama"
        assert len(providers[0].available_models) == 2

    def test_all_providers(self):
        reg = ProviderRegistry(
            anthropic_client=_make_anthropic_client(),
            openai_client=_make_openai_client(),
            ollama_client=_make_ollama_client(),
            ollama_models=_sample_ollama_models(),
        )
        providers = reg.get_available_providers()
        names = {p.name for p in providers}
        assert names == {"anthropic", "openai", "ollama"}


# ---------------------------------------------------------------------------
# set_model & persistence
# ---------------------------------------------------------------------------

class TestSetModel:
    def test_set_model_changes_default(self, tmp_path: Path):
        reg = ProviderRegistry(
            anthropic_client=_make_anthropic_client(),
            openai_client=_make_openai_client(),
            default_model="claude-sonnet-4-5",
            data_dir=str(tmp_path),
        )
        assert reg.get_provider().provider_name == "anthropic"

        reg.set_model("openai", "gpt-4o")
        provider = reg.get_provider()
        assert provider is not None
        assert provider.provider_name == "openai"
        assert provider.model_name == "gpt-4o"

    def test_set_model_persists_to_json(self, tmp_path: Path):
        reg = ProviderRegistry(
            anthropic_client=_make_anthropic_client(),
            openai_client=_make_openai_client(),
            default_model="claude-sonnet-4-5",
            data_dir=str(tmp_path),
        )
        reg.set_model("openai", "gpt-4o")

        json_path = tmp_path / "model_selection.json"
        assert json_path.exists()
        data = json.loads(json_path.read_text())
        assert data == {"provider": "openai", "model": "gpt-4o"}

    def test_set_model_to_ollama(self, tmp_path: Path):
        reg = ProviderRegistry(
            anthropic_client=_make_anthropic_client(),
            ollama_client=_make_ollama_client(),
            ollama_models=_sample_ollama_models(),
            default_model="claude-sonnet-4-5",
            data_dir=str(tmp_path),
        )
        reg.set_model("ollama", "llama3.1:8b")
        provider = reg.get_provider()
        assert provider.provider_name == "ollama"
        assert provider.model_name == "llama3.1:8b"


# ---------------------------------------------------------------------------
# get_current_model
# ---------------------------------------------------------------------------

class TestGetCurrentModel:
    def test_returns_none_when_no_providers(self):
        reg = ProviderRegistry()
        assert reg.get_current_model() is None

    def test_returns_tuple(self):
        reg = ProviderRegistry(
            anthropic_client=_make_anthropic_client(),
            default_model="claude-sonnet-4-5",
        )
        result = reg.get_current_model()
        assert result == ("anthropic", "claude-sonnet-4-5")

    def test_reflects_set_model(self, tmp_path: Path):
        reg = ProviderRegistry(
            anthropic_client=_make_anthropic_client(),
            openai_client=_make_openai_client(),
            default_model="claude-sonnet-4-5",
            data_dir=str(tmp_path),
        )
        reg.set_model("openai", "gpt-4o-mini")
        assert reg.get_current_model() == ("openai", "gpt-4o-mini")


# ---------------------------------------------------------------------------
# Provider auto-detection from model name
# ---------------------------------------------------------------------------

class TestAutoDetection:
    def test_claude_model_detected_as_anthropic(self):
        reg = ProviderRegistry(
            anthropic_client=_make_anthropic_client(),
            openai_client=_make_openai_client(),
            default_model="claude-sonnet-4-5",
        )
        assert reg.get_provider().provider_name == "anthropic"

    def test_gpt_model_detected_as_openai(self):
        reg = ProviderRegistry(
            anthropic_client=_make_anthropic_client(),
            openai_client=_make_openai_client(),
            default_model="gpt-4o",
        )
        assert reg.get_provider().provider_name == "openai"

    def test_o1_model_detected_as_openai(self):
        reg = ProviderRegistry(
            openai_client=_make_openai_client(),
            default_model="o1-preview",
        )
        assert reg.get_provider().provider_name == "openai"

    def test_o3_model_detected_as_openai(self):
        reg = ProviderRegistry(
            openai_client=_make_openai_client(),
            default_model="o3-mini",
        )
        assert reg.get_provider().provider_name == "openai"

    def test_o4_model_detected_as_openai(self):
        reg = ProviderRegistry(
            openai_client=_make_openai_client(),
            default_model="o4-mini",
        )
        assert reg.get_provider().provider_name == "openai"

    def test_known_ollama_model_detected(self):
        models = _sample_ollama_models()
        reg = ProviderRegistry(
            ollama_client=_make_ollama_client(),
            ollama_models=models,
            default_model="llama3.1:8b",
        )
        assert reg.get_provider().provider_name == "ollama"

    def test_unknown_model_falls_back_to_ollama_if_available(self):
        """Unknown model with ollama available should route to ollama."""
        reg = ProviderRegistry(
            anthropic_client=_make_anthropic_client(),
            ollama_client=_make_ollama_client(),
            ollama_models=_sample_ollama_models(),
            default_model="some-custom-model",
        )
        assert reg.get_provider().provider_name == "ollama"

    def test_unknown_model_falls_back_to_anthropic_without_ollama(self):
        """Unknown model without ollama should fall back to anthropic."""
        reg = ProviderRegistry(
            anthropic_client=_make_anthropic_client(),
            default_model="some-custom-model",
        )
        assert reg.get_provider().provider_name == "anthropic"


# ---------------------------------------------------------------------------
# Persistence — loading saved selection on startup
# ---------------------------------------------------------------------------

class TestPersistence:
    def test_loads_persisted_selection(self, tmp_path: Path):
        # Write persisted selection
        selection_file = tmp_path / "model_selection.json"
        selection_file.write_text(json.dumps({"provider": "openai", "model": "gpt-4o"}))

        reg = ProviderRegistry(
            anthropic_client=_make_anthropic_client(),
            openai_client=_make_openai_client(),
            default_model="claude-sonnet-4-5",  # would be the default
            data_dir=str(tmp_path),
        )
        # Persisted selection should override the constructor default
        provider = reg.get_provider()
        assert provider is not None
        assert provider.provider_name == "openai"
        assert provider.model_name == "gpt-4o"

    def test_ignores_invalid_persisted_file(self, tmp_path: Path):
        selection_file = tmp_path / "model_selection.json"
        selection_file.write_text("not valid json")

        reg = ProviderRegistry(
            anthropic_client=_make_anthropic_client(),
            default_model="claude-sonnet-4-5",
            data_dir=str(tmp_path),
        )
        # Should fall back to constructor default
        assert reg.get_provider().provider_name == "anthropic"

    def test_ignores_persisted_selection_for_unavailable_provider(self, tmp_path: Path):
        """If persisted provider is not configured, fall back to constructor default."""
        selection_file = tmp_path / "model_selection.json"
        selection_file.write_text(json.dumps({"provider": "openai", "model": "gpt-4o"}))

        reg = ProviderRegistry(
            anthropic_client=_make_anthropic_client(),
            default_model="claude-sonnet-4-5",
            data_dir=str(tmp_path),
            # No openai_client!
        )
        # Should fall back to default since openai not available
        provider = reg.get_provider()
        assert provider is not None
        assert provider.provider_name == "anthropic"

    def test_missing_data_dir_creates_it(self, tmp_path: Path):
        data_dir = tmp_path / "subdir" / "data"
        reg = ProviderRegistry(
            anthropic_client=_make_anthropic_client(),
            default_model="claude-sonnet-4-5",
            data_dir=str(data_dir),
        )
        reg.set_model("anthropic", "claude-haiku-4-5")
        assert (data_dir / "model_selection.json").exists()


# ---------------------------------------------------------------------------
# Runtime switching between providers
# ---------------------------------------------------------------------------

class TestRuntimeSwitching:
    def test_switch_anthropic_to_openai(self, tmp_path: Path):
        reg = ProviderRegistry(
            anthropic_client=_make_anthropic_client(),
            openai_client=_make_openai_client(),
            default_model="claude-sonnet-4-5",
            data_dir=str(tmp_path),
        )
        assert reg.get_provider().provider_name == "anthropic"

        reg.set_model("openai", "gpt-4o")
        assert reg.get_provider().provider_name == "openai"
        assert reg.get_current_model() == ("openai", "gpt-4o")

    def test_switch_openai_to_ollama(self, tmp_path: Path):
        reg = ProviderRegistry(
            openai_client=_make_openai_client(),
            ollama_client=_make_ollama_client(),
            ollama_models=_sample_ollama_models(),
            default_model="gpt-4o",
            data_dir=str(tmp_path),
        )
        assert reg.get_provider().provider_name == "openai"

        reg.set_model("ollama", "mistral:7b")
        assert reg.get_provider().provider_name == "ollama"
        assert reg.get_provider().model_name == "mistral:7b"

    def test_switch_preserves_feature_overrides(self, tmp_path: Path):
        reg = ProviderRegistry(
            anthropic_client=_make_anthropic_client(),
            openai_client=_make_openai_client(),
            default_model="claude-sonnet-4-5",
            feature_models={"diagnostic": "gpt-4o"},
            data_dir=str(tmp_path),
        )
        # Switch default to openai
        reg.set_model("openai", "gpt-4o-mini")

        # Default should now be openai gpt-4o-mini
        assert reg.get_provider().model_name == "gpt-4o-mini"
        # Feature override should still be gpt-4o
        assert reg.get_provider(feature="diagnostic").model_name == "gpt-4o"


# ---------------------------------------------------------------------------
# Well-known models
# ---------------------------------------------------------------------------

class TestWellKnownModels:
    def test_anthropic_well_known_models(self):
        reg = ProviderRegistry(anthropic_client=_make_anthropic_client())
        providers = reg.get_available_providers()
        anthropic_info = next(p for p in providers if p.name == "anthropic")
        model_ids = {m.id for m in anthropic_info.available_models}
        assert "claude-sonnet-4-5" in model_ids
        assert "claude-haiku-4-5" in model_ids

    def test_openai_well_known_models(self):
        reg = ProviderRegistry(openai_client=_make_openai_client())
        providers = reg.get_available_providers()
        openai_info = next(p for p in providers if p.name == "openai")
        model_ids = {m.id for m in openai_info.available_models}
        assert "gpt-4o" in model_ids
        assert "gpt-4o-mini" in model_ids
        assert "gpt-4.1" in model_ids
        assert "gpt-4.1-mini" in model_ids
        assert "gpt-4.1-nano" in model_ids

    def test_ollama_models_are_those_discovered(self):
        models = _sample_ollama_models()
        reg = ProviderRegistry(ollama_client=_make_ollama_client(), ollama_models=models)
        providers = reg.get_available_providers()
        ollama_info = next(p for p in providers if p.name == "ollama")
        assert len(ollama_info.available_models) == 2
        model_ids = {m.id for m in ollama_info.available_models}
        assert "llama3.1:8b" in model_ids
        assert "mistral:7b" in model_ids
