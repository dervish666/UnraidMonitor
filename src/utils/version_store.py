import json
import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


def read_announced_version(path: str) -> str | None:
    """Return the last-announced bot version, or None if unset/unreadable."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        version = data.get("version")
        return version if isinstance(version, str) else None
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, OSError, AttributeError) as e:
        logger.warning(f"Failed to read announced version from {path}: {e}")
        return None


def write_announced_version(path: str, version: str) -> None:
    """Persist the announced bot version atomically."""
    try:
        parent = Path(path).parent
        parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(parent), prefix=".tmp_version_", suffix=".json")
        try:
            os.fchmod(fd, 0o644)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"version": version}, f)
            os.replace(tmp, path)
        except Exception:
            os.unlink(tmp)
            raise
    except Exception as e:
        logger.error(f"Failed to write announced version to {path}: {e}")
