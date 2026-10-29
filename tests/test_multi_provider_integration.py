# tests/test_multi_provider_integration.py
"""Integration tests for the multi-provider LLM stack.

These tests verify end-to-end flows through ProviderRegistry -> Provider -> mock client,
ensuring the full stack works together correctly.
"""
import json

from unittest.mock import AsyncMock, MagicMock

from src.services.llm.provider import LLMProvider, LLMResponse, ModelInfo
from src.services.llm.anthropic_provider import AnthropicProvider
from src.services.llm.openai_provider import OpenAIProvider
from src.services.llm.ollama_provider import OllamaProvider
from src.services.llm.registry import ProviderRegistry


# ---------------------------------------------------------------------------
# Anthropic mock helpers
# ---------------------------------------------------------------------------

def _anthropic_text_block(text: str) -> MagicMock:
    block = MagicMock()
    block.type = "text"
    block.text = text
    return block


def _anthropic_tool_block(tool_id: str, name: str, input_data: dict) -> MagicMock:
    block = MagicMock()
    block.type = "tool_use"
    block.id = tool_id
    block.name = name
    block.input = input_data
    return block


def _anthropic_response(content_blocks: list, stop_reason: str = "end_turn") -> MagicMock:
    resp = MagicMock()
    resp.content = content_blocks
    resp.stop_reason = stop_reason
    return resp


def _anthropic_client(response: MagicMock | None = None) -> MagicMock:
    if response is None:
        response = _anthropic_response([_anthropic_text_block("Hello from Claude")])
    client = MagicMock()
    client.messages = MagicMock()
    client.messages.create = AsyncMock(return_value=response)
    return client


# ---------------------------------------------------------------------------
# OpenAI mock helpers
# ---------------------------------------------------------------------------

def _openai_response(
    content: str = "Hello from GPT",
    tool_calls: list | None = None,
    finish_reason: str = "stop",
) -> MagicMock:
    message = MagicMock()
    message.content = content
    message.tool_calls = tool_calls

    choice = MagicMock()
    choice.message = message
    choice.finish_reason = finish_reason

    resp = MagicMock()
    resp.choices = [choice]
    return resp


def _openai_tool_call(tc_id: str, name: str, arguments: dict) -> MagicMock:
    tc = MagicMock()
    tc.id = tc_id
    tc.function = MagicMock()
    tc.function.name = name
    tc.function.arguments = json.dumps(arguments)
    return tc


def _openai_client(response: MagicMock | None = None) -> MagicMock:
    if response is None:
        response = _openai_response()
    client = MagicMock()
    client.chat = MagicMock()
    client.chat.completions = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=response)
    return client


# ===========================================================================
# Test 1: ProviderRegistry -> AnthropicProvider flow
# ===========================================================================


class TestRegistryAnthropicFlow:
    """End-to-end: create registry with Anthropic client, get provider, call chat."""

    async def test_registry_returns_anthropic_provider(self, tmp_path):
        client = _anthropic_client()
        registry = ProviderRegistry(
            anthropic_client=client,
            data_dir=str(tmp_path),
        )

        provider = registry.get_provider()

        assert provider is not None
        assert isinstance(provider, AnthropicProvider)
        assert isinstance(provider, LLMProvider)
        assert provider.provider_name == "anthropic"

    async def test_registry_anthropic_chat_returns_response(self, tmp_path):
        response = _anthropic_response(
            [_anthropic_text_block("Container plex is running fine.")],
            "end_turn",
        )
        client = _anthropic_client(response)
        registry = ProviderRegistry(
            anthropic_client=client,
            data_dir=str(tmp_path),
        )

        provider = registry.get_provider()
        result = await provider.chat(
            messages=[{"role": "user", "content": "How is plex?"}],
            system="You are a server monitor.",
            max_tokens=256,
        )

        assert isinstance(result, LLMResponse)
        assert result.text == "Container plex is running fine."
        assert result.stop_reason == "end"
        assert result.tool_calls is None

        # Verify the mock client was actually called
        client.messages.create.assert_awaited_once()

    async def test_registry_anthropic_chat_with_tools(self, tmp_path):
        response = _anthropic_response(
            [
                _anthropic_text_block("Let me check that."),
                _anthropic_tool_block("tc_1", "get_status", {"name": "plex"}),
            ],
            "tool_use",
        )
        client = _anthropic_client(response)
        registry = ProviderRegistry(
            anthropic_client=client,
            data_dir=str(tmp_path),
        )

        provider = registry.get_provider()
        result = await provider.chat(
            messages=[{"role": "user", "content": "How is plex?"}],
            tools=[{"name": "get_status", "description": "Get status", "input_schema": {}}],
        )

        assert result.stop_reason == "tool_use"
        assert result.tool_calls is not None
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "get_status"
        assert result.tool_calls[0].input == {"name": "plex"}


# ===========================================================================
# Test 2: ProviderRegistry -> OpenAIProvider flow
# ===========================================================================


class TestRegistryOpenAIFlow:
    """End-to-end: create registry with OpenAI client, get provider, call chat."""

    async def test_registry_returns_openai_provider(self, tmp_path):
        client = _openai_client()
        registry = ProviderRegistry(
            openai_client=client,
            default_model="gpt-4o",
            data_dir=str(tmp_path),
        )

        provider = registry.get_provider()

        assert provider is not None
        assert isinstance(provider, OpenAIProvider)
        assert isinstance(provider, LLMProvider)
        assert provider.provider_name == "openai"

    async def test_registry_openai_chat_returns_response(self, tmp_path):
        response = _openai_response(content="All containers healthy.", finish_reason="stop")
        client = _openai_client(response)
        registry = ProviderRegistry(
            openai_client=client,
            default_model="gpt-4o",
            data_dir=str(tmp_path),
        )

        provider = registry.get_provider()
        result = await provider.chat(
            messages=[{"role": "user", "content": "Status report?"}],
            system="You are a server monitor.",
            max_tokens=512,
        )

        assert isinstance(result, LLMResponse)
        assert result.text == "All containers healthy."
        assert result.stop_reason == "end"
        assert result.tool_calls is None

        client.chat.completions.create.assert_awaited_once()

    async def test_registry_openai_chat_with_tools(self, tmp_path):
        tc = _openai_tool_call("tc_1", "get_status", {"name": "radarr"})
        response = _openai_response(
            content="",
            tool_calls=[tc],
            finish_reason="tool_calls",
        )
        client = _openai_client(response)
        registry = ProviderRegistry(
            openai_client=client,
            default_model="gpt-4o",
            data_dir=str(tmp_path),
        )

        provider = registry.get_provider()
        result = await provider.chat(
            messages=[{"role": "user", "content": "Check radarr"}],
            tools=[{"name": "get_status", "description": "Get status", "input_schema": {}}],
        )

        assert result.stop_reason == "tool_use"
        assert result.tool_calls is not None
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "get_status"
        assert result.tool_calls[0].input == {"name": "radarr"}


# ===========================================================================
# Test 3: ProviderRegistry -> OllamaProvider flow
# ===========================================================================


class TestRegistryOllamaFlow:
    """End-to-end: create registry with Ollama client and model list, get provider, call chat."""

    async def test_registry_returns_ollama_provider(self, tmp_path):
        client = _openai_client()  # Ollama uses OpenAI-compatible client
        ollama_models = [
            ModelInfo(id="llama3.1:8b", name="llama3.1:8b", provider="ollama", supports_tools=True),
            ModelInfo(
                id="codellama:7b", name="codellama:7b", provider="ollama", supports_tools=False
            ),
        ]
        registry = ProviderRegistry(
            ollama_client=client,
            ollama_models=ollama_models,
            default_model="llama3.1:8b",
            data_dir=str(tmp_path),
        )

        provider = registry.get_provider()

        assert provider is not None
        assert isinstance(provider, OllamaProvider)
        assert isinstance(provider, LLMProvider)
        assert provider.provider_name == "ollama"
        assert provider.model_name == "llama3.1:8b"
        assert provider.supports_tools is True

    async def test_registry_ollama_no_tool_support(self, tmp_path):
        client = _openai_client()
        ollama_models = [
            ModelInfo(
                id="codellama:7b", name="codellama:7b", provider="ollama", supports_tools=False
            ),
        ]
        registry = ProviderRegistry(
            ollama_client=client,
            ollama_models=ollama_models,
            default_model="codellama:7b",
            data_dir=str(tmp_path),
        )

        provider = registry.get_provider()

        assert provider is not None
        assert provider.supports_tools is False

    async def test_registry_ollama_chat_returns_response(self, tmp_path):
        response = _openai_response(content="Analysis complete.", finish_reason="stop")
        client = _openai_client(response)
        ollama_models = [
            ModelInfo(id="llama3.1:8b", name="llama3.1:8b", provider="ollama", supports_tools=True),
        ]
        registry = ProviderRegistry(
            ollama_client=client,
            ollama_models=ollama_models,
            default_model="llama3.1:8b",
            data_dir=str(tmp_path),
        )

        provider = registry.get_provider()
        result = await provider.chat(
            messages=[{"role": "user", "content": "Analyze logs"}],
            system="You are a log analyzer.",
        )

        assert isinstance(result, LLMResponse)
        assert result.text == "Analysis complete."
        assert result.stop_reason == "end"

        client.chat.completions.create.assert_awaited_once()


# ===========================================================================
# Test 4: Per-feature model override
# ===========================================================================


class TestPerFeatureModelOverride:
    """Verify that feature_models routes specific features to different providers."""

    async def test_feature_override_returns_different_provider(self, tmp_path):
        anthropic_client = _anthropic_client()
        openai_client = _openai_client()

        registry = ProviderRegistry(
            anthropic_client=anthropic_client,
            openai_client=openai_client,
            feature_models={"diagnostic": "gpt-4o"},
            data_dir=str(tmp_path),
        )

        # Default should be Anthropic (auto-selected when anthropic client is present)
        default_provider = registry.get_provider()
        assert default_provider is not None
        assert default_provider.provider_name == "anthropic"

        # Diagnostic feature should use OpenAI
        diag_provider = registry.get_provider("diagnostic")
        assert diag_provider is not None
        assert diag_provider.provider_name == "openai"
        assert diag_provider.model_name == "gpt-4o"

    async def test_feature_override_chat_uses_correct_client(self, tmp_path):
        anthropic_resp = _anthropic_response(
            [_anthropic_text_block("Claude response")], "end_turn"
        )
        openai_resp = _openai_response(content="GPT response", finish_reason="stop")

        anthropic_client = _anthropic_client(anthropic_resp)
        openai_client = _openai_client(openai_resp)

        registry = ProviderRegistry(
            anthropic_client=anthropic_client,
            openai_client=openai_client,
            feature_models={"diagnostic": "gpt-4o"},
            data_dir=str(tmp_path),
        )

        # Call default (Anthropic)
        default_provider = registry.get_provider()
        default_result = await default_provider.chat(
            messages=[{"role": "user", "content": "test"}]
        )
        assert default_result.text == "Claude response"
        anthropic_client.messages.create.assert_awaited_once()
        openai_client.chat.completions.create.assert_not_awaited()

        # Call diagnostic (OpenAI)
        diag_provider = registry.get_provider("diagnostic")
        diag_result = await diag_provider.chat(
            messages=[{"role": "user", "content": "diagnose"}]
        )
        assert diag_result.text == "GPT response"
        openai_client.chat.completions.create.assert_awaited_once()

    async def test_unknown_feature_falls_back_to_default(self, tmp_path):
        anthropic_client = _anthropic_client()
        openai_client = _openai_client()

        registry = ProviderRegistry(
            anthropic_client=anthropic_client,
            openai_client=openai_client,
            feature_models={"diagnostic": "gpt-4o"},
            data_dir=str(tmp_path),
        )

        # A feature not in the map should fall back to the global default
        provider = registry.get_provider("pattern_analysis")
        assert provider is not None
        assert provider.provider_name == "anthropic"

    async def test_multiple_feature_overrides(self, tmp_path):
        anthropic_client = _anthropic_client()
        openai_client = _openai_client()
        ollama_models = [
            ModelInfo(
                id="llama3.1:8b", name="llama3.1:8b", provider="ollama", supports_tools=True
            ),
        ]
        ollama_client = _openai_client()

        registry = ProviderRegistry(
            anthropic_client=anthropic_client,
            openai_client=openai_client,
            ollama_client=ollama_client,
            ollama_models=ollama_models,
            feature_models={
                "diagnostic": "gpt-4o",
                "pattern_analysis": "llama3.1:8b",
            },
            data_dir=str(tmp_path),
        )

        default = registry.get_provider()
        diag = registry.get_provider("diagnostic")
        pattern = registry.get_provider("pattern_analysis")

        assert default.provider_name == "anthropic"
        assert diag.provider_name == "openai"
        assert diag.model_name == "gpt-4o"
        assert pattern.provider_name == "ollama"
        assert pattern.model_name == "llama3.1:8b"


# ===========================================================================
# Test 5: Model switching via set_model
# ===========================================================================


class TestModelSwitching:
    """Verify set_model changes the current model and persists the selection."""

    async def test_set_model_changes_current_model(self, tmp_path):
        anthropic_client = _anthropic_client()
        openai_client = _openai_client()

        registry = ProviderRegistry(
            anthropic_client=anthropic_client,
            openai_client=openai_client,
            data_dir=str(tmp_path),
        )

        # Initially Anthropic
        current = registry.get_current_model()
        assert current is not None
        assert current[0] == "anthropic"

        # Switch to OpenAI
        registry.set_model("openai", "gpt-4o")

        current = registry.get_current_model()
        assert current == ("openai", "gpt-4o")

    async def test_set_model_changes_provider_returned(self, tmp_path):
        anthropic_client = _anthropic_client()
        openai_client = _openai_client()

        registry = ProviderRegistry(
            anthropic_client=anthropic_client,
            openai_client=openai_client,
            data_dir=str(tmp_path),
        )

        # Initially returns Anthropic
        provider = registry.get_provider()
        assert provider.provider_name == "anthropic"

        # Switch to OpenAI
        registry.set_model("openai", "gpt-4o")

        provider = registry.get_provider()
        assert provider.provider_name == "openai"
        assert provider.model_name == "gpt-4o"

    async def test_set_model_persists_to_disk(self, tmp_path):
        client = _anthropic_client()
        registry = ProviderRegistry(
            anthropic_client=client,
            data_dir=str(tmp_path),
        )

        registry.set_model("anthropic", "claude-haiku-4-5")

        # Read the persisted JSON file
        persistence_file = tmp_path / "model_selection.json"
        assert persistence_file.exists()

        data = json.loads(persistence_file.read_text())
        assert data["provider"] == "anthropic"
        assert data["model"] == "claude-haiku-4-5"

    async def test_set_model_survives_registry_recreation(self, tmp_path):
        anthropic_client = _anthropic_client()
        openai_client = _openai_client()

        # First registry: set model to OpenAI
        registry1 = ProviderRegistry(
            anthropic_client=anthropic_client,
            openai_client=openai_client,
            data_dir=str(tmp_path),
        )
        registry1.set_model("openai", "gpt-4o-mini")

        # Second registry: should load persisted selection
        registry2 = ProviderRegistry(
            anthropic_client=anthropic_client,
            openai_client=openai_client,
            data_dir=str(tmp_path),
        )

        current = registry2.get_current_model()
        assert current == ("openai", "gpt-4o-mini")

        provider = registry2.get_provider()
        assert provider.provider_name == "openai"
        assert provider.model_name == "gpt-4o-mini"


# ===========================================================================
# Test 6: Graceful degradation with no clients
# ===========================================================================


class TestGracefulDegradation:
    """Verify the registry handles missing clients gracefully."""

    async def test_no_clients_returns_none(self, tmp_path):
        registry = ProviderRegistry(data_dir=str(tmp_path))

        provider = registry.get_provider()
        assert provider is None

    async def test_no_clients_get_current_model_returns_none(self, tmp_path):
        registry = ProviderRegistry(data_dir=str(tmp_path))

        current = registry.get_current_model()
        assert current is None

    async def test_no_clients_available_providers_empty(self, tmp_path):
        registry = ProviderRegistry(data_dir=str(tmp_path))

        providers = registry.get_available_providers()
        assert providers == []

    async def test_feature_override_without_client_falls_back(self, tmp_path):
        """Feature override referencing unavailable provider falls back to default."""
        anthropic_client = _anthropic_client()
        registry = ProviderRegistry(
            anthropic_client=anthropic_client,
            feature_models={"diagnostic": "gpt-4o"},  # No OpenAI client
            data_dir=str(tmp_path),
        )

        # gpt-4o can't be fulfilled (no OpenAI client), should fall back to Anthropic
        provider = registry.get_provider("diagnostic")
        assert provider is not None
        assert provider.provider_name == "anthropic"

    async def test_persisted_selection_for_missing_provider_ignored(self, tmp_path):
        """If persisted selection references a provider not available, fall back."""
        # Write a persisted selection for OpenAI
        persistence_file = tmp_path / "model_selection.json"
        persistence_file.write_text(json.dumps({"provider": "openai", "model": "gpt-4o"}))

        # Create registry with only Anthropic
        anthropic_client = _anthropic_client()
        registry = ProviderRegistry(
            anthropic_client=anthropic_client,
            data_dir=str(tmp_path),
        )

        # Should ignore persisted OpenAI and fall back to Anthropic
        current = registry.get_current_model()
        assert current is not None
        assert current[0] == "anthropic"


# ===========================================================================
# Test 7: Ollama discover_models
# ===========================================================================


class TestOllamaDiscoverModels:
    """Test OllamaProvider.discover_models with mocked aiohttp responses."""

    async def test_discover_models_returns_model_list(self):
        mock_response_data = {
            "models": [
                {
                    "name": "llama3.1:8b",
                    "model": "llama3.1:8b",
                    "details": {"family": "llama"},
                },
                {
                    "name": "mistral:7b",
                    "model": "mistral:7b",
                    "details": {"family": "mistral"},
                },
                {
                    "name": "phi3:latest",
                    "model": "phi3:latest",
                    "details": {"family": "phi"},
                },
            ]
        }

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value=mock_response_data)
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.get = MagicMock(return_value=mock_resp)

        models = await OllamaProvider.discover_models("http://localhost:11434", session)

        assert len(models) == 3

        # llama family -> supports tools
        assert models[0].id == "llama3.1:8b"
        assert models[0].provider == "ollama"
        assert models[0].supports_tools is True

        # mistral family -> supports tools
        assert models[1].id == "mistral:7b"
        assert models[1].supports_tools is True

        # phi family -> no tool support
        assert models[2].id == "phi3:latest"
        assert models[2].supports_tools is False

    async def test_discover_models_tool_capable_families(self):
        """Verify all known tool-capable families are detected."""
        families = ["llama", "mistral", "qwen", "command-r", "gemma"]
        entries = [
            {"name": f"{fam}:latest", "details": {"family": fam}}
            for fam in families
        ]
        mock_response_data = {"models": entries}

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value=mock_response_data)
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.get = MagicMock(return_value=mock_resp)

        models = await OllamaProvider.discover_models("http://localhost:11434", session)

        assert len(models) == len(families)
        for model in models:
            assert model.supports_tools is True, f"{model.id} should support tools"

    async def test_discover_models_non_200_returns_empty(self):
        mock_resp = AsyncMock()
        mock_resp.status = 500
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.get = MagicMock(return_value=mock_resp)

        models = await OllamaProvider.discover_models("http://localhost:11434", session)
        assert models == []

    async def test_discover_models_connection_error_returns_empty(self):
        import aiohttp

        session = MagicMock()
        session.get = MagicMock(
            side_effect=aiohttp.ClientError("Connection refused")
        )

        models = await OllamaProvider.discover_models("http://localhost:11434", session)
        assert models == []

    async def test_discover_models_empty_response(self):
        mock_response_data = {"models": []}

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value=mock_response_data)
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.get = MagicMock(return_value=mock_resp)

        models = await OllamaProvider.discover_models("http://localhost:11434", session)
        assert models == []

    async def test_discover_models_trailing_slash_stripped(self):
        mock_response_data = {"models": []}

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value=mock_response_data)
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.get = MagicMock(return_value=mock_resp)

        await OllamaProvider.discover_models("http://localhost:11434/", session)

        # Should call with cleaned URL (no double slash)
        session.get.assert_called_once()
        assert session.get.call_args.args == ("http://localhost:11434/api/tags",)
