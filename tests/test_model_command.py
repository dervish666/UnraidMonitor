"""Tests for /model command."""

import pytest
from unittest.mock import MagicMock, AsyncMock

from aiogram.types import Message
from src.services.llm.provider import ModelInfo
from src.services.llm.registry import ProviderInfo


class TestModelCommand:
    @pytest.fixture
    def mock_registry(self):
        registry = MagicMock()
        registry.get_current_model.return_value = ("anthropic", "claude-haiku-4-5-20251001")
        registry.get_available_providers.return_value = [
            ProviderInfo(
                name="anthropic",
                display_name="Anthropic",
                available_models=[
                    ModelInfo(id="claude-haiku-4-5-20251001", name="Claude Haiku 4.5", provider="anthropic"),
                    ModelInfo(id="claude-sonnet-4-5-20250929", name="Claude Sonnet 4.5", provider="anthropic"),
                ],
            ),
            ProviderInfo(
                name="openai",
                display_name="OpenAI",
                available_models=[
                    ModelInfo(id="gpt-4o", name="GPT-4o", provider="openai"),
                ],
            ),
        ]
        return registry

    @pytest.mark.asyncio
    async def test_model_command_shows_current_and_providers(self, mock_registry):
        from src.bot.model_command import model_command

        message = MagicMock()
        message.answer = AsyncMock()

        handler = model_command(mock_registry)
        await handler(message)

        message.answer.assert_called_once()
        call_text = message.answer.call_args[0][0]
        assert "claude-haiku" in call_text.lower()

    @pytest.mark.asyncio
    async def test_model_command_no_providers(self, mock_registry):
        from src.bot.model_command import model_command

        mock_registry.get_available_providers.return_value = []
        message = MagicMock()
        message.answer = AsyncMock()

        handler = model_command(mock_registry)
        await handler(message)

        call_text = message.answer.call_args[0][0]
        assert "no ai providers" in call_text.lower()

    @pytest.mark.asyncio
    async def test_model_provider_callback_shows_models(self, mock_registry):
        from src.bot.model_command import model_provider_callback

        callback = MagicMock()
        callback.data = "model:anthropic"
        callback.message = MagicMock(spec=Message)
        callback.message.edit_text = AsyncMock()
        callback.answer = AsyncMock()

        handler = model_provider_callback(mock_registry)
        await handler(callback)

        callback.message.edit_text.assert_called_once()
        call_text = callback.message.edit_text.call_args[0][0]
        assert "Anthropic" in call_text

    @pytest.mark.asyncio
    async def test_model_provider_callback_marks_current(self, mock_registry):
        from src.bot.model_command import model_provider_callback

        callback = MagicMock()
        callback.data = "model:anthropic"
        callback.message = MagicMock(spec=Message)
        callback.message.edit_text = AsyncMock()
        callback.answer = AsyncMock()

        handler = model_provider_callback(mock_registry)
        await handler(callback)

        # Check keyboard buttons for ✓ marker
        call_kwargs = callback.message.edit_text.call_args
        keyboard = call_kwargs[1]["reply_markup"] if "reply_markup" in call_kwargs[1] else call_kwargs.kwargs["reply_markup"]
        button_texts = [row[0].text for row in keyboard.inline_keyboard]
        assert any("✓" in t for t in button_texts)

    @pytest.mark.asyncio
    async def test_model_provider_callback_not_found(self, mock_registry):
        from src.bot.model_command import model_provider_callback

        callback = MagicMock()
        callback.data = "model:nonexistent"
        callback.answer = AsyncMock()

        handler = model_provider_callback(mock_registry)
        await handler(callback)

        callback.answer.assert_called_once_with("Provider not found")

    @pytest.mark.asyncio
    async def test_model_select_callback_switches_model(self, mock_registry):
        from src.bot.model_command import model_select_callback

        callback = MagicMock()
        callback.data = "model_select:openai:gpt-4o"
        callback.message = MagicMock(spec=Message)
        callback.message.edit_text = AsyncMock()
        callback.answer = AsyncMock()

        handler = model_select_callback(mock_registry)
        await handler(callback)

        mock_registry.set_model.assert_called_once_with("openai", "gpt-4o")
        callback.message.edit_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_model_back_callback_shows_providers(self, mock_registry):
        from src.bot.model_command import model_back_callback

        callback = MagicMock()
        callback.message = MagicMock(spec=Message)
        callback.message.edit_text = AsyncMock()
        callback.answer = AsyncMock()

        handler = model_back_callback(mock_registry)
        await handler(callback)

        callback.message.edit_text.assert_called_once()
        call_text = callback.message.edit_text.call_args[0][0]
        assert "claude-haiku" in call_text.lower()

    @pytest.mark.asyncio
    async def test_model_select_invalid_data(self, mock_registry):
        from src.bot.model_command import model_select_callback

        callback = MagicMock()
        callback.data = "model_select:bad"
        callback.answer = AsyncMock()

        handler = model_select_callback(mock_registry)
        await handler(callback)

        callback.answer.assert_called_once_with("Invalid selection")

    @pytest.mark.asyncio
    async def test_model_provider_shows_no_tools_marker(self):
        from src.bot.model_command import model_provider_callback

        registry = MagicMock()
        registry.get_current_model.return_value = ("ollama", "llama3:latest")
        registry.get_available_providers.return_value = [
            ProviderInfo(
                name="ollama",
                display_name="Ollama",
                available_models=[
                    ModelInfo(id="llama3:latest", name="Llama 3", provider="ollama", supports_tools=False),
                ],
            ),
        ]

        callback = MagicMock()
        callback.data = "model:ollama"
        callback.message = MagicMock(spec=Message)
        callback.message.edit_text = AsyncMock()
        callback.answer = AsyncMock()

        handler = model_provider_callback(registry)
        await handler(callback)

        call_kwargs = callback.message.edit_text.call_args
        keyboard = call_kwargs[1]["reply_markup"] if "reply_markup" in call_kwargs[1] else call_kwargs.kwargs["reply_markup"]
        button_texts = [row[0].text for row in keyboard.inline_keyboard if row[0].callback_data != "model:back"]
        assert any("no tools" in t for t in button_texts)
