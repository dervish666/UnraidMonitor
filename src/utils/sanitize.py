"""Input sanitization utilities for AI prompt injection prevention."""

import re


def sanitize_for_prompt(text: str, max_length: int = 10000) -> str:
    """Sanitize user-controlled text before including in AI prompts.

    This helps mitigate prompt injection attacks where container logs or
    names might contain text designed to manipulate the AI model.

    Args:
        text: The text to sanitize (container name, logs, error messages).
        max_length: Maximum length to allow (truncate if longer).

    Returns:
        Sanitized text safe for inclusion in prompts.
    """
    if not text:
        return ""

    # Truncate to max length
    if len(text) > max_length:
        text = text[:max_length] + "\n... (truncated)"

    # Common prompt injection patterns to neutralize
    # These patterns attempt to break out of data context and inject instructions
    injection_patterns = [
        # Attempts to add new instructions
        (r"(?i)\b(ignore|disregard|forget)\s+(all\s+)?(previous|above|prior)\s+(instructions?|context|prompts?)", "[FILTERED]"),
        # Attempts to impersonate system prompts
        (r"(?i)^(system|assistant|human|user):\s*", "data: "),
        # Attempts to create new roles
        (r"(?i)\[?(system|assistant)\]?\s*:", "[data]:"),
        # XML/markdown injection attempts that might affect prompt parsing
        (r"<\s*/?(?:system|prompt|instruction|context)[^>]*>", "[tag]"),
    ]

    for pattern, replacement in injection_patterns:
        text = re.sub(pattern, replacement, text)

    return text


def sanitize_container_name(name: str) -> str:
    """Sanitize a container name for use in prompts.

    Container names should be alphanumeric with dashes/underscores,
    but malicious names could contain injection attempts.

    Args:
        name: Container name to sanitize.

    Returns:
        Sanitized container name.
    """
    # Container names should be relatively short
    return sanitize_for_prompt(name, max_length=256)


def sanitize_logs(logs: str, max_length: int = 8000) -> str:
    """Sanitize container logs for use in prompts.

    Logs are the main vector for prompt injection since they contain
    arbitrary output that could be crafted by an attacker.

    Args:
        logs: Log text to sanitize.
        max_length: Maximum length to allow.

    Returns:
        Sanitized log text.
    """
    return sanitize_for_prompt(logs, max_length=max_length)
