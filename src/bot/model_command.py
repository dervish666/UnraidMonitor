# src/bot/model_command.py
"""Handler for /model command — runtime LLM model switching.

Usage:
    /model                      — show current models and provider picker
    /model <family>             — set global default (e.g. /model sonnet)
    /model <feature> <family>   — set per-feature model (e.g. /model chat sonnet)
    /model <feature> default    — reset feature to use global default
"""

import logging
from typing import Callable, Awaitable, TYPE_CHECKING

from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest

if TYPE_CHECKING:
    from src.services.llm.registry import ProviderRegistry

logger = logging.getLogger(__name__)

# Maps user-facing feature names to internal registry keys
_FEATURE_ALIASES: dict[str, str] = {
    "chat": "nl_processor",
    "nl": "nl_processor",
    "diagnose": "diagnostic",
    "diag": "diagnostic",
    "analyze": "pattern_analyzer",
    "patterns": "pattern_analyzer",
}

_FEATURE_DISPLAY: dict[str, str] = {
    "nl_processor": "Chat",
    "diagnostic": "Diagnose",
    "pattern_analyzer": "Analyze",
}

_ALL_FEATURES = ("nl_processor", "diagnostic", "pattern_analyzer")


def _format_status(registry: "ProviderRegistry") -> str:
    """Build the current model status text."""
    current = registry.get_current_model()
    feature_models = registry.get_feature_models()

    lines = ["*Current models:*"]
    if current:
        lines.append(f"  Default: `{current[1]}` ({current[0]})")
    else:
        lines.append("  Default: _none_")

    for feat in _ALL_FEATURES:
        label = _FEATURE_DISPLAY[feat]
        if feat in feature_models:
            lines.append(f"  {label}: `{feature_models[feat]}`")
        else:
            lines.append(f"  {label}: _default_")

    return "\n".join(lines)


def model_command(registry: "ProviderRegistry") -> Callable[[Message], Awaitable[None]]:
    """Factory for /model command handler."""

    async def handler(message: Message) -> None:
        text = (message.text or "").strip()
        parts = text.split()
        # parts[0] is "/model"
        args = parts[1:] if len(parts) > 1 else []

        # /model <feature> <model|default>
        if len(args) >= 2 and args[0].lower() in _FEATURE_ALIASES:
            feature_key = _FEATURE_ALIASES[args[0].lower()]
            model_arg = args[1].lower()
            label = _FEATURE_DISPLAY[feature_key]

            if model_arg == "default":
                removed = registry.clear_feature_model(feature_key)
                if removed:
                    msg = f"↩️ *{label}* reset to global default"
                else:
                    msg = f"*{label}* is already using the global default"
                try:
                    await message.answer(msg, parse_mode="Markdown")
                except TelegramBadRequest:
                    await message.answer(msg.replace("*", "").replace("`", ""))
                return

            resolved = registry.set_feature_model(feature_key, model_arg)
            msg = f"✅ *{label}* model set to `{resolved}`"
            try:
                await message.answer(msg, parse_mode="Markdown")
            except TelegramBadRequest:
                await message.answer(msg.replace("*", "").replace("`", ""))
            return

        # /model <model> — set global default directly
        if len(args) == 1 and args[0].lower() not in _FEATURE_ALIASES:
            model_arg = args[0]
            providers = registry.get_available_providers()
            # Try to detect which provider serves this model
            for p in providers:
                model_ids = {m.id for m in p.available_models}
                if model_arg.lower() in model_ids or model_arg in model_ids:
                    registry.set_model(p.name, model_arg)
                    current = registry.get_current_model()
                    resolved = current[1] if current else model_arg
                    msg = f"✅ Default model set to `{resolved}` ({p.name})"
                    try:
                        await message.answer(msg, parse_mode="Markdown")
                    except TelegramBadRequest:
                        await message.answer(msg.replace("*", "").replace("`", ""))
                    return

            # Model not in well-known list — try anyway if it looks like a known provider
            if model_arg.startswith("claude-"):
                registry.set_model("anthropic", model_arg)
            elif model_arg.startswith("gpt-") or model_arg.startswith("o1") or model_arg.startswith("o3"):
                registry.set_model("openai", model_arg)
            else:
                await message.answer(
                    f"Unknown model `{model_arg}`\\. Use a family name "
                    f"\\(sonnet, haiku, opus\\) or a full model ID\\.",
                    parse_mode="MarkdownV2",
                )
                return

            current = registry.get_current_model()
            resolved = current[1] if current else model_arg
            provider = current[0] if current else "?"
            msg = f"✅ Default model set to `{resolved}` ({provider})"
            try:
                await message.answer(msg, parse_mode="Markdown")
            except TelegramBadRequest:
                await message.answer(msg.replace("*", "").replace("`", ""))
            return

        # /model (no args) — show status and provider selection buttons
        providers = registry.get_available_providers()

        if not providers:
            await message.answer("No AI providers configured. Set ANTHROPIC_API_KEY, OPENAI_API_KEY, or OLLAMA_HOST.")
            return

        status = _format_status(registry)
        help_text = "\n\n_Quick set: `/model sonnet` or `/model chat haiku`_"
        display = f"{status}{help_text}\n\nSelect a provider:"

        buttons = []
        for p in providers:
            label = f"{p.display_name} ({len(p.available_models)} models)"
            buttons.append([InlineKeyboardButton(text=label, callback_data=f"model:{p.name}")])

        try:
            await message.answer(display, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="Markdown")
        except TelegramBadRequest:
            await message.answer(
                display.replace("*", "").replace("_", "").replace("`", ""),
                reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
            )

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

        current = registry.get_current_model()
        resolved = current[1] if current else model_name

        if isinstance(callback.message, Message):
            try:
                await callback.message.edit_text(
                    f"✅ Default model set to `{resolved}` ({provider_name})",
                    parse_mode="Markdown",
                )
            except TelegramBadRequest:
                await callback.message.edit_text(
                    f"✅ Default model set to {resolved} ({provider_name})"
                )
        await callback.answer("Model switched!")

    return handler


def model_back_callback(registry: "ProviderRegistry") -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for back button callback — re-shows provider list."""

    async def handler(callback: CallbackQuery) -> None:
        providers = registry.get_available_providers()
        status = _format_status(registry)
        help_text = "\n\n_Quick set: `/model sonnet` or `/model chat haiku`_"
        display = f"{status}{help_text}\n\nSelect a provider:"

        buttons = []
        for p in providers:
            label = f"{p.display_name} ({len(p.available_models)} models)"
            buttons.append([InlineKeyboardButton(text=label, callback_data=f"model:{p.name}")])

        if isinstance(callback.message, Message):
            try:
                await callback.message.edit_text(display, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="Markdown")
            except TelegramBadRequest:
                await callback.message.edit_text(
                    display.replace("*", "").replace("_", "").replace("`", ""),
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
                )
        await callback.answer()

    return handler
