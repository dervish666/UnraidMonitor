# tests/test_ollama_provider.py
"""Tests for Ollama LLM provider with model discovery."""
import json

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.services.llm.ollama_provider import OllamaProvider
from src.services.llm.provider import LLMProvider, LLMResponse, ModelInfo


# -- Helpers to build mock OpenAI responses (same pattern as test_openai_provider) --


def _make_message(
    content: str | None = "Hello",
    role: str = "assistant",
    tool_calls: list | None = None,
) -> MagicMock:
    msg = MagicMock()
    msg.content = content
    msg.role = role
    msg.tool_calls = tool_calls
    return msg


def _make_choice(
    message: MagicMock | None = None,
    finish_reason: str = "stop",
) -> MagicMock:
    choice = MagicMock()
    choice.message = message or _make_message()
    choice.finish_reason = finish_reason
    return choice


def _make_response(choices: list | None = None) -> MagicMock:
    resp = MagicMock()
    resp.choices = choices or [_make_choice()]
    return resp


def _make_client(response: MagicMock) -> MagicMock:
    client = MagicMock()
    client.chat = MagicMock()
    client.chat.completions = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=response)
    return client


# -- Properties --


class TestOllamaProviderProperties:
    """Test provider_name, model_name, and supports_tools properties."""

    def test_provider_name(self):
        provider = OllamaProvider(client=MagicMock(), model="llama3.1")
        assert provider.provider_name == "ollama"

    def test_model_name(self):
        provider = OllamaProvider(client=MagicMock(), model="llama3.1:8b")
        assert provider.model_name == "llama3.1:8b"

    def test_supports_tools_default_false(self):
        """Ollama defaults to supports_tools=False (many models lack tool support)."""
        provider = OllamaProvider(client=MagicMock(), model="llama3.1")
        assert provider.supports_tools is False

    def test_supports_tools_explicit_true(self):
        provider = OllamaProvider(
            client=MagicMock(), model="llama3.1", supports_tools=True
        )
        assert provider.supports_tools is True

    def test_satisfies_protocol(self):
        provider = OllamaProvider(client=MagicMock(), model="llama3.1")
        assert isinstance(provider, LLMProvider)


# -- Chat inherits from OpenAIProvider --


class TestOllamaProviderChat:
    """Test that chat works (inherited from OpenAIProvider)."""

    async def test_simple_chat(self):
        msg = _make_message(content="I am Ollama!")
        response = _make_response([_make_choice(message=msg, finish_reason="stop")])
        client = _make_client(response)
        provider = OllamaProvider(client=client, model="llama3.1")

        result = await provider.chat(
            messages=[{"role": "user", "content": "Hi"}],
        )

        assert isinstance(result, LLMResponse)
        assert result.text == "I am Ollama!"
        assert result.stop_reason == "end"
        assert result.tool_calls is None

    async def test_chat_passes_model_to_client(self):
        response = _make_response()
        client = _make_client(response)
        provider = OllamaProvider(client=client, model="mistral:7b")

        await provider.chat(messages=[{"role": "user", "content": "Hi"}])

        call_kwargs = client.chat.completions.create.call_args[1]
        assert call_kwargs["model"] == "mistral:7b"


# -- Model Discovery --


class TestOllamaDiscoverModels:
    """Test discover_models static method."""

    async def test_discover_models_parses_response(self):
        """discover_models should parse Ollama /api/tags response into ModelInfo list."""
        ollama_response = {
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
                    "name": "codellama:13b",
                    "model": "codellama:13b",
                    "details": {"family": "codellama"},
                },
            ]
        }

        session = MagicMock()
        resp_mock = AsyncMock()
        resp_mock.status = 200
        resp_mock.json = AsyncMock(return_value=ollama_response)
        # aiohttp session.get returns an async context manager
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=resp_mock)
        ctx.__aexit__ = AsyncMock(return_value=False)
        session.get = MagicMock(return_value=ctx)

        models = await OllamaProvider.discover_models(
            host="http://localhost:11434", session=session
        )

        assert len(models) == 3

        # llama is a tool-capable family
        assert models[0].id == "llama3.1:8b"
        assert models[0].name == "llama3.1:8b"
        assert models[0].provider == "ollama"
        assert models[0].supports_tools is True

        # mistral is a tool-capable family
        assert models[1].id == "mistral:7b"
        assert models[1].supports_tools is True

        # codellama is NOT in the tool-capable families list
        assert models[2].id == "codellama:13b"
        assert models[2].supports_tools is False

    async def test_discover_models_tool_capable_families(self):
        """All known tool-capable families should be detected."""
        tool_families = ["llama", "mistral", "qwen", "command-r", "gemma"]

        for family in tool_families:
            ollama_response = {
                "models": [
                    {
                        "name": f"{family}-model:latest",
                        "model": f"{family}-model:latest",
                        "details": {"family": family},
                    }
                ]
            }

            session = MagicMock()
            resp_mock = AsyncMock()
            resp_mock.status = 200
            resp_mock.json = AsyncMock(return_value=ollama_response)
            ctx = AsyncMock()
            ctx.__aenter__ = AsyncMock(return_value=resp_mock)
            ctx.__aexit__ = AsyncMock(return_value=False)
            session.get = MagicMock(return_value=ctx)

            models = await OllamaProvider.discover_models(
                host="http://localhost:11434", session=session
            )

            assert len(models) == 1
            assert models[0].supports_tools is True, (
                f"Family '{family}' should support tools"
            )

    async def test_discover_models_unknown_family_no_tools(self):
        """Unknown model families should default to no tool support."""
        ollama_response = {
            "models": [
                {
                    "name": "phi3:mini",
                    "model": "phi3:mini",
                    "details": {"family": "phi3"},
                }
            ]
        }

        session = MagicMock()
        resp_mock = AsyncMock()
        resp_mock.status = 200
        resp_mock.json = AsyncMock(return_value=ollama_response)
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=resp_mock)
        ctx.__aexit__ = AsyncMock(return_value=False)
        session.get = MagicMock(return_value=ctx)

        models = await OllamaProvider.discover_models(
            host="http://localhost:11434", session=session
        )

        assert len(models) == 1
        assert models[0].supports_tools is False

    async def test_discover_models_connection_error_returns_empty(self):
        """Connection errors should return empty list, not raise."""
        import aiohttp

        session = MagicMock()
        session.get = MagicMock(side_effect=aiohttp.ClientError("Connection refused"))

        models = await OllamaProvider.discover_models(
            host="http://localhost:11434", session=session
        )

        assert models == []

    async def test_discover_models_timeout_returns_empty(self):
        """Timeouts should return empty list, not raise."""
        import asyncio

        session = MagicMock()
        session.get = MagicMock(side_effect=asyncio.TimeoutError())

        models = await OllamaProvider.discover_models(
            host="http://localhost:11434", session=session
        )

        assert models == []

    async def test_discover_models_non_200_returns_empty(self):
        """Non-200 responses should return empty list."""
        session = MagicMock()
        resp_mock = AsyncMock()
        resp_mock.status = 500
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=resp_mock)
        ctx.__aexit__ = AsyncMock(return_value=False)
        session.get = MagicMock(return_value=ctx)

        models = await OllamaProvider.discover_models(
            host="http://localhost:11434", session=session
        )

        assert models == []

    async def test_discover_models_empty_model_list(self):
        """Empty models list should return empty list."""
        ollama_response = {"models": []}

        session = MagicMock()
        resp_mock = AsyncMock()
        resp_mock.status = 200
        resp_mock.json = AsyncMock(return_value=ollama_response)
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=resp_mock)
        ctx.__aexit__ = AsyncMock(return_value=False)
        session.get = MagicMock(return_value=ctx)

        models = await OllamaProvider.discover_models(
            host="http://localhost:11434", session=session
        )

        assert models == []

    async def test_discover_models_missing_details_defaults_no_tools(self):
        """Models without details/family should default to no tool support."""
        ollama_response = {
            "models": [
                {
                    "name": "custom-model:latest",
                    "model": "custom-model:latest",
                    # no "details" key
                }
            ]
        }

        session = MagicMock()
        resp_mock = AsyncMock()
        resp_mock.status = 200
        resp_mock.json = AsyncMock(return_value=ollama_response)
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=resp_mock)
        ctx.__aexit__ = AsyncMock(return_value=False)
        session.get = MagicMock(return_value=ctx)

        models = await OllamaProvider.discover_models(
            host="http://localhost:11434", session=session
        )

        assert len(models) == 1
        assert models[0].id == "custom-model:latest"
        assert models[0].supports_tools is False

    async def test_discover_models_uses_correct_url(self):
        """discover_models should call GET {host}/api/tags."""
        session = MagicMock()
        resp_mock = AsyncMock()
        resp_mock.status = 200
        resp_mock.json = AsyncMock(return_value={"models": []})
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=resp_mock)
        ctx.__aexit__ = AsyncMock(return_value=False)
        session.get = MagicMock(return_value=ctx)

        await OllamaProvider.discover_models(
            host="http://myhost:11434", session=session
        )

        session.get.assert_called_once_with("http://myhost:11434/api/tags")

    async def test_discover_models_strips_trailing_slash(self):
        """Trailing slash on host should not cause double-slash in URL."""
        session = MagicMock()
        resp_mock = AsyncMock()
        resp_mock.status = 200
        resp_mock.json = AsyncMock(return_value={"models": []})
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=resp_mock)
        ctx.__aexit__ = AsyncMock(return_value=False)
        session.get = MagicMock(return_value=ctx)

        await OllamaProvider.discover_models(
            host="http://myhost:11434/", session=session
        )

        session.get.assert_called_once_with("http://myhost:11434/api/tags")


class TestOllamaFamilyMatching:
    """Issue #7: Ollama versions family names (qwen35, qwen3, gemma3), so an exact
    match against the whitelist left tool-capable models without tools."""

    @staticmethod
    async def _discover(details: dict) -> list[ModelInfo]:
        ollama_response = {
            "models": [{"name": "m:latest", "model": "m:latest", "details": details}]
        }
        session = MagicMock()
        resp_mock = AsyncMock()
        resp_mock.status = 200
        resp_mock.json = AsyncMock(return_value=ollama_response)
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=resp_mock)
        ctx.__aexit__ = AsyncMock(return_value=False)
        session.get = MagicMock(return_value=ctx)
        return await OllamaProvider.discover_models(
            host="http://localhost:11434", session=session
        )

    @pytest.mark.parametrize(
        "family", ["qwen35", "qwen3", "qwen2", "gemma3", "llama4", "mistral3", "Qwen35"]
    )
    async def test_versioned_family_supports_tools(self, family: str):
        models = await self._discover({"family": family})
        assert models[0].supports_tools is True, f"Family '{family}' should support tools"

    async def test_tool_capable_name_in_families_list_counts(self):
        models = await self._discover({"family": "clip", "families": ["clip", "qwen35"]})
        assert models[0].supports_tools is True

    @pytest.mark.parametrize("family", ["phi3", "codellama", "llava", "nomic-bert", ""])
    async def test_other_families_stay_without_tools(self, family: str):
        models = await self._discover({"family": family, "families": [family]})
        assert models[0].supports_tools is False

    async def test_null_details_and_junk_families_do_not_crash(self):
        assert (await self._discover(None))[0].supports_tools is False  # type: ignore[arg-type]
        models = await self._discover({"family": None, "families": [None, 3, "qwen3"]})
        assert models[0].supports_tools is True
