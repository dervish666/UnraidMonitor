"""Haiku-based pattern analyzer for generating ignore patterns."""

import hashlib
import json
import logging
import re
import time
from typing import Any, TYPE_CHECKING

from src.utils.api_errors import handle_llm_error
from src.utils.sanitize import sanitize_container_name, sanitize_logs

if TYPE_CHECKING:
    from src.services.llm.provider import LLMProvider

logger = logging.getLogger(__name__)

ANALYSIS_PROMPT = """Analyze this error from a Docker container log and create a pattern to match it and similar variations.

Container: {container}
Error: {error_message}
Recent logs for context:
{recent_logs}

Return ONLY a JSON object (no markdown, no explanation):
{{
    "pattern": "the regex or substring pattern",
    "match_type": "regex" or "substring",
    "explanation": "human-readable description of what this ignores"
}}

Guidelines:
- Prefer simple substrings when the error message is static (no variable parts)
- Use regex only when there are variable parts like timestamps, IPs, file paths, ports, counts
- For regex, use Python regex syntax
- Keep patterns as simple as possible while still matching variations
- The explanation should be concise (under 50 words)"""


class PatternAnalyzer:
    """Uses Claude Haiku to analyze errors and generate ignore patterns."""

    _CACHE_TTL = 3600  # 1 hour

    def __init__(
        self,
        provider: "LLMProvider | None",
        max_tokens: int = 500,
        context_lines: int = 30,
    ):
        self._provider = provider
        self._max_tokens = max_tokens
        self._context_lines = context_lines
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}

    async def analyze_error(
        self,
        container: str,
        error_message: str,
        recent_logs: list[str],
    ) -> dict[str, Any] | None:
        """Analyze an error and generate an ignore pattern.

        Returns:
            Dict with pattern, match_type, explanation or None if analysis failed.
        """
        if self._provider is None:
            logger.warning("No AI provider available for pattern analysis")
            return None

        # Check cache
        cache_key = hashlib.md5(f"{container}:{error_message}".encode()).hexdigest()
        cached = self._cache.get(cache_key)
        if cached and (time.monotonic() - cached[0]) < self._CACHE_TTL:
            return cached[1]

        logs_text = "\n".join(recent_logs[-self._context_lines:]) if recent_logs else "(no recent logs)"

        # Sanitize user-controlled inputs to prevent prompt injection
        safe_container = sanitize_container_name(container)
        safe_error = sanitize_logs(error_message, max_length=2000)
        safe_logs = sanitize_logs(logs_text)

        prompt = ANALYSIS_PROMPT.format(
            container=safe_container,
            error_message=safe_error,
            recent_logs=safe_logs,
        )

        try:
            response = await self._provider.chat(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=self._max_tokens,
            )

            text = response.text

            # Extract JSON from response (may be wrapped in markdown)
            json_match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
            if not json_match:
                logger.warning(f"No JSON found in Haiku response: {text[:200]}")
                return None

            try:
                result: dict[str, Any] = json.loads(json_match.group())
            except json.JSONDecodeError as e:
                logger.warning(f"Invalid JSON in Haiku response: {e}")
                return None

            # Validate required fields
            if not all(k in result for k in ("pattern", "match_type", "explanation")):
                logger.warning(f"Missing fields in Haiku response: {result}")
                return None

            # Validate regex if specified
            if result["match_type"] == "regex":
                try:
                    re.compile(result["pattern"])
                except re.error as e:
                    logger.warning(f"Invalid regex from Haiku, falling back to substring: {e}")
                    result["match_type"] = "substring"

            # Cache the result (evict least-recently-used if over limit)
            if len(self._cache) >= 256:
                oldest_key = min(self._cache, key=lambda k: self._cache[k][0])
                del self._cache[oldest_key]
            self._cache[cache_key] = (time.monotonic(), result)

            return result

        except Exception as e:
            error_result = handle_llm_error(e)
            logger.log(error_result.log_level, f"Error analyzing pattern with Haiku: {e}")
            return None
