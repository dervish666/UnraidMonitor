import json
import logging
import re
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)


@dataclass
class IgnorePattern:
    """Represents an ignore pattern with metadata."""

    pattern: str
    match_type: Literal["substring", "regex"] = "substring"
    explanation: str | None = None
    added: str | None = None  # ISO timestamp

    def matches(self, message: str) -> bool:
        """Check if this pattern matches the given message."""
        if self.match_type == "regex":
            try:
                return bool(re.search(self.pattern, message, re.IGNORECASE))
            except re.error:
                logger.warning(f"Invalid regex pattern: {self.pattern}")
                return False
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
        self._load_runtime_ignores()

    def is_ignored(self, container: str, message: str) -> bool:
        """Check if message should be ignored."""
        # Check config ignores (always substring, case-insensitive)
        message_lower = message.lower()
        for pattern in self._config_ignores.get(container, []):
            if pattern.lower() in message_lower:
                return True

        # Check runtime ignores (can be regex or substring)
        for ignore_pattern in self._runtime_ignores.get(container, []):
            if ignore_pattern.matches(message):
                return True

        return False

    def add_ignore_pattern(
        self,
        container: str,
        pattern: str,
        match_type: Literal["substring", "regex"] = "substring",
        explanation: str | None = None,
    ) -> bool:
        """Add a runtime ignore pattern with optional regex support.

        Args:
            container: Container name to add ignore for.
            pattern: The pattern string (substring or regex).
            match_type: Either "substring" or "regex".
            explanation: Human-readable explanation of what this pattern matches.

        Returns:
            True if added, False if already exists.
        """
        if container not in self._runtime_ignores:
            self._runtime_ignores[container] = []

        # Check if already exists (by pattern string, case-insensitive)
        for existing in self._runtime_ignores[container]:
            if existing.pattern.lower() == pattern.lower():
                return False

        ignore_pattern = IgnorePattern(
            pattern=pattern,
            match_type=match_type,
            explanation=explanation,
            added=datetime.now().isoformat(),
        )
        self._runtime_ignores[container].append(ignore_pattern)
        self._save_runtime_ignores()
        logger.info(f"Added ignore for {container}: {pattern} ({match_type})")
        return True

    def add_ignore(self, container: str, message: str) -> bool:
        """Add a runtime ignore pattern (backward compatible).

        This method maintains backward compatibility by creating a substring pattern.

        Returns:
            True if added, False if already exists.
        """
        return self.add_ignore_pattern(
            container=container,
            pattern=message,
            match_type="substring",
            explanation=None,
        )

    def get_all_ignores(self, container: str) -> list[tuple[str, str, str | None]]:
        """Get all ignores for a container as (pattern, source, explanation) tuples.

        Returns:
            List of tuples containing (pattern, source, explanation).
            source is either "config" or "runtime".
            explanation is None for config ignores.
        """
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
        ignores: list[tuple[int, str, str | None]] = []

        for i, ignore_pattern in enumerate(self._runtime_ignores.get(container, [])):
            ignores.append((i, ignore_pattern.pattern, ignore_pattern.explanation))

        return ignores

    def get_containers_with_runtime_ignores(self) -> list[str]:
        """Get list of containers that have runtime ignores.

        Returns:
            List of container names with at least one runtime ignore.
        """
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

        self._save_runtime_ignores()
        return True

    def _load_runtime_ignores(self) -> None:
        """Load runtime ignores from JSON file.

        Handles both old format (list of strings) and new format (list of objects).
        """
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
        """Save runtime ignores to JSON file in new format."""
        # Ensure parent directory exists
        self._json_path.parent.mkdir(parents=True, exist_ok=True)

        # Convert to serializable format
        data = {}
        for container, patterns in self._runtime_ignores.items():
            data[container] = [asdict(p) for p in patterns]

        try:
            with open(self._json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except IOError as e:
            logger.error(f"Failed to save runtime ignores: {e}")
