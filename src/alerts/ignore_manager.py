import json
import logging
import os
import re
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterator, Literal

from src.utils.formatting import strip_log_timestamps

logger = logging.getLogger(__name__)

# Maximum length for regex patterns to prevent complexity attacks
MAX_REGEX_LENGTH = 200

# Patterns that could cause catastrophic backtracking (ReDoS)
_REDOS_PATTERNS = [
    re.compile(r"\(\.\*\)\+"),  # (.*)+
    re.compile(r"\(\.\+\)\+"),  # (.+)+
    re.compile(r"\(\[.*?\]\+\)\+"),  # ([...]+)+
    re.compile(r"\(\.\*\?\)\+"),  # (.*?)+
    re.compile(r"\(\.\+\?\)\+"),  # (.+?)+
]


def validate_regex_pattern(pattern: str) -> tuple[bool, str]:
    """Validate a regex pattern for safety and correctness.

    Args:
        pattern: The regex pattern to validate.

    Returns:
        Tuple of (is_valid, error_message). If valid, error_message is empty.
    """
    if len(pattern) > MAX_REGEX_LENGTH:
        return False, f"Pattern too long (max {MAX_REGEX_LENGTH} chars)"

    # Check for ReDoS patterns
    for redos in _REDOS_PATTERNS:
        if redos.search(pattern):
            return False, "Pattern may cause performance issues (nested quantifiers)"

    try:
        compiled = re.compile(pattern)
    except re.error as e:
        return False, str(e)

    # Test against a pathological string to catch patterns that cause backtracking.
    # Uses a daemon thread with a timeout instead of signal.SIGALRM, which is
    # process-global, only works on the main thread, and blocks the event loop.
    test_string = "a" * 100
    result: list[bool] = []

    def _test_regex() -> None:
        compiled.search(test_string)
        result.append(True)

    t = threading.Thread(target=_test_regex, daemon=True)
    t.start()
    t.join(timeout=1.0)

    if not result:
        return False, "Pattern causes excessive backtracking"

    return True, ""


@dataclass
class IgnorePattern:
    """Represents an ignore pattern with metadata."""

    pattern: str
    match_type: Literal["substring", "regex"] = "substring"
    explanation: str | None = None
    added: str | None = None  # ISO timestamp
    # Exclude from serialization and comparison
    _compiled_regex: re.Pattern[str] | None = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Pre-compile regex patterns for performance."""
        if self.match_type == "regex":
            try:
                self._compiled_regex = re.compile(self.pattern, re.IGNORECASE)
            except re.error as e:
                logger.warning(f"Invalid regex pattern '{self.pattern}': {e}")
                self._compiled_regex = None

    def matches(self, message: str) -> bool:
        """Check if this pattern matches the given message."""
        if self.match_type == "regex":
            if self._compiled_regex is None:
                return False
            return bool(self._compiled_regex.search(message))
        else:
            # Substring match (case-insensitive)
            return self.pattern.lower() in message.lower()


class IgnoreManager:
    """Manages error ignore patterns from config and runtime JSON."""

    def __init__(self, config_ignores: dict[str, list[str]], json_path: str):
        """Initialize IgnoreManager.

        Args:
            config_ignores: Per-container ignore patterns from config.yaml.
            json_path: Path to runtime ignores JSON file.
        """
        self._config_ignores = config_ignores
        self._json_path = Path(json_path)
        self._runtime_ignores: dict[str, list[IgnorePattern]] = {}
        self._lock = threading.RLock()
        self._defer_save = False
        self._load_runtime_ignores()

    @contextmanager
    def batch_updates(self) -> Iterator[None]:
        """Context manager to defer saves during bulk operations.

        While inside the batch, add/remove calls skip writing to disk.
        A single save is performed when the block exits (even on error).
        """
        with self._lock:
            self._defer_save = True
        try:
            yield
        finally:
            with self._lock:
                self._defer_save = False
                self._save_runtime_ignores()

    def is_ignored(self, container: str, message: str) -> bool:
        """Check if message should be ignored.

        Also checks against a timestamp-stripped version of the message so that
        patterns created without timestamps still match log lines that have them.
        """
        with self._lock:
            # Check config ignores (always substring, case-insensitive)
            message_lower = message.lower()
            message_stripped_lower = strip_log_timestamps(message).lower()
            for pattern in self._config_ignores.get(container, []):
                pattern_lower = pattern.lower()
                if pattern_lower in message_lower or pattern_lower in message_stripped_lower:
                    return True

            # Check runtime ignores (can be regex or substring)
            for ignore_pattern in self._runtime_ignores.get(container, []):
                if ignore_pattern.matches(message) or ignore_pattern.matches(strip_log_timestamps(message)):
                    return True

            return False

    def add_ignore_pattern(
        self,
        container: str,
        pattern: str,
        match_type: Literal["substring", "regex"] = "substring",
        explanation: str | None = None,
    ) -> tuple[bool, str]:
        """Add a runtime ignore pattern with optional regex support.

        Args:
            container: Container name to add ignore for.
            pattern: The pattern string (substring or regex).
            match_type: Either "substring" or "regex".
            explanation: Human-readable explanation of what this pattern matches.

        Returns:
            Tuple of (success, message). If success is False, message explains why.
        """
        # Validate regex patterns for safety (outside lock to avoid holding it during expensive validation)
        if match_type == "regex":
            is_valid, error = validate_regex_pattern(pattern)
            if not is_valid:
                return False, f"Invalid regex: {error}"

        with self._lock:
            if container not in self._runtime_ignores:
                self._runtime_ignores[container] = []

            # Check if already exists (by pattern string, case-insensitive)
            for existing in self._runtime_ignores[container]:
                if existing.pattern.lower() == pattern.lower():
                    return False, "Pattern already exists"

            ignore_pattern = IgnorePattern(
                pattern=pattern,
                match_type=match_type,
                explanation=explanation,
                added=datetime.now().isoformat(),
            )
            self._runtime_ignores[container].append(ignore_pattern)
            if not self._defer_save:
                self._save_runtime_ignores()
            logger.info(f"Added ignore for {container}: {pattern} ({match_type})")
            return True, "Pattern added"

    def add_ignore(self, container: str, message: str) -> bool:
        """Add a runtime ignore pattern (backward compatible).

        This method maintains backward compatibility by creating a substring pattern.

        Returns:
            True if added, False if already exists.
        """
        success, _ = self.add_ignore_pattern(
            container=container,
            pattern=message,
            match_type="substring",
            explanation=None,
        )
        return success

    def get_all_ignores(self, container: str) -> list[tuple[str, str, str | None]]:
        """Get all ignores for a container as (pattern, source, explanation) tuples.

        Returns:
            List of tuples containing (pattern, source, explanation).
            source is either "config" or "runtime".
            explanation is None for config ignores.
        """
        with self._lock:
            ignores: list[tuple[str, str, str | None]] = []

            for pattern in self._config_ignores.get(container, []):
                ignores.append((pattern, "config", None))

            for ignore_pattern in self._runtime_ignores.get(container, []):
                ignores.append((ignore_pattern.pattern, "runtime", ignore_pattern.explanation))

            return ignores

    def get_runtime_ignores(self, container: str) -> list[tuple[int, str, str | None]]:
        """Get runtime ignores for a container as (index, pattern, explanation) tuples.

        Returns:
            List of tuples containing (index, pattern, explanation).
            Index is the position in the runtime ignores list.
        """
        with self._lock:
            ignores: list[tuple[int, str, str | None]] = []

            for i, ignore_pattern in enumerate(self._runtime_ignores.get(container, [])):
                ignores.append((i, ignore_pattern.pattern, ignore_pattern.explanation))

            return ignores

    def get_containers_with_runtime_ignores(self) -> list[str]:
        """Get list of containers that have runtime ignores.

        Returns:
            List of container names with at least one runtime ignore.
        """
        with self._lock:
            return [
                container
                for container, patterns in self._runtime_ignores.items()
                if patterns
            ]

    def remove_runtime_ignore(self, container: str, index: int) -> bool:
        """Remove a runtime ignore by index.

        Args:
            container: Container name.
            index: Index of the ignore to remove.

        Returns:
            True if removed, False if not found.
        """
        with self._lock:
            if container not in self._runtime_ignores:
                return False

            patterns = self._runtime_ignores[container]
            if index < 0 or index >= len(patterns):
                return False

            removed = patterns.pop(index)
            logger.info(f"Removed ignore for {container}: {removed.pattern}")

            # Clean up empty container entries
            if not patterns:
                del self._runtime_ignores[container]

            if not self._defer_save:
                self._save_runtime_ignores()
            return True

    def _load_runtime_ignores(self) -> None:
        """Load runtime ignores from JSON file.

        Handles both old format (list of strings) and new format (list of objects).
        """
        with self._lock:
            if not self._json_path.exists():
                self._runtime_ignores = {}
                return

            try:
                with open(self._json_path, encoding="utf-8") as f:
                    data = json.load(f)

                self._runtime_ignores = {}
                for container, patterns in data.items():
                    self._runtime_ignores[container] = []
                    for item in patterns:
                        if isinstance(item, str):
                            # Old format: plain string -> substring pattern
                            self._runtime_ignores[container].append(
                                IgnorePattern(pattern=item, match_type="substring")
                            )
                        elif isinstance(item, dict):
                            # New format: IgnorePattern object
                            self._runtime_ignores[container].append(
                                IgnorePattern(
                                    pattern=item.get("pattern", ""),
                                    match_type=item.get("match_type", "substring"),
                                    explanation=item.get("explanation"),
                                    added=item.get("added"),
                                )
                            )
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"Failed to load runtime ignores: {e}")
                self._runtime_ignores = {}

    def _save_runtime_ignores(self) -> None:
        """Save runtime ignores to JSON file using atomic write pattern."""
        with self._lock:
            # Ensure parent directory exists
            self._json_path.parent.mkdir(parents=True, exist_ok=True)

            # Convert to serializable format (exclude _compiled_regex)
            data = {}
            for container, patterns in self._runtime_ignores.items():
                data[container] = [
                    {
                        "pattern": p.pattern,
                        "match_type": p.match_type,
                        "explanation": p.explanation,
                        "added": p.added,
                    }
                    for p in patterns
                ]

            try:
                # Atomic write: write to temp file, then rename
                fd, temp_path = tempfile.mkstemp(
                    dir=self._json_path.parent,
                    prefix=".tmp_ignores_",
                    suffix=".json",
                )
                try:
                    os.fchmod(fd, 0o666)
                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=2)
                    os.replace(temp_path, self._json_path)  # Atomic on POSIX
                except Exception:
                    # Clean up temp file on error
                    try:
                        os.unlink(temp_path)
                    except OSError:
                        pass
                    raise
            except IOError as e:
                logger.error(f"Failed to save runtime ignores: {e}")
