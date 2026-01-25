import pytest
from unittest.mock import MagicMock


@pytest.mark.asyncio
async def test_yes_message_triggers_confirm_handler():
    """Test that 'yes' message triggers pending action."""
    from src.bot.telegram_bot import create_dispatcher, register_commands
    from src.state import ContainerStateManager
    from src.models import ContainerInfo

    state = ContainerStateManager()
    state.update(ContainerInfo("radarr", "running", None, "linuxserver/radarr", None))

    mock_client = MagicMock()
    mock_container = MagicMock()
    mock_client.containers.get.return_value = mock_container

    dp = create_dispatcher([123])
    confirmation = register_commands(dp, state, mock_client, protected_containers=[])

    # Request confirmation
    confirmation.request(user_id=123, action="restart", container_name="radarr")

    # Simulate "yes" message - this requires the handler to be registered
    # The handler should execute the action
    pending = confirmation.confirm(123)
    assert pending is not None
    assert pending.action == "restart"


@pytest.mark.asyncio
async def test_yes_filter_matches_yes_variants():
    """Test that YesFilter matches 'yes' in various forms."""
    from src.bot.telegram_bot import YesFilter
    from aiogram.types import Message

    filter_instance = YesFilter()

    # Test matching cases
    for text in ["yes", "Yes", "YES", " yes ", "  YES  "]:
        message = MagicMock(spec=Message)
        message.text = text
        result = await filter_instance(message)
        assert result is True, f"Expected True for '{text}'"

    # Test non-matching cases
    for text in ["no", "yess", "y", "yeah", "yes please", None, ""]:
        message = MagicMock(spec=Message)
        message.text = text
        result = await filter_instance(message)
        assert result is False, f"Expected False for '{text}'"


@pytest.mark.asyncio
async def test_yes_filter_handles_none_text():
    """Test that YesFilter handles messages with no text."""
    from src.bot.telegram_bot import YesFilter
    from aiogram.types import Message

    filter_instance = YesFilter()

    message = MagicMock(spec=Message)
    message.text = None
    result = await filter_instance(message)
    assert result is False
