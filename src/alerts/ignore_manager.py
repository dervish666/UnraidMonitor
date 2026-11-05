import hashlib
import logging
import re
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Literal

import re._constants as _sre  # stdlib regex parser, as used by re.compile
import re._parser as _sre_parse

from src.utils.formatting import strip_log_timestamps
from src.utils.json_store import atomic_write_json, load_json, log_rejected

logger = logging.getLogger(__name__)

# Maximum length for regex patterns to prevent complexity attacks
MAX_REGEX_LENGTH = 200

# Backtracking safety is judged on the parsed pattern, not its text: a text
# heuristic missed ((z|zz))+$, which took seconds on 38 characters. A repeat
# that can run more than once is refused when what it repeats can match the
# same text more than one way (an inner repeat or an alternation), except a
# small bounded repeat whose inner choices stay few, the shape of
# (\d{1,3}\.){3}\d{1,3} and (?:\d{2}:){2}\d{2}, which the 0.22 analyzer
# told the model to write.
_REPEATS = (_sre.MAX_REPEAT, _sre.MIN_REPEAT, _sre.POSSESSIVE_REPEAT)
_MAX_BOUNDED_REPEAT = 16
_MAX_BOUNDED_WAYS = 1000


def _children(op: object, av: Any) -> list[Any]:
    """Sub-patterns nested under one parsed node."""
    if op in _REPEATS:
        return [av[2]]
    if op is _sre.SUBPATTERN:
        return [av[3]]
    if op is _sre.BRANCH:
        return list(av[1])
    if op in (_sre.ASSERT, _sre.ASSERT_NOT):
        return [av[1]]
    if op is _sre.ATOMIC_GROUP:
        return [av]
    if op is _sre.GROUPREF_EXISTS:
        return [sub for sub in av[1:] if sub is not None]
    return []


def _shape(items: Any) -> tuple[bool, bool, int]:
    """(has an unbounded repeat, has an alternation, product of repeat choices)."""
    unbounded = branch = False
    ways = 1
    for op, av in items:
        if op in _REPEATS:
            lo, hi = av[0], av[1]
            if hi == _sre.MAXREPEAT:
                unbounded = True
            else:
                ways *= hi - lo + 1
        elif op is _sre.BRANCH:
            branch = True
        for sub in _children(op, av):
            sub_unbounded, sub_branch, sub_ways = _shape(sub)
            unbounded |= sub_unbounded
            branch |= sub_branch
            ways *= sub_ways
    return unbounded, branch, ways


def _backtracking_risk(items: Any) -> str:
    """Why this parsed pattern could backtrack exponentially, or ''."""
    for op, av in items:
        if op in _REPEATS and av[1] > 1:
            unbounded, branch, ways = _shape(av[2])
            if av[1] == _sre.MAXREPEAT:
                if unbounded or branch or ways > 1:
                    return "a repeated group contains another repeat or an alternation"
            elif unbounded or branch:
                return "a repeated group contains an unbounded repeat or an alternation"
            elif ways > 1 and (av[1] > _MAX_BOUNDED_REPEAT or ways ** av[1] > _MAX_BOUNDED_WAYS):
                return "a repeated group has too many ways to match"
        for sub in _children(op, av):
            why = _backtracking_risk(sub)
            if why:
                return why
    return ""


# Inputs that make common backtracking shapes blow up, tried against every
# regex before it is used. The limit of this check: Python's re has no
# timeout, so a pattern that hangs a probe leaves a daemon thread spinning
# until restart. The parsed check above is the guarantee; this is the net.
_ADVERSARIAL_PROBES = [
    "a" * 3000 + "!",
    "0" * 3000 + "x",
    " " * 3000 + "x",
    "ab" * 1500 + "!",
    "a1-_." * 600 + "\x00",
]
_PROBE_SECONDS = 0.5


def validate_regex_pattern(pattern: str) -> tuple[bool, str]:
    """Validate a regex pattern for safety and correctness.

    Args:
        pattern: The regex pattern to validate.

    Returns:
        Tuple of (is_valid, error_message). If valid, error_message is empty.
    """
    if len(pattern) > MAX_REGEX_LENGTH:
        return False, f"Pattern too long (max {MAX_REGEX_LENGTH} chars)"

    try:
        compiled = re.compile(pattern)
        why = _backtracking_risk(_sre_parse.parse(pattern))
    except re.error as e:
        return False, str(e)
    if why:
        return False, f"Pattern may cause performance issues ({why})"

    # Probe with pathological strings in a daemon thread with a timeout
    # (signal.SIGALRM is process-global, main-thread only, and would block the
    # event loop).
    result: list[bool] = []

    def _test_regex() -> None:
        for probe in _ADVERSARIAL_PROBES:
            compiled.search(probe)
        result.append(True)

    t = threading.Thread(target=_test_regex, daemon=True)
    t.start()
    t.join(timeout=_PROBE_SECONDS * len(_ADVERSARIAL_PROBES))

    if not result:
        return False, "Pattern causes excessive backtracking"

    return True, ""


def ignore_key(pattern: str) -> str:
    """Short stable id for an ignore pattern (8 hex chars), used in buttons."""
    return hashlib.sha1(pattern.encode("utf-8")).hexdigest()[:8]


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
        # Entries the loader refused (an unsafe regex, a malformed item), kept
        # raw and written back untouched by every save. Dropping them meant
        # the first save after an upgrade erased the user's patterns.
        self._preserved: dict[str, Any] = {}
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
            message_stripped = strip_log_timestamps(message)
            message_stripped_lower = message_stripped.lower()
            for pattern in self._config_ignores.get(container, []):
                pattern_lower = pattern.lower()
                if pattern_lower in message_lower or pattern_lower in message_stripped_lower:
                    return True

            # Check runtime ignores (can be regex or substring)
            for ignore_pattern in self._runtime_ignores.get(container, []):
                if ignore_pattern.matches(message) or ignore_pattern.matches(message_stripped):
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
        # An empty pattern is a substring of every line and would silently
        # ignore every error for the container.
        if not pattern.strip():
            return False, "Pattern is empty"

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

    def remove_runtime_ignore_by_key(self, container: str, key: str) -> bool:
        """Remove the runtime ignore whose ``ignore_key`` is *key*.

        Buttons carry this key, not a list index: an index shifts when another
        ignore is removed first, so a stale button deleted the wrong pattern.
        """
        with self._lock:
            for i, ignore_pattern in enumerate(self._runtime_ignores.get(container, [])):
                if ignore_key(ignore_pattern.pattern) == key:
                    return self.remove_runtime_ignore(container, i)
            return False

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
        """Load runtime ignores, keeping every well-formed entry.

        Handles both old format (list of strings) and new format (list of
        objects). A file that is not a JSON object is set aside.
        """
        with self._lock:
            self._runtime_ignores = {}
            self._preserved = {}
            data = load_json(self._json_path, dict, "runtime ignores")
            if data is None:
                return
            rejected: list[str] = []
            for container, patterns in data.items():
                if not isinstance(patterns, list):
                    rejected.append(str(container))
                    self._preserved[str(container)] = patterns
                    continue
                kept: list[IgnorePattern] = []
                set_aside: list[Any] = []
                for item in patterns:
                    if isinstance(item, str) and item:
                        # Old format: plain string -> substring pattern
                        kept.append(IgnorePattern(pattern=item, match_type="substring"))
                        continue
                    if not isinstance(item, dict):
                        rejected.append(f"{container}: {item!r}"[:60])
                        set_aside.append(item)
                        continue
                    pattern = item.get("pattern")
                    match_type = item.get("match_type", "substring")
                    if not isinstance(pattern, str) or not pattern or match_type not in ("substring", "regex"):
                        rejected.append(f"{container}: {pattern!r}"[:60])
                        set_aside.append(item)
                        continue
                    # A regex edited into the file by hand gets the same checks
                    # as one added at runtime. A bad one is never compiled, but
                    # stays in the file for the user to fix.
                    if match_type == "regex":
                        ok, why = validate_regex_pattern(pattern)
                        if not ok:
                            logger.error(
                                f"Not using unsafe ignore regex for {container}: {pattern!r} "
                                f"({why}). It stays in {self._json_path}; edit or delete it there."
                            )
                            set_aside.append(item)
                            continue
                    explanation = item.get("explanation")
                    added = item.get("added")
                    kept.append(IgnorePattern(
                        pattern=pattern,
                        match_type=match_type,
                        explanation=explanation if isinstance(explanation, str) else None,
                        added=added if isinstance(added, str) else None,
                    ))
                if kept:
                    self._runtime_ignores[str(container)] = kept
                if set_aside:
                    self._preserved[str(container)] = set_aside
            log_rejected(str(self._json_path), rejected)

    def _save_runtime_ignores(self) -> None:
        """Save runtime ignores atomically."""
        with self._lock:
            # Convert to serializable format (exclude _compiled_regex)
            data: dict[str, Any] = {
                container: [
                    {
                        "pattern": p.pattern,
                        "match_type": p.match_type,
                        "explanation": p.explanation,
                        "added": p.added,
                    }
                    for p in patterns
                ]
                for container, patterns in self._runtime_ignores.items()
            }
            for container, raw in self._preserved.items():
                if isinstance(raw, list):
                    data[container] = data.get(container, []) + raw
                elif container not in data:
                    data[container] = raw
            try:
                atomic_write_json(self._json_path, data, indent=2)
            except OSError as e:
                logger.error(f"Failed to save runtime ignores: {e}")
