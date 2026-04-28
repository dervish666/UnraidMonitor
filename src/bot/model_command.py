# src/bot/model_command.py
"""Handler for /model command — runtime LLM model switching."""

import logging
from typing import Callable, Awaitable, TYPE_CHECKING

from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest

if TYPE_CHECKING:
    from src.services.llm.registry import ProviderRegistry

logger = logging.getLogger(__name__)


def model_command(registry: "ProviderRegistry") -> Callable[[Message], Awaitable[None]]:
    """Factory for /model command handler."""

    async def handler(message: Message) -> None:
        current = registry.get_current_model()
        providers = registry.get_available_providers()

        if not providers:
            await message.answer("No AI providers configured. Set ANTHROPIC_API_KEY, OPENAI_API_KEY, or OLLAMA_HOST.")
            return

        if current:
            provider_name, model_name = current
            text = f"Current model: *{model_name}* ({provider_name})\n\nSelect a provider:"
        else:
            text = "No model selected.\n\nSelect a provider:"

        buttons = []
        for p in providers:
            label = f"{p.display_name} ({len(p.available_models)} models)"
            buttons.append([InlineKeyboardButton(text=label, callback_data=f"model:{p.name}")])

        try:
            await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="Markdown")
        except TelegramBadRequest:
            await message.answer(text.replace("*", ""), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

    return handler


def model_provider_callback(registry: "ProviderRegistry") -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for model provider selection callback."""

    async def handler(callback: CallbackQuery) -> None:
        if not callback.data:
            return
        provider_name = callback.data.split(":", 1)[1]
        providers = registry.get_available_providers()
        provider = next((p for p in providers if p.name == provider_name), None)

        if not provider:
            await callback.answer("Provider not found")
            return

        current = registry.get_current_model()
        current_model = current[1] if current else None

        text = f"{provider.display_name} models:"
        buttons = []
        for m in provider.available_models:
            label = m.name
            if m.id == current_model:
                label = f"✓ {label}"
            if not m.supports_tools:
                label = f"{label} (no tools)"
            buttons.append([InlineKeyboardButton(text=label, callback_data=f"model_select:{provider_name}:{m.id}")])

        buttons.append([InlineKeyboardButton(text="← Back", callback_data="model:back")])

        if isinstance(callback.message, Message):
            await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
        await callback.answer()

    return handler


def model_select_callback(registry: "ProviderRegistry") -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for model selection callback."""

    async def handler(callback: CallbackQuery) -> None:
        if not callback.data:
            return
        parts = callback.data.split(":", 2)
        if len(parts) != 3:
            await callback.answer("Invalid selection")
            return

        _, provider_name, model_name = parts
        registry.set_model(provider_name, model_name)

        if isinstance(callback.message, Message):
            try:
                await callback.message.edit_text(
                    f"✅ Switched to *{model_name}* ({provider_name})",
                    parse_mode="Markdown",
                )
            except TelegramBadRequest:
                await callback.message.edit_text(
                    f"✅ Switched to {model_name} ({provider_name})"
                )
        await callback.answer("Model switched!")

    return handler


def model_back_callback(registry: "ProviderRegistry") -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for back button callback — re-shows provider list."""

    async def handler(callback: CallbackQuery) -> None:
        current = registry.get_current_model()
        providers = registry.get_available_providers()

        if current:
            provider_name, model_name = current
            text = f"Current model: *{model_name}* ({provider_name})\n\nSelect a provider:"
        else:
            text = "No model selected.\n\nSelect a provider:"

        buttons = []
        for p in providers:
            label = f"{p.display_name} ({len(p.available_models)} models)"
            buttons.append([InlineKeyboardButton(text=label, callback_data=f"model:{p.name}")])

        if isinstance(callback.message, Message):
            try:
                await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="Markdown")
            except TelegramBadRequest:
                await callback.message.edit_text(text.replace("*", ""), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
        await callback.answer()

    return handler
