"""
Phase 3 integration tests - verify control commands work end-to-end with inline buttons.
"""
import pytest
from unittest.mock import MagicMock, AsyncMock


@pytest.mark.asyncio
async def test_restart_with_confirmation_flow():
    """Test: Full restart flow with inline button confirmation."""
    from src.state import ContainerStateManager
    from src.models import ContainerInfo
    from src.bot.control_commands import restart_command, create_ctrl_confirm_callback
    from src.services.container_control import ContainerController

    # Setup
    state = ContainerStateManager()
    state.update(ContainerInfo("radarr", "running", None, "linuxserver/radarr", None))

    mock_container = MagicMock()
    mock_client = MagicMock()
    mock_client.containers.get.return_value = mock_container

    controller = ContainerController(mock_client, protected_containers=[])

    # Step 1: User sends /restart radarr
    restart_handler = restart_command(state, controller)
    message1 = MagicMock()
    message1.text = "/restart radarr"
    message1.from_user.id = 123
    message1.answer = AsyncMock()

    await restart_handler(message1)

    # Should show confirmation with inline buttons
    response = message1.answer.call_args[0][0]
    assert "Restart radarr?" in response

    reply_markup = message1.answer.call_args.kwargs.get("reply_markup")
    assert reply_markup is not None
    buttons = reply_markup.inline_keyboard[0]
    assert any("Confirm" in b.text for b in buttons)

    # Step 2: User clicks Confirm button
    confirm_handler = create_ctrl_confirm_callback(state, controller)
    callback = MagicMock()
    callback.data = "ctrl_confirm:restart:radarr"
    callback.from_user.id = 123
    callback.answer = AsyncMock()
    callback.message = MagicMock()
    callback.message.edit_text = AsyncMock()
    callback.message.bot.send_chat_action = AsyncMock()

    await confirm_handler(callback)

    # Should have restarted
    mock_container.restart.assert_called_once()


@pytest.mark.asyncio
async def test_protected_container_rejected():
    """Test: Protected containers cannot be controlled."""
    from src.state import ContainerStateManager
    from src.models import ContainerInfo
    from src.bot.control_commands import restart_command
    from src.services.container_control import ContainerController

    state = ContainerStateManager()
    state.update(ContainerInfo("mariadb", "running", None, "mariadb:latest", None))

    mock_client = MagicMock()
    controller = ContainerController(mock_client, protected_containers=["mariadb"])

    handler = restart_command(state, controller)

    message = MagicMock()
    message.text = "/restart mariadb"
    message.from_user.id = 123
    message.answer = AsyncMock()

    await handler(message)

    # Should reject
    response = message.answer.call_args[0][0]
    assert "protected" in response.lower()


@pytest.mark.asyncio
async def test_confirm_callback_rejects_protected():
    """Test: Protected containers rejected at callback level too."""
    from src.state import ContainerStateManager
    from src.bot.control_commands import create_ctrl_confirm_callback
    from src.services.container_control import ContainerController

    state = ContainerStateManager()
    mock_client = MagicMock()
    controller = ContainerController(mock_client, protected_containers=["radarr"])

    handler = create_ctrl_confirm_callback(state, controller)

    callback = MagicMock()
    callback.data = "ctrl_confirm:restart:radarr"
    callback.from_user.id = 123
    callback.answer = AsyncMock()

    await handler(callback)

    callback.answer.assert_called_once()
    assert "protected" in callback.answer.call_args[0][0].lower()
