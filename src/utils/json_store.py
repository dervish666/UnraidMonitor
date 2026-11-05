"""Crash-safe JSON files for data/*.json.

Every state file the bot keeps goes through here. A write lands whole or not
at all (temp file, fsync, rename, directory fsync), and a file that cannot be
read is moved aside as ``<name>.corrupt-<timestamp>`` rather than overwritten
by the next save, so whatever was in it can still be recovered by hand. The
startup message lists files set aside this run.
"""

import json
import logging
import os
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_quarantined: list[str] = []
_quarantine_lock = threading.Lock()


def atomic_write_json(path: str | Path, data: Any, indent: int | None = None) -> None:
    """Write *data* as JSON so a crash or full disk never leaves a half file.

    Raises:
        OSError / TypeError: the write failed; the previous file is untouched.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=f".tmp_{target.stem}_", suffix=".json")
    try:
        os.fchmod(fd, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    _fsync_dir(target.parent)


def _fsync_dir(directory: Path) -> None:
    """Make the rename itself durable. Not every filesystem allows it."""
    try:
        dir_fd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    except OSError as e:
        logger.debug(f"Directory fsync not supported for {directory}: {e}")
    finally:
        os.close(dir_fd)


def quarantine(path: str | Path, reason: str) -> str | None:
    """Move an unreadable file to ``<path>.corrupt-<ts>`` and remember it.

    Returns the new path, or None if the move itself failed (then the bot runs
    on defaults but the next save will replace the file; the log says so).
    """
    source = Path(path)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    dest = source.with_name(f"{source.name}.corrupt-{stamp}")
    try:
        os.replace(source, dest)
    except OSError as e:
        logger.error(f"{source} is unreadable ({reason}) and could not be set aside: {e}")
        return None
    logger.error(f"{source} is unreadable ({reason}); moved to {dest.name}, starting from defaults")
    with _quarantine_lock:
        _quarantined.append(f"{source.name} → {dest.name}")
    return str(dest)


def quarantined_files() -> list[str]:
    """Files set aside since startup, as 'name → name.corrupt-<ts>'."""
    with _quarantine_lock:
        return list(_quarantined)


def load_json(path: str | Path, expect: type, label: str = "") -> Any | None:
    """Read a JSON file whose root must be *expect* (dict or list).

    Returns None when the file does not exist or is empty. A file that cannot be parsed,
    or whose root is the wrong type, is quarantined and None is returned.
    An OSError reading it (permissions) is logged and returns None without
    moving anything, since the content may be fine.
    """
    source = Path(path)
    if not source.exists():
        return None
    try:
        if source.stat().st_size == 0:
            # Nothing in it to recover (a `touch`, or a pre-atomic crash).
            logger.info(f"{label or source} is empty; starting from defaults")
            return None
        with open(source, encoding="utf-8") as f:
            data = json.load(f)
    except OSError as e:
        logger.error(f"Cannot read {label or source}: {e}")
        return None
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        quarantine(source, f"not valid JSON: {e}")
        return None
    if not isinstance(data, expect):
        quarantine(source, f"expected a JSON {expect.__name__}, found {type(data).__name__}")
        return None
    return data


def log_rejected(label: str, rejected: list[str]) -> None:
    """One warning naming the entries a loader dropped as malformed."""
    if rejected:
        shown = ", ".join(rejected[:5]) + ("…" if len(rejected) > 5 else "")
        logger.warning(f"{label}: ignored {len(rejected)} malformed entr{'y' if len(rejected) == 1 else 'ies'} ({shown})")
