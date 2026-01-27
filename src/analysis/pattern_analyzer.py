"""Haiku-based pattern analyzer for generating ignore patterns."""

import json
import logging
import re
from typing import TYPE_CHECKING

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

    def __init__(self, anthropic_client: "anthropic.Anthropic | None"):
        self._client = anthropic_client

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

        logs_text = "\n".join(recent_logs[-30:]) if recent_logs else "(no recent logs)"

        prompt = ANALYSIS_PROMPT.format(
            container=container,
            error_message=error_message,
            recent_logs=logs_text,
        )

        try:
            response = self._client.messages.create(
                model="claude-3-5-haiku-20241022",
                max_tokens=500,
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
            logger.error(f"Error analyzing pattern with Haiku: {e}")
            return None
