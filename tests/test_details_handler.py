import pytest
from unittest.mock import MagicMock, AsyncMock


@pytest.mark.asyncio
async def test_details_filter_matches_yes():
    """Test DetailsFilter matches 'yes' and variants."""
    from src.bot.telegram_bot import DetailsFilter
    from aiogram.types import Message

    filter_instance = DetailsFilter()

    for text in ["yes", "Yes", "YES", "more", "details", "More Details", " yes "]:
        message = MagicMock(spec=Message)
        message.text = text
        result = await filter_instance(message)
        assert result is True, f"Expected True for '{text}'"

    for text in ["no", "help", "/status", "yess", None]:
        message = MagicMock(spec=Message)
        message.text = text
        result = await filter_instance(message)
        assert result is False, f"Expected False for '{text}'"


@pytest.mark.asyncio
async def test_details_handler_returns_detailed_analysis():
    """Test details handler returns detailed analysis."""
    from src.bot.telegram_bot import create_details_handler
    from src.services.diagnostic import DiagnosticService

    mock_service = MagicMock(spec=DiagnosticService)
    mock_service.has_pending.return_value = True
    mock_service.get_details = AsyncMock(return_value="Detailed analysis: root cause is...")

    handler = create_details_handler(mock_service)

    message = MagicMock()
    message.text = "yes"
    message.from_user.id = 123
    message.answer = AsyncMock()

    await handler(message)

    mock_service.get_details.assert_called_once_with(123)
    response = message.answer.call_args[0][0]
    assert "Detailed" in response


@pytest.mark.asyncio
async def test_details_handler_ignores_when_no_pending():
    """Test details handler ignores when no pending context."""
    from src.bot.telegram_bot import create_details_handler
    from src.services.diagnostic import DiagnosticService

    mock_service = MagicMock(spec=DiagnosticService)
    mock_service.has_pending.return_value = False

    handler = create_details_handler(mock_service)

    message = MagicMock()
    message.text = "yes"
    message.from_user.id = 123
    message.answer = AsyncMock()

    await handler(message)

    # Should not respond when no pending context
    message.answer.assert_not_called()
