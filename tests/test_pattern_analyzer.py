"""Tests for Haiku-based pattern analysis."""

import pytest
from unittest.mock import MagicMock, AsyncMock

from src.services.llm.provider import LLMResponse


@pytest.fixture
def mock_provider():
    provider = MagicMock()
    provider.supports_tools = False
    provider.model_name = "test-model"
    provider.provider_name = "test"
    provider.chat = AsyncMock(return_value=LLMResponse(
        text="",
        stop_reason="end",
        tool_calls=None,
    ))
    return provider


class TestPatternAnalyzer:
    @pytest.mark.asyncio
    async def test_analyze_returns_pattern(self, mock_provider):
        from src.analysis.pattern_analyzer import PatternAnalyzer

        mock_provider.chat = AsyncMock(return_value=LLMResponse(
            text='''```json
{
    "pattern": "Connection refused to .* on port \\\\d+",
    "match_type": "regex",
    "explanation": "Connection refused errors to any host on any port"
}
```''',
            stop_reason="end",
        ))

        analyzer = PatternAnalyzer(mock_provider)

        result = await analyzer.analyze_error(
            container="sonarr",
            error_message="Connection refused to api.example.com on port 443",
            recent_logs=["log line 1", "log line 2"],
        )

        assert result is not None
        # A model regex is never compiled; its longest literal run is kept.
        assert result["pattern"] == "Connection refused to"
        assert result["match_type"] == "substring"
        assert "Connection refused" in result["explanation"]

    @pytest.mark.asyncio
    async def test_analyze_returns_substring_for_static_errors(self, mock_provider):
        from src.analysis.pattern_analyzer import PatternAnalyzer

        mock_provider.chat = AsyncMock(return_value=LLMResponse(
            text='''```json
{
    "pattern": "Database connection pool exhausted",
    "match_type": "substring",
    "explanation": "Database pool exhaustion errors"
}
```''',
            stop_reason="end",
        ))

        analyzer = PatternAnalyzer(mock_provider)

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

    @pytest.mark.asyncio
    async def test_analyze_error_caches_results(self):
        """Same error should return cached result without second API call."""
        from src.analysis.pattern_analyzer import PatternAnalyzer

        mock_provider = MagicMock()
        mock_provider.supports_tools = False
        mock_provider.model_name = "test-model"
        mock_provider.provider_name = "test"
        mock_provider.chat = AsyncMock(return_value=LLMResponse(
            text='{"pattern": "Connection timeout", "match_type": "substring", "explanation": "Timeout errors"}',
            stop_reason="end",
        ))

        analyzer = PatternAnalyzer(provider=mock_provider)

        result1 = await analyzer.analyze_error("plex", "Connection timeout error", [])
        result2 = await analyzer.analyze_error("plex", "Connection timeout error", [])

        assert result1 == result2
        assert mock_provider.chat.call_count == 1

    @pytest.mark.asyncio
    async def test_analyze_error_cache_expires(self):
        """Expired cache entries should trigger a new API call."""
        from src.analysis.pattern_analyzer import PatternAnalyzer

        mock_provider = MagicMock()
        mock_provider.supports_tools = False
        mock_provider.model_name = "test-model"
        mock_provider.provider_name = "test"
        mock_provider.chat = AsyncMock(return_value=LLMResponse(
            text='{"pattern": "Connection timeout", "match_type": "substring", "explanation": "Timeout errors"}',
            stop_reason="end",
        ))

        analyzer = PatternAnalyzer(provider=mock_provider)

        result1 = await analyzer.analyze_error("plex", "Connection timeout error", [])

        # Expire the cache by manipulating the stored timestamp
        for key in analyzer._cache:
            ts, val = analyzer._cache[key]
            analyzer._cache[key] = (ts - PatternAnalyzer._CACHE_TTL - 1, val)

        result2 = await analyzer.analyze_error("plex", "Connection timeout error", [])

        assert result1 is not None and result1 == result2
        assert mock_provider.chat.call_count == 2

    @pytest.mark.asyncio
    async def test_analyze_error_failed_result_not_cached(self):
        """Failed analyses (returning None) should not be cached."""
        from src.analysis.pattern_analyzer import PatternAnalyzer

        mock_provider = MagicMock()
        mock_provider.supports_tools = False
        mock_provider.model_name = "test-model"
        mock_provider.provider_name = "test"
        # First call: invalid JSON response
        # Second call: valid response
        mock_provider.chat = AsyncMock(side_effect=[
            LLMResponse(text='not valid json', stop_reason="end"),
            LLMResponse(text='{"pattern": "Some error", "match_type": "substring", "explanation": "Errors"}', stop_reason="end"),
        ])

        analyzer = PatternAnalyzer(provider=mock_provider)

        result1 = await analyzer.analyze_error("plex", "Some error", [])
        assert result1 is None

        result2 = await analyzer.analyze_error("plex", "Some error", [])
        assert result2 is not None
        assert result2["pattern"] == "Some error"
        assert mock_provider.chat.call_count == 2
