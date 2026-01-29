"""Haiku-based pattern analyzer for generating ignore patterns."""

import json
import logging
import re
from typing import TYPE_CHECKING

from src.utils.api_errors import handle_anthropic_error
from src.utils.sanitize import sanitize_container_name, sanitize_logs

if TYPE_CHECKING:
    import anthropic

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

    def __init__(
        self,
        anthropic_client: "anthropic.Anthropic | None",
        model: str = "claude-haiku-4-5-20251001",
        max_tokens: int = 500,
        context_lines: int = 30,
    ):
        self._client = anthropic_client
        self._model = model
        self._max_tokens = max_tokens
        self._context_lines = context_lines

    async def analyze_error(
        self,
        container: str,
        error_message: str,
        recent_logs: list[str],
    ) -> dict | None:
        """Analyze an error and generate an ignore pattern.

        Returns:
            Dict with pattern, match_type, explanation or None if analysis failed.
        """
        if self._client is None:
            logger.warning("No Anthropic client available for pattern analysis")
            return None

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
            response = self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )

            text = response.content[0].text

            # Extract JSON from response (may be wrapped in markdown)
            json_match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
            if not json_match:
                logger.error(f"No JSON found in Haiku response: {text}")
                return None

            result = json.loads(json_match.group())

            # Validate required fields
            if not all(k in result for k in ("pattern", "match_type", "explanation")):
                logger.error(f"Missing fields in Haiku response: {result}")
                return None

            # Validate regex if specified
            if result["match_type"] == "regex":
                try:
                    re.compile(result["pattern"])
                except re.error as e:
                    logger.warning(f"Invalid regex from Haiku, falling back to substring: {e}")
                    result["match_type"] = "substring"

            return result

        except Exception as e:
            error_result = handle_anthropic_error(e)
            logger.log(error_result.log_level, f"Error analyzing pattern with Haiku: {e}")
            return None
