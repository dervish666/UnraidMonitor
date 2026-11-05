import logging

from src.utils.json_store import atomic_write_json, load_json

logger = logging.getLogger(__name__)


def read_announced_version(path: str) -> str | None:
    """Return the last-announced bot version, or None if unset/unreadable.

    A corrupt file is set aside by load_json (it used to be deleted).
    """
    data = load_json(path, dict, "announced version")
    if data is None:
        return None
    version = data.get("version")
    return version if isinstance(version, str) else None


def write_announced_version(path: str, version: str) -> None:
    """Persist the announced bot version atomically."""
    try:
        atomic_write_json(path, {"version": version})
    except OSError as e:
        logger.error(f"Failed to write announced version to {path}: {e}")
