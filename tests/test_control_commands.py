import pytest
from unittest.mock import MagicMock, AsyncMock


@pytest.mark.asyncio
async def test_restart_command_requests_confirmation():
    """Test that /restart asks for confirmation."""
    from src.bot.control_commands import restart_command
    from src.bot.confirmation import ConfirmationManager
    from src.state import ContainerStateManager
    from src.models import ContainerInfo

    state = ContainerStateManager()
    state.update(ContainerInfo("radarr", "running", None, "linuxserver/radarr", None))

    confirmation = ConfirmationManager()
    controller = MagicMock()
    controller.is_protected.return_value = False

    handler = restart_command(state, controller, confirmation)

    message = MagicMock()
    message.text = "/restart radarr"
    message.from_user.id = 123
    message.answer = AsyncMock()

    await handler(message)

    # Should ask for confirmation
    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]
    assert "Restart radarr?" in response
    assert "yes" in response.lower()

    # Should have pending confirmation
    pending = confirmation.get_pending(123)
    assert pending is not None
    assert pending.action == "restart"
    assert pending.container_name == "radarr"


@pytest.mark.asyncio
async def test_restart_command_rejects_protected():
    """Test that protected containers cannot be restarted."""
    from src.bot.control_commands import restart_command
    from src.bot.confirmation import ConfirmationManager
    from src.state import ContainerStateManager
    from src.models import ContainerInfo

    state = ContainerStateManager()
    state.update(ContainerInfo("mariadb", "running", None, "mariadb:latest", None))

    confirmation = ConfirmationManager()
    controller = MagicMock()
    controller.is_protected.return_value = True

    handler = restart_command(state, controller, confirmation)

    message = MagicMock()
    message.text = "/restart mariadb"
    message.from_user.id = 123
    message.answer = AsyncMock()

    await handler(message)

    response = message.answer.call_args[0][0]
    assert "protected" in response.lower()

    # Should NOT have pending confirmation
    assert confirmation.get_pending(123) is None


@pytest.mark.asyncio
async def test_restart_command_container_not_found():
    """Test error when container not found."""
    from src.bot.control_commands import restart_command
    from src.bot.confirmation import ConfirmationManager
    from src.state import ContainerStateManager

    state = ContainerStateManager()
    confirmation = ConfirmationManager()
    controller = MagicMock()

    handler = restart_command(state, controller, confirmation)

    message = MagicMock()
    message.text = "/restart nonexistent"
    message.from_user.id = 123
    message.answer = AsyncMock()

    await handler(message)

    response = message.answer.call_args[0][0]
    assert "No container found" in response


@pytest.mark.asyncio
async def test_confirm_handler_executes_action():
    """Test that 'yes' executes pending action."""
    from src.bot.control_commands import create_confirm_handler
    from src.bot.confirmation import ConfirmationManager

    confirmation = ConfirmationManager()
    confirmation.request(user_id=123, action="restart", container_name="radarr")

    controller = MagicMock()
    controller.restart = AsyncMock(return_value="✅ radarr restarted successfully")

    handler = create_confirm_handler(controller, confirmation)

    message = MagicMock()
    message.text = "yes"
    message.from_user.id = 123
    message.answer = AsyncMock()

    await handler(message)

    controller.restart.assert_called_once_with("radarr")
    response = message.answer.call_args[0][0]
    assert "restarted" in response.lower()


@pytest.mark.asyncio
async def test_confirm_handler_no_pending():
    """Test 'yes' with no pending confirmation is ignored."""
    from src.bot.control_commands import create_confirm_handler
    from src.bot.confirmation import ConfirmationManager

    confirmation = ConfirmationManager()
    controller = MagicMock()

    handler = create_confirm_handler(controller, confirmation)

    message = MagicMock()
    message.text = "yes"
    message.from_user.id = 123
    message.answer = AsyncMock()

    await handler(message)

    # Should not call any controller method
    controller.restart.assert_not_called()
    controller.stop.assert_not_called()
    controller.start.assert_not_called()

    # Should inform user
    response = message.answer.call_args[0][0]
    assert "No pending" in response
