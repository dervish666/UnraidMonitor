"""Buttons stay inside Telegram's 64-byte callback limit and still work.

Telegram rejects the whole message when one button's data is over 64 bytes,
so a long container or Ollama model name made a confirmation, picker or
memory alert fail to send at all.
"""

from unittest.mock import AsyncMock, MagicMock

from aiogram.types import Message

from src.bot.control_commands import build_confirmation, create_ctrl_confirm_callback
from src.bot.model_command import model_provider_callback, model_select_callback
from src.models import ContainerInfo
from src.services.container_control import ContainerController
from src.state import ContainerStateManager
from src.utils.formatting import fit_callback, resolve_cut_name

LONG = "linuxserver-qbittorrentvpn-with-wireguard-and-privoxy-enabled"


def test_fit_callback_keeps_fixed_fields_whole():
    data = fit_callback("logs:", LONG, ":50")
    assert len(data.encode()) <= 64
    assert data.startswith("logs:") and data.endswith("…:50")


def test_short_names_are_untouched():
    assert fit_callback("restart:", "plex") == "restart:plex"
    assert resolve_cut_name("plex", ["plex", "plexx"]) == "plex"


def test_ambiguous_cut_name_resolves_to_nothing():
    assert resolve_cut_name("abc…", ["abcdef", "abcxyz"]) is None


async def test_long_container_confirm_button_fits_and_runs():
    state = ContainerStateManager()
    state.update(ContainerInfo(name=LONG, status="running", health=None, image="x", started_at=None))
    _, keyboard = build_confirmation("restart", LONG, "running")
    data = keyboard.inline_keyboard[0][0].callback_data
    assert len(data.encode()) <= 64

    container = MagicMock(status="running")
    docker_client = MagicMock()
    docker_client.containers.get.return_value = container
    callback = MagicMock()
    callback.data = data
    callback.answer = AsyncMock()
    callback.message = None
    await create_ctrl_confirm_callback(state, ContainerController(docker_client, []))(callback)

    docker_client.containers.get.assert_called_with(LONG)
    container.restart.assert_called_once()


async def test_two_long_quantisations_are_each_selectable():
    """Both tags cut to the same prefix, so neither button could pick its model."""
    q4 = "hf.co/bartowski/Qwen2.5-Coder-32B-Instruct-GGUF:Q4_K_M"
    q8 = "hf.co/bartowski/Qwen2.5-Coder-32B-Instruct-GGUF:Q8_0"
    provider = MagicMock()
    provider.name = "ollama"
    provider.display_name = "Ollama"
    provider.available_models = [
        MagicMock(id=q4, supports_tools=True), MagicMock(id=q8, supports_tools=True),
        MagicMock(id="llama3.2:3b", supports_tools=True),
    ]
    for m in provider.available_models:
        m.name = m.id
    registry = MagicMock()
    registry.get_available_providers.return_value = [provider]
    registry.get_current_model.return_value = ("ollama", "llama3.2:3b")

    # Render the picker through its real handler and read the buttons it sent.
    picker = MagicMock()
    picker.data = "model:ollama"
    picker.answer = AsyncMock()
    picker.message = MagicMock(spec=Message)
    picker.message.edit_text = AsyncMock()
    await model_provider_callback(registry)(picker)
    markup = picker.message.edit_text.await_args.kwargs["reply_markup"]
    data = {row[0].text: row[0].callback_data for row in markup.inline_keyboard[:-1]}
    assert all(len(d.encode()) <= 64 for d in data.values())
    assert data[q4] != data[q8]

    for model_id in (q4, q8):
        callback = MagicMock()
        callback.data = data[model_id]
        callback.answer = AsyncMock()
        callback.message = MagicMock(spec=Message)
        callback.message.edit_text = AsyncMock()
        await model_select_callback(registry)(callback)
        registry.set_model.assert_called_with("ollama", model_id)
