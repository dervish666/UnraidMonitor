"""
Phase 3 integration tests - verify control commands work end-to-end.
"""
import pytest
from unittest.mock import MagicMock, AsyncMock


@pytest.mark.asyncio
async def test_restart_with_confirmation_flow():
    """Test: Full restart flow with confirmation."""
    from src.state import ContainerStateManager
    from src.models import ContainerInfo
    from src.bot.control_commands import restart_command, create_confirm_handler
    from src.bot.confirmation import ConfirmationManager
    from src.services.container_control import ContainerController

    # Setup
    state = ContainerStateManager()
    state.update(ContainerInfo("radarr", "running", None, "linuxserver/radarr", None))

    mock_container = MagicMock()
    mock_client = MagicMock()
    mock_client.containers.get.return_value = mock_container

    controller = ContainerController(mock_client, protected_containers=[])
    confirmation = ConfirmationManager()

    # Step 1: User sends /restart radarr
    restart_handler = restart_command(state, controller, confirmation)
    message1 = MagicMock()
    message1.text = "/restart radarr"
    message1.from_user.id = 123
    message1.answer = AsyncMock()

    await restart_handler(message1)

    # Should ask for confirmation
    assert "Restart radarr?" in message1.answer.call_args[0][0]
    assert confirmation.get_pending(123) is not None

    # Step 2: User sends 'yes'
    confirm_handler = create_confirm_handler(controller, confirmation)
    message2 = MagicMock()
    message2.text = "yes"
    message2.from_user.id = 123
    message2.answer = AsyncMock()

    await confirm_handler(message2)

    # Should have restarted
    mock_container.restart.assert_called_once()


@pytest.mark.asyncio
async def test_protected_container_rejected():
    """Test: Protected containers cannot be controlled."""
    from src.state import ContainerStateManager
    from src.models import ContainerInfo
    from src.bot.control_commands import restart_command
    from src.bot.confirmation import ConfirmationManager
    from src.services.container_control import ContainerController

    state = ContainerStateManager()
    state.update(ContainerInfo("mariadb", "running", None, "mariadb:latest", None))

    mock_client = MagicMock()
    controller = ContainerController(mock_client, protected_containers=["mariadb"])
    confirmation = ConfirmationManager()

    handler = restart_command(state, controller, confirmation)

    message = MagicMock()
    message.text = "/restart mariadb"
    message.from_user.id = 123
    message.answer = AsyncMock()

    await handler(message)

    # Should reject
    response = message.answer.call_args[0][0]
    assert "protected" in response.lower()
    assert confirmation.get_pending(123) is None


@pytest.mark.asyncio
async def test_confirmation_timeout():
    """Test: Expired confirmation is rejected."""
    from datetime import datetime, timedelta
    from src.bot.control_commands import create_confirm_handler
    from src.bot.confirmation import ConfirmationManager, PendingConfirmation
    from src.services.container_control import ContainerController

    mock_client = MagicMock()
    controller = ContainerController(mock_client, protected_containers=[])
    confirmation = ConfirmationManager()

    # Create an expired confirmation
    confirmation._pending[123] = PendingConfirmation(
        action="restart",
        container_name="radarr",
        expires_at=datetime.now() - timedelta(seconds=1),
    )

    handler = create_confirm_handler(controller, confirmation)

    message = MagicMock()
    message.text = "yes"
    message.from_user.id = 123
    message.answer = AsyncMock()

    await handler(message)

    # Should reject - no pending
    response = message.answer.call_args[0][0]
    assert "No pending" in response
    mock_client.containers.get.assert_not_called()
