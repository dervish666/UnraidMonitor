import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


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
        self._runtime_ignores: dict[str, list[str]] = {}
        self._load_runtime_ignores()

    def is_ignored(self, container: str, message: str) -> bool:
        """Check if message should be ignored (substring, case-insensitive)."""
        message_lower = message.lower()

        # Check config ignores
        for pattern in self._config_ignores.get(container, []):
            if pattern.lower() in message_lower:
                return True

        # Check runtime ignores
        for pattern in self._runtime_ignores.get(container, []):
            if pattern.lower() in message_lower:
                return True

        return False

    def add_ignore(self, container: str, message: str) -> bool:
        """Add a runtime ignore pattern.

        Returns:
            True if added, False if already exists.
        """
        if container not in self._runtime_ignores:
            self._runtime_ignores[container] = []

        # Check if already exists (case-insensitive)
        for existing in self._runtime_ignores[container]:
            if existing.lower() == message.lower():
                return False

        self._runtime_ignores[container].append(message)
        self._save_runtime_ignores()
        logger.info(f"Added ignore for {container}: {message}")
        return True

    def get_all_ignores(self, container: str) -> list[tuple[str, str]]:
        """Get all ignores for a container as (message, source) tuples."""
        ignores = []

        for pattern in self._config_ignores.get(container, []):
            ignores.append((pattern, "config"))

        for pattern in self._runtime_ignores.get(container, []):
            ignores.append((pattern, "runtime"))

        return ignores

    def _load_runtime_ignores(self) -> None:
        """Load runtime ignores from JSON file."""
        if not self._json_path.exists():
            self._runtime_ignores = {}
            return

        try:
            with open(self._json_path, encoding="utf-8") as f:
                self._runtime_ignores = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Failed to load runtime ignores: {e}")
            self._runtime_ignores = {}

    def _save_runtime_ignores(self) -> None:
        """Save runtime ignores to JSON file."""
        # Ensure parent directory exists
        self._json_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            with open(self._json_path, "w", encoding="utf-8") as f:
                json.dump(self._runtime_ignores, f, indent=2)
        except IOError as e:
            logger.error(f"Failed to save runtime ignores: {e}")
