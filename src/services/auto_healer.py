import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.config import AutoHealConfig
    from src.services.container_control import ContainerController
    from src.alerts.manager import AlertSender

logger = logging.getLogger(__name__)


class AutoHealer:
    """Auto-restarts opted-in containers reporting HEALTHCHECK 'unhealthy'.

    A per-container, time-windowed storm guard prevents restart loops: after
    ``max_restarts`` within ``window_minutes`` the healer gives up and sends a
    single escalation until the window clears.
    """

    def __init__(self, config: "AutoHealConfig", controller: "ContainerController", alert_manager: "AlertSender") -> None:
        self._config = config
        self._controller = controller
        self._alert_manager = alert_manager
        self._restarts: dict[str, list[datetime]] = {}
        self._gave_up: set[str] = set()

    def is_enabled(self, container_name: str) -> bool:
        if not self._config.enabled:
            return False
        if container_name not in self._config.containers:
            return False
        if self._controller.is_protected(container_name):
            return False
        return True

    def _recent_count(self, container_name: str, now: datetime) -> int:
        cutoff = now - timedelta(minutes=self._config.window_minutes)
        times = [t for t in self._restarts.get(container_name, []) if t > cutoff]
        self._restarts[container_name] = times
        if not times:
            self._gave_up.discard(container_name)
        return len(times)

    async def heal(self, container_name: str) -> None:
        now = datetime.now()
        count = self._recent_count(container_name, now)
        if count >= self._config.max_restarts:
            if container_name not in self._gave_up:
                self._gave_up.add(container_name)
                logger.warning(
                    f"Auto-heal giving up on {container_name} after {count} restarts in {self._config.window_minutes} min"
                )
                await self._alert_manager.send_autoheal_alert(
                    container_name=container_name, attempt=count, max_attempts=self._config.max_restarts, gave_up=True,
                )
            return
        self._restarts.setdefault(container_name, []).append(now)
        attempt = count + 1
        logger.info(f"Auto-restarting unhealthy container {container_name} (attempt {attempt}/{self._config.max_restarts})")
        await self._controller.restart(container_name)
        await self._alert_manager.send_autoheal_alert(
            container_name=container_name, attempt=attempt, max_attempts=self._config.max_restarts, gave_up=False,
        )
