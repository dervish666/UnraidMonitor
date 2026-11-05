"""Buttons stay inside Telegram's 64-byte callback limit and still work.

Telegram rejects the whole message when one button's data is over 64 bytes,
so a long container or Ollama model name made a confirmation, picker or
memory alert fail to send at all.
"""

from unittest.mock import AsyncMock, MagicMock

from aiogram.types import Message

from src.bot.control_commands import build_confirmation, create_ctrl_confirm_callback
from src.bot.model_command import model_select_callback
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


async def test_long_ollama_model_button_selects_the_full_tag():
    model_id = "hf.co/bartowski/Qwen2.5-Coder-32B-Instruct-GGUF:Q4_K_M"
    provider = MagicMock()
    provider.name = "ollama"
    provider.available_models = [MagicMock(id=model_id), MagicMock(id="llama3.2:3b")]
    registry = MagicMock()
    registry.get_available_providers.return_value = [provider]
    registry.get_current_model.return_value = ("ollama", model_id)

    data = fit_callback("model_select:ollama:", model_id)
    assert len(data.encode()) <= 64
    callback = MagicMock()
    callback.data = data
    callback.answer = AsyncMock()
    callback.message = MagicMock(spec=Message)
    callback.message.edit_text = AsyncMock()

    await model_select_callback(registry)(callback)

    registry.set_model.assert_called_once_with("ollama", model_id)
