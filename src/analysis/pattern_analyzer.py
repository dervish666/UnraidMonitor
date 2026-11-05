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
    "pattern": "exact text copied from the error",
    "match_type": "substring",
    "explanation": "human-readable description of what this ignores"
}}

Guidelines:
- The pattern is matched as plain text (case-insensitive), never as a regex
- Copy the longest part of the error that stays the same every time it occurs
- Leave out variable parts: timestamps, IPs, ports, paths, IDs, counts
- Do not pick text so generic it would hide other errors (such as just "error")
- The explanation should be concise (under 50 words)"""


# Shorter literals ("error", "failed") would hide unrelated errors.
_MIN_LITERAL = 8


def _literal_text(pattern: str, is_regex: bool) -> str:
    """The longest literal run in a model-proposed pattern.

    A substring is returned as-is. For a regex, the text is split at every
    metacharacter or class escape and the longest literal piece is kept, so
    "Failed to fetch \\[metadata\\] from .*" becomes
    "Failed to fetch [metadata] from". Alternation keeps one branch, which
    ignores less than the model asked for, never more.
    """
    if not is_regex:
        return pattern.strip()
    pieces: list[str] = []
    current: list[str] = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\" and i + 1 < len(pattern):
            nxt = pattern[i + 1]
            if nxt.isalnum():  # \d \w \s \b ...: a variable part
                pieces.append("".join(current))
                current = []
            else:
                current.append(nxt)
            i += 2
            continue
        if ch in ".^$*+?{}[]()|":
            if ch in "*+?{" and current:
                current.pop()  # the quantified character is optional or repeated
            pieces.append("".join(current))
            current = []
            # Skip a whole character class or quantifier body.
            closer = {"[": "]", "{": "}"}.get(ch)
            if closer:
                end = pattern.find(closer, i + 1)
                i = end if end != -1 else i
            i += 1
            continue
        current.append(ch)
        i += 1
    pieces.append("".join(current))
    return max((p.strip() for p in pieces), key=len, default="")


class PatternAnalyzer:
    """Uses Claude Haiku to analyze errors and generate ignore patterns."""

    _CACHE_TTL = 3600  # 1 hour

    def __init__(
        self,
        provider: "LLMProvider | None" = None,
        max_tokens: int = 500,
        context_lines: int = 30,
        registry: Any = None,
        feature: str = "pattern_analyzer",
    ):
        self._provider = provider
        self._registry = registry
        self._feature = feature
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
        # Resolve per call so a /model change applies without a restart.
        provider = (
            self._registry.get_provider(self._feature) if self._registry is not None else self._provider
        )
        if provider is None:
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
            response = await provider.chat(
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

            # Model output is never compiled as a regex: a pattern written by
            # a model reading attacker-influenced logs is the classic ReDoS
            # input. It becomes a literal substring that must occur in the
            # error it was asked about.
            literal = _literal_text(str(result["pattern"]), result.get("match_type") == "regex")
            if len(literal) < _MIN_LITERAL or literal.lower() not in error_message.lower():
                logger.warning("AI ignore pattern not usable as a literal; falling back to the error text")
                return None
            result["pattern"] = literal
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
