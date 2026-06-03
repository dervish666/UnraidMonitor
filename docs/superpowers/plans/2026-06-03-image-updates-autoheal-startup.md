# Image-update detection, Auto-heal & Richer startup — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add opt-in image-update detection (notify-only, batched daily digest), opt-in auto-heal (auto-restart unhealthy containers with a storm guard), and a richer version-gated startup message — all for v0.12.0.

**Architecture:** Two new components mirror existing patterns — `ImageUpdateMonitor` (poll loop like `ResourceMonitor`) and `AutoHealer` (time-windowed tracker like `CrashTracker`). Delivery goes through the existing `AlertSender` protocol -> `AlertManager` -> `AlertManagerProxy`. Auto-heal hooks the already-present `unhealthy` event handling in `DockerEventMonitor` via a late-bound setter. The startup message reuses an extracted status helper and a curated `WHATS_NEW` map gated by a persisted last-announced version.

**Tech Stack:** Python 3.11, docker-py, aiogram, pytest/pytest-asyncio (asyncio_mode=auto), ruff, mypy --strict.

**Test conventions (this repo):** No `conftest.py`. `MagicMock` for sync, `AsyncMock` for async methods. Construct dataclasses/objects inline. Call a factory's returned handler directly. Run a single test with `uv run pytest tests/test_x.py::test_y -v`.

Full design + decisions: `docs/superpowers/specs/2026-06-03-image-updates-autoheal-startup-design.md`.

---

## File Structure

**New files**
- `src/services/auto_healer.py` — `AutoHealer` (opt-in check + storm-guarded restart).
- `src/monitors/image_update_monitor.py` — `ImageUpdateMonitor` (poll loop + digest compare + dedup).
- `src/utils/version_store.py` — read/write `data/announced_version.json` (atomic).
- `tests/test_auto_healer.py`, `tests/test_image_update_monitor.py`, `tests/test_version_store.py`, `tests/test_startup_notification.py`.

**Modified files**
- `src/constants.py` — new defaults + `WHATS_NEW`.
- `src/config.py` — `ImageUpdatesConfig`, `AutoHealConfig`, `AppConfig` properties.
- `src/alerts/manager.py` — `send_update_alert`, `send_autoheal_alert` (+ `AlertSender` protocol).
- `src/alert_proxy.py` — proxy delegation for the two new methods.
- `src/bot/alert_callbacks.py` — `pull_callback` (digest button -> pull confirmation).
- `src/bot/telegram_bot.py` — register `pull:` callback.
- `src/monitors/docker_events.py` — `set_auto_healer` + branch in `_handle_health_event`.
- `src/bot/health_command.py` — extract `build_status_lines`, add new-monitor rows.
- `src/startup.py` — build/start `ImageUpdateMonitor`, build/inject `AutoHealer`, enrich startup notification.
- `src/background.py` — track + stop `image_update_monitor`.
- `README.md`, `CLAUDE.md`, `CHANGELOG.md`, `pyproject.toml` (version bump).

---

## PHASE A — Config & constants

### Task 1: Constants + WHATS_NEW
**Files:** Modify `src/constants.py`

- [ ] Step 1: Append to `src/constants.py`:
```python
# Image-update detection defaults
IMAGE_UPDATE_POLL_INTERVAL_HOURS = 24
IMAGE_UPDATE_MAX_SHOWN = 10  # cap Pull buttons per digest message

# Auto-heal defaults
AUTOHEAL_MAX_RESTARTS = 3
AUTOHEAL_WINDOW_MINUTES = 60

# Startup "What's new" - curated user-facing one-liners per version.
# Shown once when BOT_VERSION first differs from data/announced_version.json.
ANNOUNCED_VERSION_PATH = "data/announced_version.json"
WHATS_NEW: dict[str, list[str]] = {
    "0.12.0": [
        "Image-update detection - notified when a newer image is available (opt-in: image_updates.enabled)",
        "Auto-heal - auto-restart unhealthy containers (opt-in: auto_heal.containers)",
        "Tests now run in CI on every change",
    ],
}
```
- [ ] Step 2: `uv run python -c "from src.constants import WHATS_NEW, AUTOHEAL_MAX_RESTARTS, IMAGE_UPDATE_POLL_INTERVAL_HOURS, ANNOUNCED_VERSION_PATH; print('ok')"` -> `ok`
- [ ] Step 3: `git add src/constants.py && git commit -m "feat: add image-update, auto-heal, whats-new constants"`

---

### Task 2: Config dataclasses + AppConfig properties
**Files:** Modify `src/config.py`; Test `tests/test_config_extended.py`

- [ ] Step 1: Append failing tests to `tests/test_config_extended.py`:
```python
def test_image_updates_config_defaults():
    from src.config import ImageUpdatesConfig
    c = ImageUpdatesConfig.from_dict({})
    assert c.enabled is False
    assert c.poll_interval_hours == 24

def test_image_updates_config_clamps_interval():
    from src.config import ImageUpdatesConfig
    c = ImageUpdatesConfig.from_dict({"enabled": True, "poll_interval_hours": 0})
    assert c.enabled is True
    assert c.poll_interval_hours == 1

def test_auto_heal_config_defaults():
    from src.config import AutoHealConfig
    c = AutoHealConfig.from_dict({})
    assert c.enabled is True
    assert c.containers == []
    assert c.max_restarts == 3
    assert c.window_minutes == 60

def test_auto_heal_config_parses_list():
    from src.config import AutoHealConfig
    c = AutoHealConfig.from_dict({"containers": ["radarr", "sonarr"], "max_restarts": 5})
    assert c.containers == ["radarr", "sonarr"]
    assert c.max_restarts == 5
```
- [ ] Step 2: `uv run pytest tests/test_config_extended.py -k "image_updates_config or auto_heal_config" -v` -> FAIL (ImportError)
- [ ] Step 3: Add to `src/config.py` (near `ResourceConfig`/`MemoryConfig`); add the three new constants to the existing `from src.constants import ...` line:
```python
@dataclass
class ImageUpdatesConfig:
    """Configuration for proactive image-update detection."""

    enabled: bool = False
    poll_interval_hours: int = IMAGE_UPDATE_POLL_INTERVAL_HOURS

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ImageUpdatesConfig":
        return cls(
            enabled=data.get("enabled", False),
            poll_interval_hours=max(data.get("poll_interval_hours", IMAGE_UPDATE_POLL_INTERVAL_HOURS), 1),
        )


@dataclass
class AutoHealConfig:
    """Configuration for auto-restarting unhealthy containers."""

    enabled: bool = True
    containers: list[str] = field(default_factory=list)
    max_restarts: int = AUTOHEAL_MAX_RESTARTS
    window_minutes: int = AUTOHEAL_WINDOW_MINUTES

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AutoHealConfig":
        return cls(
            enabled=data.get("enabled", True),
            containers=data.get("containers", []) or [],
            max_restarts=max(data.get("max_restarts", AUTOHEAL_MAX_RESTARTS), 1),
            window_minutes=max(data.get("window_minutes", AUTOHEAL_WINDOW_MINUTES), 1),
        )
```
- [ ] Step 4: In `AppConfig.__init__` (alongside `self._resource_monitoring = ...`):
```python
        self._image_updates = ImageUpdatesConfig.from_dict(self._yaml_config.get("image_updates", {}))
        self._auto_heal = AutoHealConfig.from_dict(self._yaml_config.get("auto_heal", {}))
```
Add properties:
```python
    @property
    def image_updates(self) -> ImageUpdatesConfig:
        return self._image_updates

    @property
    def auto_heal(self) -> AutoHealConfig:
        return self._auto_heal
```
- [ ] Step 5: `uv run pytest tests/test_config_extended.py -k "image_updates_config or auto_heal_config" -v` -> 4 passed
- [ ] Step 6: `git add src/config.py tests/test_config_extended.py && git commit -m "feat: add image_updates and auto_heal config blocks"`

---

## PHASE B — Auto-heal (Feature 2)

### Task 3: AutoHealer service
**Files:** Create `src/services/auto_healer.py`; Test `tests/test_auto_healer.py`

- [ ] Step 1: `tests/test_auto_healer.py`:
```python
from unittest.mock import AsyncMock, MagicMock

from src.config import AutoHealConfig
from src.services.auto_healer import AutoHealer


def _make(controller=None, config=None):
    controller = controller or MagicMock()
    controller.is_protected.return_value = False
    controller.restart = AsyncMock(return_value="ok")
    config = config or AutoHealConfig(enabled=True, containers=["radarr"], max_restarts=3, window_minutes=60)
    alert = MagicMock()
    alert.send_autoheal_alert = AsyncMock()
    return AutoHealer(config=config, controller=controller, alert_manager=alert), controller, alert


async def test_heals_opted_in_container():
    healer, controller, alert = _make()
    assert healer.is_enabled("radarr") is True
    await healer.heal("radarr")
    controller.restart.assert_awaited_once_with("radarr")
    alert.send_autoheal_alert.assert_awaited_once()
    assert alert.send_autoheal_alert.call_args.kwargs["gave_up"] is False


def test_not_enabled_for_unlisted_container():
    healer, _, _ = _make()
    assert healer.is_enabled("plex") is False


def test_not_enabled_for_protected_container():
    healer, controller, _ = _make()
    controller.is_protected.return_value = True
    assert healer.is_enabled("radarr") is False


def test_disabled_globally():
    healer, _, _ = _make(config=AutoHealConfig(enabled=False, containers=["radarr"], max_restarts=3, window_minutes=60))
    assert healer.is_enabled("radarr") is False


async def test_storm_guard_gives_up_after_max():
    healer, controller, alert = _make()
    for _ in range(3):
        await healer.heal("radarr")
    assert controller.restart.await_count == 3
    await healer.heal("radarr")
    assert controller.restart.await_count == 3
    assert alert.send_autoheal_alert.call_args.kwargs["gave_up"] is True


async def test_gives_up_alert_sent_once():
    healer, controller, alert = _make()
    for _ in range(5):
        await healer.heal("radarr")
    gave_up_calls = [c for c in alert.send_autoheal_alert.call_args_list if c.kwargs["gave_up"]]
    assert len(gave_up_calls) == 1
```
- [ ] Step 2: `uv run pytest tests/test_auto_healer.py -v` -> FAIL (ModuleNotFoundError)
- [ ] Step 3: `src/services/auto_healer.py`:
```python
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
```
- [ ] Step 4: `uv run pytest tests/test_auto_healer.py -v` -> 6 passed
- [ ] Step 5: `git add src/services/auto_healer.py tests/test_auto_healer.py && git commit -m "feat: add AutoHealer with storm guard"`

---

### Task 4: send_autoheal_alert (manager + protocol + proxy)
**Files:** Modify `src/alerts/manager.py`, `src/alert_proxy.py`; Test `tests/test_alert_manager.py`

- [ ] Step 1: Append to `tests/test_alert_manager.py` (ensure `from unittest.mock import AsyncMock, MagicMock`):
```python
async def test_send_autoheal_alert_restarted():
    from src.alerts.manager import AlertManager
    bot = MagicMock(); bot.send_message = AsyncMock()
    mgr = AlertManager(bot=bot, chat_id=123)
    await mgr.send_autoheal_alert(container_name="radarr", attempt=1, max_attempts=3, gave_up=False)
    bot.send_message.assert_awaited_once()
    text = bot.send_message.call_args.kwargs["text"]
    assert "Auto-restarted" in text and "radarr" in text


async def test_send_autoheal_alert_gave_up():
    from src.alerts.manager import AlertManager
    bot = MagicMock(); bot.send_message = AsyncMock()
    mgr = AlertManager(bot=bot, chat_id=123)
    await mgr.send_autoheal_alert(container_name="radarr", attempt=3, max_attempts=3, gave_up=True)
    text = bot.send_message.call_args.kwargs["text"]
    assert "gave up" in text.lower()
```
- [ ] Step 2: `uv run pytest tests/test_alert_manager.py -k autoheal -v` -> FAIL (AttributeError)
- [ ] Step 3: Add to `AlertSender` Protocol in `src/alerts/manager.py` after `send_recovery_alert`:
```python
    async def send_update_alert(self, updates: list[tuple[str, str]]) -> None: ...

    async def send_autoheal_alert(self, container_name: str, attempt: int, max_attempts: int, gave_up: bool) -> None: ...
```
- [ ] Step 4: Implement on `AlertManager` after `send_health_alert`:
```python
    async def send_autoheal_alert(self, container_name: str, attempt: int, max_attempts: int, gave_up: bool) -> None:
        """Notify that an unhealthy container was auto-restarted (or that we gave up)."""
        safe_name = escape_markdown(container_name)
        if gave_up:
            text = (f"⚠️ *Auto-heal gave up:* {safe_name}\n\n"
                    f"Restarted {attempt} times but it's still unhealthy. Manual attention needed.")
        else:
            text = (f"🔧 *Auto-restarted:* {safe_name}\n\n"
                    f"Container was unhealthy - auto-restarted (attempt {attempt}/{max_attempts}).")
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="📋 Logs", callback_data=truncate_callback_data("logs:", f"{container_name}:50")),
            InlineKeyboardButton(text="🔍 Diagnose", callback_data=truncate_callback_data("diagnose:", container_name)),
        ]])
        try:
            await send_with_retry(self.bot.send_message, chat_id=self.chat_id, text=text,
                                  parse_mode="Markdown", reply_markup=keyboard)
            logger.info(f"Sent autoheal alert for {container_name} (gave_up={gave_up})")
        except Exception as e:
            logger.error(f"Failed to send autoheal alert: {e}")
```
- [ ] Step 5: Add to `src/alert_proxy.py` after the `send_health_alert` delegate:
```python
    async def send_autoheal_alert(self, container_name: str, attempt: int, max_attempts: int, gave_up: bool) -> None:
        await self._send_alert("send_autoheal_alert", container_name=container_name,
                               attempt=attempt, max_attempts=max_attempts, gave_up=gave_up)
```
- [ ] Step 6: `uv run pytest tests/test_alert_manager.py -k autoheal -v` -> 2 passed
- [ ] Step 7: `git add src/alerts/manager.py src/alert_proxy.py tests/test_alert_manager.py && git commit -m "feat: add send_autoheal_alert to manager, protocol, proxy"`

---

### Task 5: Wire AutoHealer into DockerEventMonitor
**Files:** Modify `src/monitors/docker_events.py`; Test `tests/test_docker_events.py`

- [ ] Step 1: Append to `tests/test_docker_events.py` (ensure `AsyncMock` imported):
```python
async def test_unhealthy_opted_in_calls_healer_and_skips_alert():
    from src.monitors.docker_events import DockerEventMonitor
    from src.state import ContainerStateManager
    alert = MagicMock(); alert.send_health_alert = AsyncMock()
    mon = DockerEventMonitor(state_manager=ContainerStateManager(), alert_manager=alert)
    healer = MagicMock(); healer.is_enabled.return_value = True; healer.heal = AsyncMock()
    mon.set_auto_healer(healer)
    await mon._handle_health_event({"Actor": {"Attributes": {"name": "radarr"}}, "_alert_type": "health"})
    healer.heal.assert_awaited_once_with("radarr")
    alert.send_health_alert.assert_not_awaited()


async def test_unhealthy_not_opted_in_sends_alert():
    from src.monitors.docker_events import DockerEventMonitor
    from src.state import ContainerStateManager
    alert = MagicMock(); alert.send_health_alert = AsyncMock()
    mon = DockerEventMonitor(state_manager=ContainerStateManager(), alert_manager=alert)
    healer = MagicMock(); healer.is_enabled.return_value = False; healer.heal = AsyncMock()
    mon.set_auto_healer(healer)
    await mon._handle_health_event({"Actor": {"Attributes": {"name": "plex"}}, "_alert_type": "health"})
    healer.heal.assert_not_awaited()
    alert.send_health_alert.assert_awaited_once()
```
- [ ] Step 2: `uv run pytest tests/test_docker_events.py -k "opted_in or not_opted_in" -v` -> FAIL
- [ ] Step 3: In `src/monitors/docker_events.py`:
(a) `__init__`, after `self._unhealthy_alerted: set[str] = set()`: `self._auto_healer: Any | None = None`
(b) New method:
```python
    def set_auto_healer(self, auto_healer: Any) -> None:
        """Late-bind the AutoHealer (created after the ContainerController exists)."""
        self._auto_healer = auto_healer
```
(c) In `_handle_health_event`, replace the final block:
```python
        logger.info(f"Container {container_name} is unhealthy")
        await self.alert_manager.send_health_alert(container_name=container_name, health_status="unhealthy")
```
with:
```python
        logger.info(f"Container {container_name} is unhealthy")
        if self._auto_healer is not None and self._auto_healer.is_enabled(container_name):
            await self._auto_healer.heal(container_name)
            return
        await self.alert_manager.send_health_alert(container_name=container_name, health_status="unhealthy")
```
- [ ] Step 4: `uv run pytest tests/test_docker_events.py -k "opted_in or not_opted_in" -v` -> 2 passed
- [ ] Step 5: `uv run pytest tests/test_docker_events.py -v` -> all pass
- [ ] Step 6: `git add src/monitors/docker_events.py tests/test_docker_events.py && git commit -m "feat: auto-heal branch in DockerEventMonitor unhealthy handling"`

---

### Task 6: Wire AutoHealer in startup
**Files:** Modify `src/startup.py`

- [ ] Step 1: In `src/startup.py`, after the `if nl_processor and controller:` block (~line 458):
```python
    if controller is not None:
        from src.services.auto_healer import AutoHealer
        auto_healer = AutoHealer(config=config.auto_heal, controller=controller, alert_manager=alert_manager)
        monitor.set_auto_healer(auto_healer)
        logger.info(
            f"Auto-heal {'enabled' if config.auto_heal.enabled else 'disabled'} "
            f"for {len(config.auto_heal.containers)} container(s)"
        )
```
- [ ] Step 2: `uv run mypy src/startup.py` -> Success
- [ ] Step 3: `uv run pytest tests/ -q` -> all pass
- [ ] Step 4: `git add src/startup.py && git commit -m "feat: build and inject AutoHealer in startup"`

---

## PHASE C — Image-update detection (Feature 1)

### Task 7: send_update_alert (manager + protocol + proxy)
**Files:** Modify `src/alerts/manager.py`, `src/alert_proxy.py`; Test `tests/test_alert_manager.py`
> The `send_update_alert` protocol line was added in Task 4 Step 3.

- [ ] Step 1: Append to `tests/test_alert_manager.py`:
```python
async def test_send_update_alert_batches_with_buttons():
    from src.alerts.manager import AlertManager
    bot = MagicMock(); bot.send_message = AsyncMock()
    mgr = AlertManager(bot=bot, chat_id=123)
    await mgr.send_update_alert([("radarr", "linuxserver/radarr:latest"), ("sonarr", "linuxserver/sonarr:latest")])
    bot.send_message.assert_awaited_once()
    text = bot.send_message.call_args.kwargs["text"]
    kb = bot.send_message.call_args.kwargs["reply_markup"]
    assert "radarr" in text and "sonarr" in text and "(2)" in text
    assert len(kb.inline_keyboard) == 2
    assert kb.inline_keyboard[0][0].callback_data == "pull:radarr"


async def test_send_update_alert_empty_noop():
    from src.alerts.manager import AlertManager
    bot = MagicMock(); bot.send_message = AsyncMock()
    mgr = AlertManager(bot=bot, chat_id=123)
    await mgr.send_update_alert([])
    bot.send_message.assert_not_awaited()
```
- [ ] Step 2: `uv run pytest tests/test_alert_manager.py -k update_alert -v` -> FAIL
- [ ] Step 3: Implement on `AlertManager` after `send_autoheal_alert`. Add `from src.constants import IMAGE_UPDATE_MAX_SHOWN` to the top-of-file imports:
```python
    async def send_update_alert(self, updates: list[tuple[str, str]]) -> None:
        """Send a single batched digest of containers with newer images available."""
        if not updates:
            return
        lines = [f"⬇️ *Image updates available* ({len(updates)})", ""]
        rows: list[list[InlineKeyboardButton]] = []
        for name, image in updates[:IMAGE_UPDATE_MAX_SHOWN]:
            lines.append(f"• {escape_markdown(name)} - {escape_markdown(image)}")
            rows.append([InlineKeyboardButton(text=f"⬇️ Pull {name}", callback_data=truncate_callback_data("pull:", name))])
        if len(updates) > IMAGE_UPDATE_MAX_SHOWN:
            lines.append(f"...and {len(updates) - IMAGE_UPDATE_MAX_SHOWN} more")
        keyboard = InlineKeyboardMarkup(inline_keyboard=rows)
        try:
            await send_with_retry(self.bot.send_message, chat_id=self.chat_id, text="\n".join(lines),
                                  parse_mode="Markdown", reply_markup=keyboard)
            logger.info(f"Sent image-update digest for {len(updates)} container(s)")
        except Exception as e:
            logger.error(f"Failed to send update alert: {e}")
```
- [ ] Step 4: Add to `src/alert_proxy.py` after `send_autoheal_alert`:
```python
    async def send_update_alert(self, updates: list[tuple[str, str]]) -> None:
        await self._send_alert("send_update_alert", updates=updates)
```
- [ ] Step 5: `uv run pytest tests/test_alert_manager.py -k update_alert -v` -> 2 passed
- [ ] Step 6: `git add src/alerts/manager.py src/alert_proxy.py tests/test_alert_manager.py && git commit -m "feat: add batched send_update_alert with Pull buttons"`

---

### Task 8: pull_callback (digest button -> pull confirmation)
**Files:** Modify `src/bot/alert_callbacks.py`, `src/bot/telegram_bot.py`; Test `tests/test_alert_callbacks.py`
> `pull:<name>` shows the standard `/pull` confirmation via `_build_confirmation`; existing `ctrl_confirm` handler runs `pull_and_recreate`.

- [ ] Step 1: Append to `tests/test_alert_callbacks.py`:
```python
async def test_pull_callback_shows_confirmation():
    from src.bot.alert_callbacks import pull_callback
    from src.state import ContainerStateManager
    from src.models import ContainerInfo
    state = ContainerStateManager()
    state.update(ContainerInfo(name="radarr", status="running", health=None, image="img", started_at=None))
    controller = MagicMock(); controller.is_protected.return_value = False
    handler = pull_callback(state, controller)
    cb = MagicMock(); cb.data = "pull:radarr"; cb.answer = AsyncMock()
    cb.message = MagicMock(); cb.message.answer = AsyncMock()
    await handler(cb)
    cb.message.answer.assert_awaited_once()
    kb = cb.message.answer.call_args.kwargs["reply_markup"]
    assert kb.inline_keyboard[0][0].callback_data == "ctrl_confirm:pull:radarr"


async def test_pull_callback_blocks_protected():
    from src.bot.alert_callbacks import pull_callback
    from src.state import ContainerStateManager
    from src.models import ContainerInfo
    state = ContainerStateManager()
    state.update(ContainerInfo(name="mariadb", status="running", health=None, image="img", started_at=None))
    controller = MagicMock(); controller.is_protected.return_value = True
    handler = pull_callback(state, controller)
    cb = MagicMock(); cb.data = "pull:mariadb"; cb.answer = AsyncMock()
    cb.message = MagicMock(); cb.message.answer = AsyncMock()
    await handler(cb)
    cb.message.answer.assert_not_awaited()
    cb.answer.assert_awaited()
```
- [ ] Step 2: `uv run pytest tests/test_alert_callbacks.py -k pull_callback -v` -> FAIL
- [ ] Step 3: Add to `src/bot/alert_callbacks.py` (mirrors `restart_callback`):
```python
def pull_callback(
    state: ContainerStateManager,
    controller: ContainerController,
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for the image-update digest 'Pull' button - shows a confirmation."""

    async def handler(callback: CallbackQuery) -> None:
        if not callback.data:
            return
        parts = callback.data.split(":", 1)
        if len(parts) < 2:
            await callback.answer("Invalid callback data")
            return
        container_name = parts[1]
        if not validate_container_name(container_name):
            await callback.answer("Invalid container name")
            return
        matches = state.find_by_name(container_name)
        if not matches:
            await callback.answer(f"Container '{container_name}' not found")
            return
        actual_name = matches[0].name
        if controller.is_protected(actual_name):
            await callback.answer(f"{actual_name} is protected", show_alert=True)
            return
        await callback.answer()
        info = state.get(actual_name)
        status = info.status if info else "unknown"
        from src.bot.control_commands import _build_confirmation
        text, keyboard = _build_confirmation("pull", actual_name, status)
        if callback.message:
            await callback.message.answer(text, reply_markup=keyboard, parse_mode="Markdown")

    return handler
```
- [ ] Step 4: In `src/bot/telegram_bot.py` `register_commands()`, after the `restart:` registration: `dp.callback_query.register(pull_callback(state, controller), F.data.startswith("pull:"))`. Import `pull_callback` with the other alert_callbacks imports.
- [ ] Step 5: `uv run pytest tests/test_alert_callbacks.py -k pull_callback -v` -> 2 passed
- [ ] Step 6: `uv run pytest tests/test_control_commands.py -v` -> all pass (confirms ctrl_confirm:pull execution path)
- [ ] Step 7: `git add src/bot/alert_callbacks.py src/bot/telegram_bot.py tests/test_alert_callbacks.py && git commit -m "feat: pull_callback for image-update digest buttons"`

---

### Task 9: ImageUpdateMonitor
**Files:** Create `src/monitors/image_update_monitor.py`; Test `tests/test_image_update_monitor.py`

- [ ] Step 1: `tests/test_image_update_monitor.py`:
```python
from unittest.mock import AsyncMock, MagicMock

from src.config import ImageUpdatesConfig
from src.monitors.image_update_monitor import ImageUpdateMonitor, extract_local_digests


def _container(name, tag, repo_digests):
    c = MagicMock()
    c.name = name
    c.image.tags = [tag]
    c.image.attrs = {"RepoDigests": repo_digests}
    return c


def _monitor(client, ignored=None):
    alert = MagicMock(); alert.send_update_alert = AsyncMock()
    cfg = ImageUpdatesConfig(enabled=True, poll_interval_hours=24)
    return ImageUpdateMonitor(docker_client=client, config=cfg, alert_manager=alert, ignored_containers=ignored), alert


def test_extract_local_digests():
    c = _container("x", "img:latest", ["repo@sha256:aaa", "repo@sha256:bbb"])
    assert extract_local_digests(c) == ["sha256:aaa", "sha256:bbb"]


async def test_alerts_when_remote_differs():
    client = MagicMock()
    client.containers.list.return_value = [_container("radarr", "linuxserver/radarr:latest", ["linuxserver/radarr@sha256:old"])]
    client.images.get_registry_data.return_value = MagicMock(id="sha256:new")
    mon, alert = _monitor(client)
    await mon.check_once()
    alert.send_update_alert.assert_awaited_once_with([("radarr", "linuxserver/radarr:latest")])


async def test_no_alert_when_up_to_date():
    client = MagicMock()
    client.containers.list.return_value = [_container("radarr", "linuxserver/radarr:latest", ["linuxserver/radarr@sha256:same"])]
    client.images.get_registry_data.return_value = MagicMock(id="sha256:same")
    mon, alert = _monitor(client)
    await mon.check_once()
    alert.send_update_alert.assert_not_awaited()


async def test_dedup_same_remote_digest():
    client = MagicMock()
    client.containers.list.return_value = [_container("radarr", "linuxserver/radarr:latest", ["linuxserver/radarr@sha256:old"])]
    client.images.get_registry_data.return_value = MagicMock(id="sha256:new")
    mon, alert = _monitor(client)
    await mon.check_once()
    await mon.check_once()
    assert alert.send_update_alert.await_count == 1


async def test_skips_ignored():
    client = MagicMock()
    client.containers.list.return_value = [_container("radarr", "linuxserver/radarr:latest", ["linuxserver/radarr@sha256:old"])]
    client.images.get_registry_data.return_value = MagicMock(id="sha256:new")
    mon, alert = _monitor(client, ignored=["radarr"])
    await mon.check_once()
    alert.send_update_alert.assert_not_awaited()


async def test_skips_when_no_repo_digests():
    client = MagicMock()
    client.containers.list.return_value = [_container("built", "local/built:latest", [])]
    mon, alert = _monitor(client)
    await mon.check_once()
    alert.send_update_alert.assert_not_awaited()


async def test_registry_error_does_not_crash():
    client = MagicMock()
    client.containers.list.return_value = [_container("radarr", "linuxserver/radarr:latest", ["linuxserver/radarr@sha256:old"])]
    client.images.get_registry_data.side_effect = RuntimeError("registry down")
    mon, alert = _monitor(client)
    await mon.check_once()
    alert.send_update_alert.assert_not_awaited()
```
- [ ] Step 2: `uv run pytest tests/test_image_update_monitor.py -v` -> FAIL (ModuleNotFoundError)
- [ ] Step 3: `src/monitors/image_update_monitor.py`:
```python
import asyncio
import logging
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from src.config import ImageUpdatesConfig
    from src.alerts.manager import AlertSender
    from src.services.docker_client import SharedDockerClient

logger = logging.getLogger(__name__)


def extract_local_digests(container: Any) -> list[str]:
    """Return the sha256 digests recorded in a container image's RepoDigests."""
    image = getattr(container, "image", None)
    repo_digests = image.attrs.get("RepoDigests", []) if image is not None else []
    digests: list[str] = []
    for rd in repo_digests:
        if "@" in rd:
            digests.append(rd.split("@", 1)[1])
    return digests


class ImageUpdateMonitor:
    """Polls registries for newer images of running containers and notifies.

    Notify-only: never pulls. A batched digest is sent per cycle, deduped by
    remote digest so the same available update isn't re-announced every cycle.
    """

    _STOP_TICK_SECONDS = 1

    def __init__(
        self,
        docker_client: "SharedDockerClient",
        config: "ImageUpdatesConfig",
        alert_manager: "AlertSender",
        ignored_containers: list[str] | None = None,
    ) -> None:
        self._docker = docker_client
        self._config = config
        self._alert_manager = alert_manager
        self._ignored = set(ignored_containers or [])
        self._running = False
        self._notified: dict[str, str] = {}

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
        updates = await asyncio.to_thread(self._collect_updates)
        if updates:
            await self._alert_manager.send_update_alert(updates)

    def _collect_updates(self) -> list[tuple[str, str]]:
        updates: list[tuple[str, str]] = []
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
                self._notified[name] = remote_digest
                updates.append((name, image_ref))
            except Exception as e:
                logger.debug(f"Image-update check skipped for {name}: {e}")
                continue
        return updates
```
- [ ] Step 4: `uv run pytest tests/test_image_update_monitor.py -v` -> 7 passed
- [ ] Step 5: `git add src/monitors/image_update_monitor.py tests/test_image_update_monitor.py && git commit -m "feat: add ImageUpdateMonitor with digest compare and dedup"`

> **Verify-during-implementation (highest uncertainty):** confirm `get_registry_data(image_ref).id` matches the container's `RepoDigests` form for a real multi-arch image; if not, compare against the manifest-list digest. One-off:
> `uv run python -c "import docker; c=docker.from_env(); ct=c.containers.list()[0]; print('local', ct.image.attrs.get('RepoDigests')); print('remote', c.images.get_registry_data(ct.image.tags[0]).id)"`

---

### Task 10: Wire ImageUpdateMonitor into startup + shutdown
**Files:** Modify `src/background.py`, `src/startup.py`

- [ ] Step 1: In `src/background.py`: (a) import `from src.monitors.image_update_monitor import ImageUpdateMonitor`; (b) `__init__` after `self.memory_monitor`: `self.image_update_monitor: ImageUpdateMonitor | None = None`; (c) `shutdown()` after memory_monitor stop:
```python
        if self.image_update_monitor is not None:
            self.image_update_monitor.stop()
```
- [ ] Step 2: In `src/startup.py` after `bg.resource_monitor = resource_monitor`:
```python
    image_update_monitor = None
    if config.image_updates.enabled:
        from src.monitors.image_update_monitor import ImageUpdateMonitor
        image_update_monitor = ImageUpdateMonitor(
            docker_client=monitor.shared_client,  # type: ignore[arg-type]
            config=config.image_updates,
            alert_manager=alert_manager,
            ignored_containers=config.ignored_containers,
        )
        logger.info("Image-update detection enabled")
    bg.image_update_monitor = image_update_monitor
```
- [ ] Step 3: In `_start_background_monitors` after the memory_monitor start block:
```python
    if bg.image_update_monitor is not None:
        bg.add_task(asyncio.create_task(bg.image_update_monitor.start()))
```
- [ ] Step 4: `uv run mypy src/ && uv run pytest tests/ -q` -> mypy Success; all pass
- [ ] Step 5: `git add src/background.py src/startup.py && git commit -m "feat: wire ImageUpdateMonitor into startup and shutdown"`

---

## PHASE D — Richer startup message (Feature 3)

### Task 11: announced-version persistence
**Files:** Create `src/utils/version_store.py`; Test `tests/test_version_store.py`

- [ ] Step 1: `tests/test_version_store.py`:
```python
from src.utils.version_store import read_announced_version, write_announced_version


def test_read_missing_returns_none(tmp_path):
    assert read_announced_version(str(tmp_path / "nope.json")) is None


def test_write_then_read_roundtrip(tmp_path):
    p = str(tmp_path / "announced_version.json")
    write_announced_version(p, "0.12.0")
    assert read_announced_version(p) == "0.12.0"


def test_read_corrupt_returns_none(tmp_path):
    p = tmp_path / "announced_version.json"
    p.write_text("{not json")
    assert read_announced_version(str(p)) is None
```
- [ ] Step 2: `uv run pytest tests/test_version_store.py -v` -> FAIL
- [ ] Step 3: `src/utils/version_store.py`:
```python
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
    except OSError as e:
        logger.error(f"Failed to write announced version to {path}: {e}")
```
- [ ] Step 4: `uv run pytest tests/test_version_store.py -v` -> 3 passed
- [ ] Step 5: `git add src/utils/version_store.py tests/test_version_store.py && git commit -m "feat: add announced-version persistence helper"`

---

### Task 12: Extract shared status-summary helper
**Files:** Modify `src/bot/health_command.py`; Test `tests/test_health_command.py`

- [ ] Step 1: Append to `tests/test_health_command.py` (ensure `from unittest.mock import MagicMock`):
```python
def test_build_status_lines_includes_new_monitors():
    from src.bot.health_command import build_status_lines
    from src.config import AutoHealConfig
    monitor = MagicMock(); monitor.is_running = True
    monitor.state_manager.get_all.return_value = [1, 2, 3]
    image_mon = MagicMock(); image_mon.is_running = True
    lines = build_status_lines(
        monitor=monitor, log_watcher=None, resource_monitor=None, memory_monitor=None,
        unraid_client=None, unraid_system_monitor=None, unraid_array_monitor=None,
        image_update_monitor=image_mon,
        auto_heal_config=AutoHealConfig(enabled=True, containers=["radarr"], max_restarts=3, window_minutes=60),
    )
    blob = "\n".join(lines)
    assert "Image updates" in blob
    assert "Auto-heal" in blob
    assert "1 container" in blob
```
- [ ] Step 2: `uv run pytest tests/test_health_command.py -k build_status_lines -v` -> FAIL
- [ ] Step 3: Add `build_status_lines(...)` to `src/bot/health_command.py` (moves the handler's per-monitor logic + two new rows):
```python
def build_status_lines(
    monitor: "DockerEventMonitor | None" = None,
    log_watcher: "LogWatcher | None" = None,
    resource_monitor: "ResourceMonitor | None" = None,
    memory_monitor: "MemoryMonitor | None" = None,
    unraid_client: Any = None,
    unraid_system_monitor: "UnraidSystemMonitor | None" = None,
    unraid_array_monitor: "ArrayMonitor | None" = None,
    image_update_monitor: Any = None,
    auto_heal_config: Any = None,
) -> list[str]:
    lines: list[str] = ["*Monitors:*"]
    if monitor:
        status = "✅ Running" if monitor.is_running else "🔴 Stopped"
        lines.append(f"  Docker Events: {status} ({len(monitor.state_manager.get_all())} containers)")
    else:
        lines.append("  Docker Events: ⚪ Not configured")
    if log_watcher:
        status = "✅ Running" if log_watcher.is_running else "🔴 Stopped"
        drop_info = f", {log_watcher.total_drops} dropped" if log_watcher.total_drops else ""
        lines.append(f"  Log Watcher: {status} ({len(log_watcher.containers)} containers{drop_info})")
    else:
        lines.append("  Log Watcher: ⚪ Not configured")
    if resource_monitor:
        lines.append(f"  Resources: {'✅ Running' if resource_monitor.is_running else '🔴 Stopped'}")
    else:
        lines.append("  Resources: ⚪ Disabled")
    if memory_monitor:
        lines.append(f"  Memory: {'✅ Running' if memory_monitor.is_running else '🔴 Stopped'}")
    else:
        lines.append("  Memory: ⚪ Disabled")
    if image_update_monitor:
        lines.append(f"  Image updates: {'✅ Running' if image_update_monitor.is_running else '🔴 Stopped'}")
    else:
        lines.append("  Image updates: ⚪ Disabled")
    if auto_heal_config is not None and auto_heal_config.enabled and auto_heal_config.containers:
        lines.append(f"  Auto-heal: ✅ {len(auto_heal_config.containers)} container(s)")
    else:
        lines.append("  Auto-heal: ⚪ Disabled")
    if unraid_client:
        lines.append(f"  Unraid: {'✅ Connected' if unraid_client.is_connected else '🔴 Disconnected'}")
        if unraid_system_monitor:
            lines.append(f"    System: {'✅' if unraid_system_monitor.is_running else '🔴'}")
        if unraid_array_monitor:
            lines.append(f"    Array: {'✅' if unraid_array_monitor.is_running else '🔴'}")
    else:
        lines.append("  Unraid: ⚪ Not configured")
    return lines
```
Refactor the `/health` handler: delete the inline `*Monitors:*`-through-Unraid block and replace with `lines.extend(build_status_lines(...))` passing the same monitor refs plus `image_update_monitor=image_update_monitor, auto_heal_config=auto_heal_config`. Add `image_update_monitor` and `auto_heal_config` (default `None`) to the `health_command(...)` factory signature.
- [ ] Step 4: `uv run pytest tests/test_health_command.py -v` -> all pass
- [ ] Step 5: In `src/startup.py` where `health_command(...)` is registered (~line 476), add `image_update_monitor=image_update_monitor,` and `auto_heal_config=config.auto_heal,`.
- [ ] Step 6: `git add src/bot/health_command.py src/startup.py tests/test_health_command.py && git commit -m "refactor: extract build_status_lines and add new-monitor rows"`

---

### Task 13: Enriched startup notification + What's new
**Files:** Modify `src/startup.py`; Test `tests/test_startup_notification.py`

- [ ] Step 1: `tests/test_startup_notification.py`:
```python
from unittest.mock import AsyncMock, MagicMock

import src.startup as startup_mod
from src.config import AutoHealConfig


def _ctx():
    bot = MagicMock(); bot.send_message = AsyncMock()
    chat_store = MagicMock(); chat_store.get_all_chat_ids.return_value = [555]
    state = MagicMock(); state.get_all.return_value = [1, 2]
    uc = MagicMock(); uc.client = None
    return bot, chat_store, state, uc


async def test_whats_new_shown_on_version_change(tmp_path, monkeypatch):
    bot, chat_store, state, uc = _ctx()
    path = str(tmp_path / "announced_version.json")
    monkeypatch.setattr(startup_mod, "BOT_VERSION", "0.12.0")
    monkeypatch.setattr(startup_mod, "ANNOUNCED_VERSION_PATH", path)
    await startup_mod._send_startup_notification(
        bot, chat_store, state, {"containers": []}, uc,
        image_update_monitor=None, auto_heal_config=AutoHealConfig(),
        resource_monitor=None, memory_monitor=None, log_watcher=None, monitor=None,
    )
    text = bot.send_message.call_args.kwargs["text"]
    assert "What's new" in text
    assert "Image-update detection" in text


async def test_whats_new_hidden_on_same_version(tmp_path, monkeypatch):
    bot, chat_store, state, uc = _ctx()
    path = str(tmp_path / "announced_version.json")
    from src.utils.version_store import write_announced_version
    write_announced_version(path, "0.12.0")
    monkeypatch.setattr(startup_mod, "BOT_VERSION", "0.12.0")
    monkeypatch.setattr(startup_mod, "ANNOUNCED_VERSION_PATH", path)
    await startup_mod._send_startup_notification(
        bot, chat_store, state, {"containers": []}, uc,
        image_update_monitor=None, auto_heal_config=AutoHealConfig(),
        resource_monitor=None, memory_monitor=None, log_watcher=None, monitor=None,
    )
    text = bot.send_message.call_args.kwargs["text"]
    assert "What's new" not in text
    assert "Bot started" in text
```
- [ ] Step 2: `uv run pytest tests/test_startup_notification.py -v` -> FAIL
- [ ] Step 3: In `src/startup.py`: (a) add imports near top:
```python
from src.bot.health_command import BOT_VERSION, build_status_lines
from src.constants import WHATS_NEW, ANNOUNCED_VERSION_PATH
from src.utils.version_store import read_announced_version, write_announced_version
```
(b) Replace `_send_startup_notification`:
```python
async def _send_startup_notification(
    bot: Bot,
    chat_id_store: ChatIdStore,
    state: ContainerStateManager,
    log_watching_config: dict[str, Any],
    uc: _UnraidComponents,
    image_update_monitor: Any = None,
    auto_heal_config: Any = None,
    resource_monitor: Any = None,
    memory_monitor: Any = None,
    log_watcher: Any = None,
    monitor: Any = None,
) -> None:
    previous = read_announced_version(ANNOUNCED_VERSION_PATH)
    show_whats_new = previous != BOT_VERSION and BOT_VERSION in WHATS_NEW
    status_lines = build_status_lines(
        monitor=monitor, log_watcher=log_watcher, resource_monitor=resource_monitor,
        memory_monitor=memory_monitor, unraid_client=uc.client,
        unraid_system_monitor=uc.system_monitor, unraid_array_monitor=uc.array_monitor,
        image_update_monitor=image_update_monitor, auto_heal_config=auto_heal_config,
    )
    parts = [f"🟢 *Bot started* - v{BOT_VERSION}", "", *status_lines]
    if show_whats_new:
        parts.append("")
        parts.append(f"✨ *What's new in v{BOT_VERSION}*")
        parts.extend(f"  • {item}" for item in WHATS_NEW[BOT_VERSION])
    startup_msg = "\n".join(parts)
    for cid in chat_id_store.get_all_chat_ids():
        try:
            await send_with_retry(bot.send_message, chat_id=cid, text=startup_msg, parse_mode="Markdown")
        except Exception as e:
            logger.warning(f"Failed to send startup notification: {e}")
    if show_whats_new:
        write_announced_version(ANNOUNCED_VERSION_PATH, BOT_VERSION)
```
(c) Update the call site (~line 499):
```python
    await _send_startup_notification(
        bot, chat_id_store, state, log_watching_config, uc,
        image_update_monitor=image_update_monitor, auto_heal_config=config.auto_heal,
        resource_monitor=resource_monitor, memory_monitor=memory_monitor,
        log_watcher=log_watcher, monitor=monitor,
    )
```
- [ ] Step 4: `uv run pytest tests/test_startup_notification.py -v` -> 2 passed
- [ ] Step 5: `git check-ignore data/announced_version.json` -> prints path (ignored via `data/`); else add to `.gitignore`
- [ ] Step 6: `git add src/startup.py tests/test_startup_notification.py && git commit -m "feat: enriched startup message with version-gated Whats new"`

---

## PHASE E — Docs, version bump, full verification

### Task 14: Docs + version bump + final gates
**Files:** `pyproject.toml`, `CHANGELOG.md`, `README.md`, `CLAUDE.md`

- [ ] Step 1: `pyproject.toml`: `version = "0.11.1"` -> `version = "0.12.0"`.
- [ ] Step 2: CHANGELOG `## [0.12.0] - 2026-06-03` with Added: image-update detection (opt-in), auto-heal (opt-in), richer startup message + What's new, CI test pipeline.
- [ ] Step 3: README: add Image-update detection + Auto-heal bullets and the two config blocks (image_updates enabled:false / poll_interval_hours:24; auto_heal enabled:true / containers / max_restarts:3 / window_minutes:60).
- [ ] Step 4: CLAUDE.md: add `pull:container_name` to Callback Data Conventions; add `image_updates`/`auto_heal` to config key-sections; add `data/announced_version.json` to data-files; add new source files to the File Map; update `.claude/structure/*.yaml` if present.
- [ ] Step 5: Gates: `uv run ruff check src/` ; `uv run mypy src/` ; `uv run pytest tests/` -> ruff clean; mypy Success; all pass.
- [ ] Step 6: `TZ=UTC uv run pytest tests/ -q` -> all pass (catches tz/clock coupling).
- [ ] Step 7: `git add pyproject.toml CHANGELOG.md README.md CLAUDE.md .claude/structure/ && git commit -m "docs: v0.12.0 - image updates, auto-heal, richer startup"`
- [ ] Step 8: Open PR:
```bash
git push -u origin feature/image-updates-autoheal-startup
gh pr create --base master --title "feat: image-update detection, auto-heal, richer startup (v0.12.0)" --body "Implements docs/superpowers/specs/2026-06-03-image-updates-autoheal-startup-design.md. Three opt-in/low-risk additions surfaced by the SOTA scan. All CI gates green."
```

---

## Self-Review (completed during planning)
- **Spec coverage:** image-update detection -> Tasks 7-10; auto-heal -> Tasks 3-6; richer startup + What's new -> Tasks 11-13; config -> Tasks 1-2; docs/version -> Task 14. Mute decision (digest excludes ignored only) -> encoded in ImageUpdateMonitor (no mute dependency).
- **Type consistency:** ImageUpdatesConfig(enabled, poll_interval_hours); AutoHealConfig(enabled, containers, max_restarts, window_minutes); AutoHealer(config, controller, alert_manager) with is_enabled/heal; send_update_alert(updates); send_autoheal_alert(container_name, attempt, max_attempts, gave_up); set_auto_healer; build_status_lines(...); read_announced_version(path)/write_announced_version(path, version) — identical across tasks.
- **No placeholders:** every code step has complete code; verify-during-implementation items (multi-arch digest, ctrl_confirm-handles-pull) are explicit verification steps with commands.
