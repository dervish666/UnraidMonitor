"""Tests for Haiku-based pattern analysis."""

import pytest
from unittest.mock import MagicMock


@pytest.fixture
def mock_anthropic_client():
    client = MagicMock()
    return client


class TestPatternAnalyzer:
    @pytest.mark.asyncio
    async def test_analyze_returns_pattern(self, mock_anthropic_client):
        from src.analysis.pattern_analyzer import PatternAnalyzer

        # Mock Haiku response
        mock_response = MagicMock()
        mock_response.content = [MagicMock()]
        mock_response.content[0].text = '''```json
{
    "pattern": "Connection refused to .* on port \\\\d+",
    "match_type": "regex",
    "explanation": "Connection refused errors to any host on any port"
}
```'''
        mock_anthropic_client.messages.create.return_value = mock_response

        analyzer = PatternAnalyzer(mock_anthropic_client)

        result = await analyzer.analyze_error(
            container="sonarr",
            error_message="Connection refused to api.example.com on port 443",
            recent_logs=["log line 1", "log line 2"],
        )

        assert result is not None
        assert result["pattern"] == "Connection refused to .* on port \\d+"
        assert result["match_type"] == "regex"
        assert "Connection refused" in result["explanation"]

    @pytest.mark.asyncio
    async def test_analyze_returns_substring_for_static_errors(self, mock_anthropic_client):
        from src.analysis.pattern_analyzer import PatternAnalyzer

        mock_response = MagicMock()
        mock_response.content = [MagicMock()]
        mock_response.content[0].text = '''```json
{
    "pattern": "Database connection pool exhausted",
    "match_type": "substring",
    "explanation": "Database pool exhaustion errors"
}
```'''
        mock_anthropic_client.messages.create.return_value = mock_response

        analyzer = PatternAnalyzer(mock_anthropic_client)

        result = await analyzer.analyze_error(
            container="app",
            error_message="Database connection pool exhausted",
            recent_logs=[],
        )

        assert result["match_type"] == "substring"

    @pytest.mark.asyncio
    async def test_analyze_returns_none_when_no_client(self):
        from src.analysis.pattern_analyzer import PatternAnalyzer

        analyzer = PatternAnalyzer(None)

        result = await analyzer.analyze_error(
            container="sonarr",
            error_message="Some error",
            recent_logs=[],
        )

        assert result is None
