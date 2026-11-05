import asyncio
import logging
from typing import TYPE_CHECKING
from src.utils.json_store import atomic_write_json, load_json, log_rejected

if TYPE_CHECKING:
    from docker.models.containers import Container
    from src.config import ImageUpdatesConfig
    from src.alerts.manager import AlertSender
    from src.services.docker_client import SharedDockerClient

logger = logging.getLogger(__name__)


def extract_local_digests(container: "Container") -> list[str]:
    """Return the sha256 digests recorded in a container image's RepoDigests."""
    image = getattr(container, "image", None)
    repo_digests = image.attrs.get("RepoDigests", []) if image is not None else []
    digests: list[str] = []
    for rd in repo_digests:
        if "@" in rd:
            digests.append(rd.split("@", 1)[1])
    return digests


def _load_notified(path: str) -> dict[str, str]:
    """Load the announced-updates dedup map; a missing file yields {}.

    A file that is not a JSON object is set aside, not overwritten.
    """
    data = load_json(path, dict, "announced updates")
    if data is None:
        return {}
    kept = {str(k): v for k, v in data.items() if isinstance(v, str)}
    log_rejected(path, [str(k) for k in data if str(k) not in kept])
    return kept

def _save_notified(path: str, notified: dict[str, str]) -> None:
    """Persist the dedup map atomically; failures are logged, never raised."""
    try:
        atomic_write_json(path, notified)
    except (OSError, TypeError) as e:
        logger.error(f"Could not persist announced updates to {path}: {e}")



class ImageUpdateMonitor:
    """Polls registries for newer images of running containers and notifies.

    Notify-only: never pulls. A batched digest is sent per cycle, deduped by
    remote digest so the same available update isn't re-announced every cycle.
    The dedup map persists to *state_path* so restart-to-apply flows (wizard,
    /manage feature toggles) don't re-announce updates the user already saw.
    """

    _STOP_TICK_SECONDS = 1

    def __init__(
        self,
        docker_client: "SharedDockerClient",
        config: "ImageUpdatesConfig",
        alert_manager: "AlertSender",
        ignored_containers: list[str] | None = None,
        state_path: str | None = None,
    ) -> None:
        self._docker = docker_client
        self._config = config
        self._alert_manager = alert_manager
        self._ignored = set(ignored_containers or [])
        self._running = False
        self._state_path = state_path
        self._notified: dict[str, str] = _load_notified(state_path) if state_path else {}

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        self._running = True
        interval = self._config.poll_interval_hours * 3600
        logger.info(f"Starting image-update monitor (every {self._config.poll_interval_hours}h)")
        while self._running:
            try:
                await self.check_once()
            except Exception as e:
                logger.error(f"Image-update check failed: {e}")
            slept = 0
            while self._running and slept < interval:
                await asyncio.sleep(min(self._STOP_TICK_SECONDS, interval - slept))
                slept += self._STOP_TICK_SECONDS

    def stop(self) -> None:
        self._running = False

    async def check_once(self) -> None:
        found = await asyncio.to_thread(self._collect_updates)
        if found:
            sent = await self._alert_manager.send_update_alert(
                [(name, image_ref) for name, image_ref, _ in found]
            )
            if sent:
                # Only now: recording before the send lost the update for good
                # whenever Telegram was down at poll time.
                for name, _, digest in found:
                    self._notified[name] = digest
            else:
                logger.warning(f"Image-update digest not delivered; will retry {len(found)} next cycle")
        if self._state_path:
            await asyncio.to_thread(_save_notified, self._state_path, dict(self._notified))

    def _collect_updates(self) -> list[tuple[str, str, str]]:
        """(name, image_ref, remote_digest) for each not-yet-announced update."""
        updates: list[tuple[str, str, str]] = []
        failures: list[str] = []
        try:
            containers = self._docker.containers.list()
        except Exception as e:
            logger.error(f"Failed to list containers for image check: {e}")
            return updates
        for container in containers:
            name = getattr(container, "name", "")
            if not name or name in self._ignored:
                continue
            try:
                image = getattr(container, "image", None)
                tags = image.tags if image is not None else []
                image_ref = tags[0] if tags else None
                if not image_ref:
                    continue
                local_digests = extract_local_digests(container)
                if not local_digests:
                    continue
                remote = self._docker.images.get_registry_data(image_ref)
                remote_digest = remote.id
                if remote_digest in local_digests:
                    continue
                if self._notified.get(name) == remote_digest:
                    continue
                updates.append((name, image_ref, remote_digest))
            except Exception as e:
                failures.append(name)
                logger.debug(f"Image-update check skipped for {name}: {e}")
                continue
        if failures:
            # A registry outage or rate limit makes every check "find nothing";
            # say so, or a quiet digest reads as "all up to date".
            logger.warning(
                f"Image-update check could not reach the registry for {len(failures)} "
                f"of {len(containers)} container(s): {', '.join(sorted(failures)[:10])}"
            )
        # Drop dedup entries for containers that no longer exist (only when the
        # container list itself was fetched successfully).
        current = {getattr(c, "name", "") for c in containers}
        for stale in [n for n in self._notified if n not in current]:
            del self._notified[stale]
        return updates
