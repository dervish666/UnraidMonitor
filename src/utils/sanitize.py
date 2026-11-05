"""Input sanitization utilities for AI prompt injection prevention."""

import re
import unicodedata


# Pre-compile patterns for performance
_INJECTION_PATTERNS = [
    # Attempts to add new instructions
    (re.compile(r"(?i)\b(ignore|disregard|forget)\s+(all\s+)?(previous|above|prior)\s+(instructions?|context|prompts?)"), "[FILTERED]"),
    # Attempts to impersonate system prompts
    (re.compile(r"(?i)^(system|assistant|human|user):\s*", re.MULTILINE), "data: "),
    # Attempts to create new roles
    (re.compile(r"(?i)\[?(system|assistant)\]?\s*:"), "[data]:"),
    # XML/markdown injection attempts that might affect prompt parsing
    (re.compile(r"<\s*/?(?:system|prompt|instruction|context|tool_result|function_result|result)[^>]*>", re.IGNORECASE), "[tag]"),
    # Block attempts to simulate tool/function responses
    (re.compile(r"(?i)\b(tool_use|tool_result|function_call|function_result)\b"), "[FILTERED]"),
    # Block attempts to reference AI systems
    (re.compile(r"(?i)\b(anthropic|claude|openai|chatgpt|gpt-4|gpt-3)\b"), "[FILTERED]"),
    # Block "act as" / "pretend to be" instructions
    (re.compile(r"(?i)\b(act\s+as|pretend\s+(to\s+be|you\s+are)|you\s+are\s+now|roleplay\s+as)\b"), "[FILTERED]"),
    # Block jailbreak-style instructions
    (re.compile(r"(?i)\b(jailbreak|dan\s+mode|developer\s+mode|unrestricted\s+mode)\b"), "[FILTERED]"),
    # HTML comments that could hide text
    (re.compile(r"<!--.*?-->", re.DOTALL), ""),
]

# Zero-width and invisible characters used to evade pattern detection
_ZERO_WIDTH = re.compile(r"[\u200b\u200c\u200d\u00ad\ufeff\u2060\u180e]")

# Maximum length per line to prevent single-line flooding
_MAX_LINE_LENGTH = 1000


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

    # Strip zero-width characters that could bypass pattern detection
    text = _ZERO_WIDTH.sub("", text)
    # Normalize Unicode (fullwidth → ASCII, etc.)
    text = unicodedata.normalize("NFKC", text)

    # Credentials never leave the server, whether in a prompt or a reply.
    # Before truncation, so a cut cannot leave a partial secret unmatched.
    text = redact_secrets(text)

    # Truncate individual lines to prevent single-line flooding
    lines = text.split("\n")
    truncated_lines = []
    for line in lines:
        if len(line) > _MAX_LINE_LENGTH:
            truncated_lines.append(line[:_MAX_LINE_LENGTH] + "...")
        else:
            truncated_lines.append(line)
    text = "\n".join(truncated_lines)

    # Truncate total length
    if len(text) > max_length:
        text = text[:max_length] + "\n... (truncated)"

    # Apply injection pattern filters
    for pattern, replacement in _INJECTION_PATTERNS:
        text = pattern.sub(replacement, text)

    return text


# Pre-compile patterns for sensitive data redaction
_SENSITIVE_DATA_PATTERNS = [
    # Chat/webhook URLs carry their secret in the path
    (re.compile(r"(?i)https?://(?:hooks\.slack\.com/services|(?:\w+\.)?discord(?:app)?\.com/api/webhooks)/\S+"), "***WEBHOOK_REDACTED***"),
    # Telegram Bot API URLs: /bot<id>:<token>/ has no word boundary before the id
    (re.compile(r"/bot\d{6,}:[A-Za-z0-9_-]{20,}"), "/bot***BOT_TOKEN_REDACTED***"),
    # Telegram bot tokens (123456789:AA...)
    (re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b"), "***BOT_TOKEN_REDACTED***"),
    # Provider API keys with a recognisable prefix
    (re.compile(r"\b(?:sk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{30,}|xox[abprs]-[A-Za-z0-9-]{10,})"), "***API_KEY_REDACTED***"),
    # AWS-style keys
    (re.compile(r"(?i)(AKIA|ABIA|ACCA|ASIA)[A-Z0-9]{16}"), "***AWS_KEY_REDACTED***"),
    # key=value / key: value credentials, including base64 values
    (re.compile(r"(?i)\b([\w.-]*(?:api[_-]?key|access[_-]?key|token|secret|pass(?:word|wd|phrase)?|pwd|credentials?|dsn|webhook[_-]?url|auth[_-]?key)[\w.-]*)\s*[=:]\s*[\"']?[\w\-.+/=~:@]{6,}[\"']?"), r"\1=***REDACTED***"),
    # A key in URL userinfo with no password (Sentry DSN style: https://KEY@host)
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s:@]{6,}@"), r"\1***@"),
    # Bearer tokens
    (re.compile(r"(?i)Bearer\s+[\w\-\.+/=]+"), "Bearer ***REDACTED***"),
    # Basic auth in URLs
    (re.compile(r"://[^:]+:[^@]+@"), "://***:***@"),
    # Connection strings (database URLs)
    (re.compile(r"(?i)(mysql|postgres|postgresql|mongodb|redis|amqp)://[^\s]+"), r"\1://***REDACTED***"),
    # Generic hex tokens (32+ chars, likely secrets)
    (re.compile(r"\b[a-fA-F0-9]{32,}\b"), "***HEX_REDACTED***"),
    # JWT tokens (three base64 segments separated by dots)
    (re.compile(r"eyJ[a-zA-Z0-9_-]*\.eyJ[a-zA-Z0-9_-]*\.[a-zA-Z0-9_-]*"), "***JWT_REDACTED***"),
]


def redact_secrets(text: str) -> str:
    """Replace credentials (keys, tokens, passwords, DSNs, webhook URLs) in text."""
    if not text:
        return ""
    for pattern, replacement in _SENSITIVE_DATA_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def sanitize_logs_for_display(logs: str) -> str:
    """Remove potentially sensitive data from logs before display.

    This helps prevent accidental exposure of secrets, tokens, and credentials
    that may appear in container logs.

    Args:
        logs: Raw log text.

    Returns:
        Logs with sensitive data redacted.
    """
    return redact_secrets(logs)


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
