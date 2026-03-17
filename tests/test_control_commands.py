import pytest
from unittest.mock import MagicMock, AsyncMock


@pytest.mark.asyncio
async def test_restart_command_shows_confirmation_buttons():
    """Test that /restart shows inline confirmation buttons."""
    from src.bot.control_commands import restart_command
    from src.state import ContainerStateManager
    from src.models import ContainerInfo

    state = ContainerStateManager()
    state.update(ContainerInfo("radarr", "running", None, "linuxserver/radarr", None))

    controller = MagicMock()
    controller.is_protected.return_value = False

    handler = restart_command(state, controller)

    message = MagicMock()
    message.text = "/restart radarr"
    message.from_user.id = 123
    message.answer = AsyncMock()

    await handler(message)

    # Should respond with confirmation message and inline keyboard
    message.answer.assert_called_once()
    call_kwargs = message.answer.call_args
    response = call_kwargs[0][0]
    assert "Restart radarr?" in response

    # Should have inline keyboard with Confirm and Cancel buttons
    reply_markup = call_kwargs[1].get("reply_markup") if len(call_kwargs) > 1 else call_kwargs.kwargs.get("reply_markup")
    assert reply_markup is not None
    buttons = reply_markup.inline_keyboard[0]
    assert any("Confirm" in b.text for b in buttons)
    assert any("Cancel" in b.text for b in buttons)


@pytest.mark.asyncio
async def test_restart_command_rejects_protected():
    """Test that protected containers cannot be restarted."""
    from src.bot.control_commands import restart_command
    from src.state import ContainerStateManager
    from src.models import ContainerInfo

    state = ContainerStateManager()
    state.update(ContainerInfo("mariadb", "running", None, "mariadb:latest", None))

    controller = MagicMock()
    controller.is_protected.return_value = True

    handler = restart_command(state, controller)

    message = MagicMock()
    message.text = "/restart mariadb"
    message.from_user.id = 123
    message.answer = AsyncMock()

    await handler(message)

    response = message.answer.call_args[0][0]
    assert "protected" in response.lower()


@pytest.mark.asyncio
async def test_restart_command_container_not_found():
    """Test error when container not found."""
    from src.bot.control_commands import restart_command
    from src.state import ContainerStateManager

    state = ContainerStateManager()
    controller = MagicMock()

    handler = restart_command(state, controller)

    message = MagicMock()
    message.text = "/restart nonexistent"
    message.from_user.id = 123
    message.answer = AsyncMock()

    await handler(message)

    response = message.answer.call_args[0][0]
    assert "No container found" in response


@pytest.mark.asyncio
async def test_ctrl_confirm_callback_executes_restart():
    """Test that ctrl_confirm callback executes the action."""
    from src.bot.control_commands import create_ctrl_confirm_callback
    from src.state import ContainerStateManager
    from src.models import ContainerInfo

    state = ContainerStateManager()
    state.update(ContainerInfo("radarr", "running", None, "linuxserver/radarr", None))

    controller = MagicMock()
    controller.is_protected.return_value = False
    controller.restart = AsyncMock(return_value="✅ radarr restarted successfully")

    handler = create_ctrl_confirm_callback(state, controller)

    callback = MagicMock()
    callback.data = "ctrl_confirm:restart:radarr"
    callback.from_user.id = 123
    callback.answer = AsyncMock()
    callback.message = MagicMock()
    callback.message.edit_text = AsyncMock()
    callback.message.bot.send_chat_action = AsyncMock()

    await handler(callback)

    controller.restart.assert_called_once_with("radarr")
    # Should have edited the message with the result
    assert callback.message.edit_text.call_count >= 1


@pytest.mark.asyncio
async def test_ctrl_confirm_callback_rejects_protected():
    """Test that ctrl_confirm rejects protected containers."""
    from src.bot.control_commands import create_ctrl_confirm_callback
    from src.state import ContainerStateManager

    state = ContainerStateManager()
    controller = MagicMock()
    controller.is_protected.return_value = True

    handler = create_ctrl_confirm_callback(state, controller)

    callback = MagicMock()
    callback.data = "ctrl_confirm:restart:radarr"
    callback.from_user.id = 123
    callback.answer = AsyncMock()

    await handler(callback)

    callback.answer.assert_called_once()
    assert "protected" in callback.answer.call_args[0][0].lower()


@pytest.mark.asyncio
async def test_ctrl_confirm_callback_invalid_action():
    """Test that ctrl_confirm rejects unknown actions."""
    from src.bot.control_commands import create_ctrl_confirm_callback
    from src.state import ContainerStateManager

    state = ContainerStateManager()
    controller = MagicMock()

    handler = create_ctrl_confirm_callback(state, controller)

    callback = MagicMock()
    callback.data = "ctrl_confirm:destroy:radarr"
    callback.from_user.id = 123
    callback.answer = AsyncMock()

    await handler(callback)

    callback.answer.assert_called_once()
    assert "Unknown action" in callback.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_ctrl_cancel_callback():
    """Test that ctrl_cancel shows cancellation."""
    from src.bot.control_commands import create_ctrl_cancel_callback

    handler = create_ctrl_cancel_callback()

    callback = MagicMock()
    callback.answer = AsyncMock()
    callback.message = MagicMock()
    callback.message.edit_text = AsyncMock()

    await handler(callback)

    callback.answer.assert_called_once_with("Cancelled")
    callback.message.edit_text.assert_called_once()
    assert "cancelled" in callback.message.edit_text.call_args[0][0].lower()


@pytest.mark.asyncio
async def test_stop_command_shows_buttons():
    """Test that /stop also uses inline buttons."""
    from src.bot.control_commands import stop_command
    from src.state import ContainerStateManager
    from src.models import ContainerInfo

    state = ContainerStateManager()
    state.update(ContainerInfo("plex", "running", None, "plexinc/plex", None))

    controller = MagicMock()
    controller.is_protected.return_value = False

    handler = stop_command(state, controller)

    message = MagicMock()
    message.text = "/stop plex"
    message.from_user.id = 123
    message.answer = AsyncMock()

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]
    assert "Stop plex?" in response

    reply_markup = message.answer.call_args.kwargs.get("reply_markup")
    assert reply_markup is not None


@pytest.mark.asyncio
async def test_ctrl_confirm_callback_executes_all_actions():
    """Test that ctrl_confirm handles all action types."""
    from src.bot.control_commands import create_ctrl_confirm_callback
    from src.state import ContainerStateManager
    from src.models import ContainerInfo

    state = ContainerStateManager()
    state.update(ContainerInfo("plex", "running", None, "plex:latest", None))

    for action, method in [("stop", "stop"), ("start", "start"), ("pull", "pull_and_recreate")]:
        controller = MagicMock()
        controller.is_protected.return_value = False
        setattr(controller, method, AsyncMock(return_value=f"✅ {action} done"))

        handler = create_ctrl_confirm_callback(state, controller)

        callback = MagicMock()
        callback.data = f"ctrl_confirm:{action}:plex"
        callback.from_user.id = 123
        callback.answer = AsyncMock()
        callback.message = MagicMock()
        callback.message.edit_text = AsyncMock()
        callback.message.bot.send_chat_action = AsyncMock()

        await handler(callback)

        getattr(controller, method).assert_called_once_with("plex")


@pytest.mark.asyncio
async def test_restart_command_no_args_shows_styled_usage():
    """Test that /restart without args shows styled usage message."""
    from src.bot.control_commands import restart_command
    from src.state import ContainerStateManager

    state = ContainerStateManager()
    controller = MagicMock()

    handler = restart_command(state, controller)

    message = MagicMock()
    message.text = "/restart"
    message.from_user.id = 123
    message.answer = AsyncMock()

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]
    assert "Usage" in response or "`/restart" in response
    assert "Partial names" in response or "partial" in response.lower()
