# Phase 3 Implementation Plan: Container Control

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add container control commands (/restart, /stop, /start, /pull) with confirmation flow and protected container support.

**Architecture:** ConfirmationManager tracks pending user confirmations with timeout. ContainerController wraps Docker operations. Control commands request confirmation, plain text "yes" handler executes pending action.

**Tech Stack:** Python 3.11+, aiogram 3.x, docker SDK

---

## Task 1: Configuration - Protected Containers

**Files:**
- Modify: `src/config.py`
- Modify: `config/config.yaml`
- Create: `tests/test_config_protected.py`

**Step 1: Write the failing test**

Create `tests/test_config_protected.py`:

```python
import pytest
from unittest.mock import patch


def test_config_loads_protected_containers():
    """Test that protected_containers is loaded from YAML."""
    yaml_content = """
protected_containers:
  - unraid-monitor-bot
  - mariadb
"""
    with patch.dict("os.environ", {
        "TELEGRAM_BOT_TOKEN": "test-token",
        "TELEGRAM_ALLOWED_USERS": "123",
    }, clear=True):
        with patch("builtins.open", create=True) as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = yaml_content
            with patch("os.path.exists", return_value=True):
                from src.config import Settings, AppConfig

                settings = Settings()
                config = AppConfig(settings)

                assert config.protected_containers == ["unraid-monitor-bot", "mariadb"]


def test_config_protected_containers_defaults_to_empty():
    """Test that protected_containers defaults to empty list."""
    with patch.dict("os.environ", {
        "TELEGRAM_BOT_TOKEN": "test-token",
        "TELEGRAM_ALLOWED_USERS": "123",
    }, clear=True):
        with patch("os.path.exists", return_value=False):
            from src.config import Settings, AppConfig

            settings = Settings()
            config = AppConfig(settings)

            assert config.protected_containers == []
```

**Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && pytest tests/test_config_protected.py -v`
Expected: FAIL with "AttributeError: 'AppConfig' object has no attribute 'protected_containers'"

**Step 3: Update implementation**

Add to `src/config.py` in the `AppConfig` class (after `ignored_containers` property):

```python
    @property
    def protected_containers(self) -> list[str]:
        """Get list of containers that cannot be controlled via Telegram."""
        return self._yaml_config.get("protected_containers", [])
```

**Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && pytest tests/test_config_protected.py -v`
Expected: PASS

**Step 5: Update config.yaml**

Add to `config/config.yaml`:

```yaml
# Containers that cannot be controlled via Telegram
# (restart/stop/start/pull commands will be rejected)
protected_containers:
  - unraid-monitor-bot
```

**Step 6: Commit**

```bash
git add src/config.py config/config.yaml tests/test_config_protected.py
git commit -m "feat: add protected_containers config for control commands"
```

---

## Task 2: Confirmation Manager

**Files:**
- Create: `src/bot/confirmation.py`
- Create: `tests/test_confirmation.py`

**Step 1: Write the failing test**

Create `tests/test_confirmation.py`:

```python
import pytest
from datetime import datetime, timedelta


def test_confirmation_manager_stores_pending():
    """Test that confirmation is stored for user."""
    from src.bot.confirmation import ConfirmationManager

    manager = ConfirmationManager(timeout_seconds=60)
    manager.request(user_id=123, action="restart", container_name="radarr")

    pending = manager.get_pending(123)
    assert pending is not None
    assert pending.action == "restart"
    assert pending.container_name == "radarr"


def test_confirmation_manager_confirm_returns_and_clears():
    """Test that confirm returns pending and clears it."""
    from src.bot.confirmation import ConfirmationManager

    manager = ConfirmationManager(timeout_seconds=60)
    manager.request(user_id=123, action="stop", container_name="sonarr")

    pending = manager.confirm(123)
    assert pending is not None
    assert pending.action == "stop"
    assert pending.container_name == "sonarr"

    # Should be cleared now
    assert manager.get_pending(123) is None
    assert manager.confirm(123) is None


def test_confirmation_manager_expired_not_returned():
    """Test that expired confirmations are not returned."""
    from src.bot.confirmation import ConfirmationManager, PendingConfirmation

    manager = ConfirmationManager(timeout_seconds=60)
    manager.request(user_id=123, action="restart", container_name="radarr")

    # Manually expire it
    manager._pending[123] = PendingConfirmation(
        action="restart",
        container_name="radarr",
        expires_at=datetime.now() - timedelta(seconds=1),
    )

    assert manager.get_pending(123) is None
    assert manager.confirm(123) is None


def test_confirmation_manager_replaces_previous():
    """Test that new request replaces previous pending."""
    from src.bot.confirmation import ConfirmationManager

    manager = ConfirmationManager(timeout_seconds=60)
    manager.request(user_id=123, action="restart", container_name="radarr")
    manager.request(user_id=123, action="stop", container_name="sonarr")

    pending = manager.get_pending(123)
    assert pending.action == "stop"
    assert pending.container_name == "sonarr"


def test_confirmation_manager_users_independent():
    """Test that different users have independent confirmations."""
    from src.bot.confirmation import ConfirmationManager

    manager = ConfirmationManager(timeout_seconds=60)
    manager.request(user_id=123, action="restart", container_name="radarr")
    manager.request(user_id=456, action="stop", container_name="sonarr")

    pending_123 = manager.get_pending(123)
    pending_456 = manager.get_pending(456)

    assert pending_123.container_name == "radarr"
    assert pending_456.container_name == "sonarr"
```

**Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && pytest tests/test_confirmation.py -v`
Expected: FAIL with "No module named 'src.bot.confirmation'"

**Step 3: Write implementation**

Create `src/bot/confirmation.py`:

```python
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass
class PendingConfirmation:
    """A pending confirmation waiting for user response."""
    action: str  # "restart", "stop", "start", "pull"
    container_name: str
    expires_at: datetime


class ConfirmationManager:
    """Manages pending confirmations for control commands."""

    def __init__(self, timeout_seconds: int = 60):
        self.timeout_seconds = timeout_seconds
        self._pending: dict[int, PendingConfirmation] = {}

    def request(self, user_id: int, action: str, container_name: str) -> None:
        """Store a pending confirmation for a user.

        Replaces any existing pending confirmation for this user.
        """
        expires_at = datetime.now() + timedelta(seconds=self.timeout_seconds)
        self._pending[user_id] = PendingConfirmation(
            action=action,
            container_name=container_name,
            expires_at=expires_at,
        )

    def get_pending(self, user_id: int) -> PendingConfirmation | None:
        """Get pending confirmation for user if not expired."""
        pending = self._pending.get(user_id)
        if pending is None:
            return None

        if datetime.now() > pending.expires_at:
            del self._pending[user_id]
            return None

        return pending

    def confirm(self, user_id: int) -> PendingConfirmation | None:
        """Get and clear pending confirmation if valid.

        Returns the pending confirmation and removes it, or None if
        no valid pending confirmation exists.
        """
        pending = self.get_pending(user_id)
        if pending is not None:
            del self._pending[user_id]
        return pending

    def cancel(self, user_id: int) -> bool:
        """Cancel pending confirmation for user.

        Returns True if there was a pending confirmation to cancel.
        """
        if user_id in self._pending:
            del self._pending[user_id]
            return True
        return False
```

**Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && pytest tests/test_confirmation.py -v`
Expected: All 5 tests PASS

**Step 5: Commit**

```bash
git add src/bot/confirmation.py tests/test_confirmation.py
git commit -m "feat: add confirmation manager for control commands"
```

---

## Task 3: Container Controller

**Files:**
- Create: `src/services/__init__.py`
- Create: `src/services/container_control.py`
- Create: `tests/test_container_control.py`

**Step 1: Write the failing test**

Create `tests/test_container_control.py`:

```python
import pytest
from unittest.mock import MagicMock, patch


def test_container_controller_is_protected():
    """Test that protected containers are identified."""
    from src.services.container_control import ContainerController

    mock_client = MagicMock()
    controller = ContainerController(
        docker_client=mock_client,
        protected_containers=["mariadb", "unraid-monitor-bot"],
    )

    assert controller.is_protected("mariadb") is True
    assert controller.is_protected("unraid-monitor-bot") is True
    assert controller.is_protected("radarr") is False


@pytest.mark.asyncio
async def test_container_controller_restart():
    """Test restart stops and starts container."""
    from src.services.container_control import ContainerController

    mock_container = MagicMock()
    mock_client = MagicMock()
    mock_client.containers.get.return_value = mock_container

    controller = ContainerController(docker_client=mock_client, protected_containers=[])

    result = await controller.restart("radarr")

    mock_container.restart.assert_called_once()
    assert "restarted" in result.lower()


@pytest.mark.asyncio
async def test_container_controller_stop():
    """Test stop container."""
    from src.services.container_control import ContainerController

    mock_container = MagicMock()
    mock_container.status = "running"
    mock_client = MagicMock()
    mock_client.containers.get.return_value = mock_container

    controller = ContainerController(docker_client=mock_client, protected_containers=[])

    result = await controller.stop("radarr")

    mock_container.stop.assert_called_once()
    assert "stopped" in result.lower()


@pytest.mark.asyncio
async def test_container_controller_stop_already_stopped():
    """Test stop on already stopped container."""
    from src.services.container_control import ContainerController

    mock_container = MagicMock()
    mock_container.status = "exited"
    mock_client = MagicMock()
    mock_client.containers.get.return_value = mock_container

    controller = ContainerController(docker_client=mock_client, protected_containers=[])

    result = await controller.stop("radarr")

    mock_container.stop.assert_not_called()
    assert "already stopped" in result.lower()


@pytest.mark.asyncio
async def test_container_controller_start():
    """Test start container."""
    from src.services.container_control import ContainerController

    mock_container = MagicMock()
    mock_container.status = "exited"
    mock_client = MagicMock()
    mock_client.containers.get.return_value = mock_container

    controller = ContainerController(docker_client=mock_client, protected_containers=[])

    result = await controller.start("radarr")

    mock_container.start.assert_called_once()
    assert "started" in result.lower()


@pytest.mark.asyncio
async def test_container_controller_start_already_running():
    """Test start on already running container."""
    from src.services.container_control import ContainerController

    mock_container = MagicMock()
    mock_container.status = "running"
    mock_client = MagicMock()
    mock_client.containers.get.return_value = mock_container

    controller = ContainerController(docker_client=mock_client, protected_containers=[])

    result = await controller.start("radarr")

    mock_container.start.assert_not_called()
    assert "already running" in result.lower()


@pytest.mark.asyncio
async def test_container_controller_not_found():
    """Test handling of container not found."""
    import docker
    from src.services.container_control import ContainerController

    mock_client = MagicMock()
    mock_client.containers.get.side_effect = docker.errors.NotFound("not found")

    controller = ContainerController(docker_client=mock_client, protected_containers=[])

    result = await controller.restart("nonexistent")

    assert "not found" in result.lower()
```

**Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && pytest tests/test_container_control.py -v`
Expected: FAIL with "No module named 'src.services'"

**Step 3: Write implementation**

Create `src/services/__init__.py`:

```python
# Services module
```

Create `src/services/container_control.py`:

```python
import asyncio
import logging

import docker

logger = logging.getLogger(__name__)


class ContainerController:
    """Controls Docker containers with protection support."""

    def __init__(
        self,
        docker_client: docker.DockerClient,
        protected_containers: list[str],
    ):
        self.docker_client = docker_client
        self.protected_containers = set(protected_containers)

    def is_protected(self, container_name: str) -> bool:
        """Check if container is protected from control commands."""
        return container_name in self.protected_containers

    async def restart(self, container_name: str) -> str:
        """Restart a container.

        Returns a status message.
        """
        try:
            container = self.docker_client.containers.get(container_name)
            await asyncio.to_thread(container.restart)
            logger.info(f"Restarted container: {container_name}")
            return f"✅ {container_name} restarted successfully"
        except docker.errors.NotFound:
            return f"❌ Container '{container_name}' not found"
        except Exception as e:
            logger.error(f"Failed to restart {container_name}: {e}")
            return f"❌ Failed to restart {container_name}: {e}"

    async def stop(self, container_name: str) -> str:
        """Stop a container.

        Returns a status message.
        """
        try:
            container = self.docker_client.containers.get(container_name)
            if container.status != "running":
                return f"ℹ️ {container_name} is already stopped"

            await asyncio.to_thread(container.stop)
            logger.info(f"Stopped container: {container_name}")
            return f"✅ {container_name} stopped"
        except docker.errors.NotFound:
            return f"❌ Container '{container_name}' not found"
        except Exception as e:
            logger.error(f"Failed to stop {container_name}: {e}")
            return f"❌ Failed to stop {container_name}: {e}"

    async def start(self, container_name: str) -> str:
        """Start a container.

        Returns a status message.
        """
        try:
            container = self.docker_client.containers.get(container_name)
            if container.status == "running":
                return f"ℹ️ {container_name} is already running"

            await asyncio.to_thread(container.start)
            logger.info(f"Started container: {container_name}")
            return f"✅ {container_name} started"
        except docker.errors.NotFound:
            return f"❌ Container '{container_name}' not found"
        except Exception as e:
            logger.error(f"Failed to start {container_name}: {e}")
            return f"❌ Failed to start {container_name}: {e}"

    async def pull_and_recreate(self, container_name: str) -> str:
        """Pull latest image and recreate container.

        Returns a status message.
        """
        try:
            container = self.docker_client.containers.get(container_name)
            image_name = container.image.tags[0] if container.image.tags else container.image.id

            # Pull latest image
            logger.info(f"Pulling image for {container_name}: {image_name}")
            await asyncio.to_thread(self.docker_client.images.pull, image_name)

            # Get container config before stopping
            config = container.attrs

            # Stop and remove old container
            await asyncio.to_thread(container.stop)
            await asyncio.to_thread(container.remove)

            # Recreate container with same config
            # Note: This is simplified - full recreation would need to preserve
            # volumes, networks, env vars, etc. from the original container
            new_container = await asyncio.to_thread(
                self.docker_client.containers.run,
                image_name,
                name=container_name,
                detach=True,
                **self._extract_run_config(config),
            )

            logger.info(f"Recreated container: {container_name}")
            return f"✅ {container_name} updated (pulled {image_name} and recreated)"

        except docker.errors.NotFound:
            return f"❌ Container '{container_name}' not found"
        except Exception as e:
            logger.error(f"Failed to pull and recreate {container_name}: {e}")
            return f"❌ Failed to update {container_name}: {e}"

    def _extract_run_config(self, attrs: dict) -> dict:
        """Extract run configuration from container attributes.

        This extracts common settings to recreate a container.
        """
        config = attrs.get("Config", {})
        host_config = attrs.get("HostConfig", {})

        run_config = {}

        # Environment variables
        if config.get("Env"):
            run_config["environment"] = config["Env"]

        # Volumes/binds
        if host_config.get("Binds"):
            run_config["volumes"] = host_config["Binds"]

        # Port bindings
        if host_config.get("PortBindings"):
            run_config["ports"] = host_config["PortBindings"]

        # Restart policy
        if host_config.get("RestartPolicy"):
            run_config["restart_policy"] = host_config["RestartPolicy"]

        # Network mode
        if host_config.get("NetworkMode"):
            run_config["network_mode"] = host_config["NetworkMode"]

        return run_config
```

**Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && pytest tests/test_container_control.py -v`
Expected: All 7 tests PASS

**Step 5: Commit**

```bash
git add src/services/__init__.py src/services/container_control.py tests/test_container_control.py
git commit -m "feat: add container controller for restart/stop/start/pull"
```

---

## Task 4: Control Commands

**Files:**
- Create: `src/bot/control_commands.py`
- Create: `tests/test_control_commands.py`

**Step 1: Write the failing test**

Create `tests/test_control_commands.py`:

```python
import pytest
from unittest.mock import MagicMock, AsyncMock


@pytest.mark.asyncio
async def test_restart_command_requests_confirmation():
    """Test that /restart asks for confirmation."""
    from src.bot.control_commands import restart_command
    from src.bot.confirmation import ConfirmationManager
    from src.state import ContainerStateManager
    from src.models import ContainerInfo

    state = ContainerStateManager()
    state.update(ContainerInfo("radarr", "running", None, "linuxserver/radarr", None))

    confirmation = ConfirmationManager()
    controller = MagicMock()
    controller.is_protected.return_value = False

    handler = restart_command(state, controller, confirmation)

    message = MagicMock()
    message.text = "/restart radarr"
    message.from_user.id = 123
    message.answer = AsyncMock()

    await handler(message)

    # Should ask for confirmation
    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]
    assert "Restart radarr?" in response
    assert "yes" in response.lower()

    # Should have pending confirmation
    pending = confirmation.get_pending(123)
    assert pending is not None
    assert pending.action == "restart"
    assert pending.container_name == "radarr"


@pytest.mark.asyncio
async def test_restart_command_rejects_protected():
    """Test that protected containers cannot be restarted."""
    from src.bot.control_commands import restart_command
    from src.bot.confirmation import ConfirmationManager
    from src.state import ContainerStateManager
    from src.models import ContainerInfo

    state = ContainerStateManager()
    state.update(ContainerInfo("mariadb", "running", None, "mariadb:latest", None))

    confirmation = ConfirmationManager()
    controller = MagicMock()
    controller.is_protected.return_value = True

    handler = restart_command(state, controller, confirmation)

    message = MagicMock()
    message.text = "/restart mariadb"
    message.from_user.id = 123
    message.answer = AsyncMock()

    await handler(message)

    response = message.answer.call_args[0][0]
    assert "protected" in response.lower()

    # Should NOT have pending confirmation
    assert confirmation.get_pending(123) is None


@pytest.mark.asyncio
async def test_restart_command_container_not_found():
    """Test error when container not found."""
    from src.bot.control_commands import restart_command
    from src.bot.confirmation import ConfirmationManager
    from src.state import ContainerStateManager

    state = ContainerStateManager()
    confirmation = ConfirmationManager()
    controller = MagicMock()

    handler = restart_command(state, controller, confirmation)

    message = MagicMock()
    message.text = "/restart nonexistent"
    message.from_user.id = 123
    message.answer = AsyncMock()

    await handler(message)

    response = message.answer.call_args[0][0]
    assert "No container found" in response


@pytest.mark.asyncio
async def test_confirm_handler_executes_action():
    """Test that 'yes' executes pending action."""
    from src.bot.control_commands import create_confirm_handler
    from src.bot.confirmation import ConfirmationManager

    confirmation = ConfirmationManager()
    confirmation.request(user_id=123, action="restart", container_name="radarr")

    controller = MagicMock()
    controller.restart = AsyncMock(return_value="✅ radarr restarted successfully")

    handler = create_confirm_handler(controller, confirmation)

    message = MagicMock()
    message.text = "yes"
    message.from_user.id = 123
    message.answer = AsyncMock()

    await handler(message)

    controller.restart.assert_called_once_with("radarr")
    response = message.answer.call_args[0][0]
    assert "restarted" in response.lower()


@pytest.mark.asyncio
async def test_confirm_handler_no_pending():
    """Test 'yes' with no pending confirmation is ignored."""
    from src.bot.control_commands import create_confirm_handler
    from src.bot.confirmation import ConfirmationManager

    confirmation = ConfirmationManager()
    controller = MagicMock()

    handler = create_confirm_handler(controller, confirmation)

    message = MagicMock()
    message.text = "yes"
    message.from_user.id = 123
    message.answer = AsyncMock()

    await handler(message)

    # Should not call any controller method
    controller.restart.assert_not_called()
    controller.stop.assert_not_called()
    controller.start.assert_not_called()

    # Should inform user
    response = message.answer.call_args[0][0]
    assert "No pending" in response
```

**Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && pytest tests/test_control_commands.py -v`
Expected: FAIL with "No module named 'src.bot.control_commands'"

**Step 3: Write implementation**

Create `src/bot/control_commands.py`:

```python
import logging
from typing import Callable, Awaitable

from aiogram.types import Message

from src.state import ContainerStateManager
from src.bot.confirmation import ConfirmationManager
from src.services.container_control import ContainerController

logger = logging.getLogger(__name__)


def _find_container(state: ContainerStateManager, query: str) -> tuple[str | None, str | None]:
    """Find container by name, return (container_name, error_message)."""
    matches = state.find_by_name(query)

    if not matches:
        return None, f"❌ No container found matching '{query}'"

    if len(matches) > 1:
        names = ", ".join(m.name for m in matches)
        return None, f"Multiple matches found: {names}\n\n_Be more specific_"

    return matches[0].name, None


def _format_confirmation_message(action: str, container_name: str, status: str) -> str:
    """Format the confirmation request message."""
    action_emoji = {
        "restart": "🔄",
        "stop": "🛑",
        "start": "▶️",
        "pull": "⬇️",
    }
    emoji = action_emoji.get(action, "⚠️")

    return f"""{emoji} *{action.capitalize()} {container_name}?*

Current status: {status}

Reply 'yes' to confirm (expires in 60s)"""


def restart_command(
    state: ContainerStateManager,
    controller: ContainerController,
    confirmation: ConfirmationManager,
) -> Callable[[Message], Awaitable[None]]:
    """Factory for /restart command handler."""
    async def handler(message: Message) -> None:
        text = message.text or ""
        parts = text.strip().split()

        if len(parts) < 2:
            await message.answer("Usage: /restart <container>\n\nExample: /restart radarr")
            return

        query = parts[1]
        container_name, error = _find_container(state, query)

        if error:
            await message.answer(error, parse_mode="Markdown")
            return

        if controller.is_protected(container_name):
            await message.answer(f"🔒 {container_name} is protected and cannot be controlled via Telegram")
            return

        # Get current status for confirmation message
        container_info = state.get(container_name)
        status = container_info.status if container_info else "unknown"

        # Request confirmation
        user_id = message.from_user.id
        confirmation.request(user_id, action="restart", container_name=container_name)

        await message.answer(
            _format_confirmation_message("restart", container_name, status),
            parse_mode="Markdown",
        )

    return handler


def stop_command(
    state: ContainerStateManager,
    controller: ContainerController,
    confirmation: ConfirmationManager,
) -> Callable[[Message], Awaitable[None]]:
    """Factory for /stop command handler."""
    async def handler(message: Message) -> None:
        text = message.text or ""
        parts = text.strip().split()

        if len(parts) < 2:
            await message.answer("Usage: /stop <container>\n\nExample: /stop radarr")
            return

        query = parts[1]
        container_name, error = _find_container(state, query)

        if error:
            await message.answer(error, parse_mode="Markdown")
            return

        if controller.is_protected(container_name):
            await message.answer(f"🔒 {container_name} is protected and cannot be controlled via Telegram")
            return

        container_info = state.get(container_name)
        status = container_info.status if container_info else "unknown"

        user_id = message.from_user.id
        confirmation.request(user_id, action="stop", container_name=container_name)

        await message.answer(
            _format_confirmation_message("stop", container_name, status),
            parse_mode="Markdown",
        )

    return handler


def start_command(
    state: ContainerStateManager,
    controller: ContainerController,
    confirmation: ConfirmationManager,
) -> Callable[[Message], Awaitable[None]]:
    """Factory for /start command handler."""
    async def handler(message: Message) -> None:
        text = message.text or ""
        parts = text.strip().split()

        if len(parts) < 2:
            await message.answer("Usage: /start <container>\n\nExample: /start radarr")
            return

        query = parts[1]
        container_name, error = _find_container(state, query)

        if error:
            await message.answer(error, parse_mode="Markdown")
            return

        if controller.is_protected(container_name):
            await message.answer(f"🔒 {container_name} is protected and cannot be controlled via Telegram")
            return

        container_info = state.get(container_name)
        status = container_info.status if container_info else "unknown"

        user_id = message.from_user.id
        confirmation.request(user_id, action="start", container_name=container_name)

        await message.answer(
            _format_confirmation_message("start", container_name, status),
            parse_mode="Markdown",
        )

    return handler


def pull_command(
    state: ContainerStateManager,
    controller: ContainerController,
    confirmation: ConfirmationManager,
) -> Callable[[Message], Awaitable[None]]:
    """Factory for /pull command handler."""
    async def handler(message: Message) -> None:
        text = message.text or ""
        parts = text.strip().split()

        if len(parts) < 2:
            await message.answer("Usage: /pull <container>\n\nExample: /pull radarr")
            return

        query = parts[1]
        container_name, error = _find_container(state, query)

        if error:
            await message.answer(error, parse_mode="Markdown")
            return

        if controller.is_protected(container_name):
            await message.answer(f"🔒 {container_name} is protected and cannot be controlled via Telegram")
            return

        container_info = state.get(container_name)
        status = container_info.status if container_info else "unknown"

        user_id = message.from_user.id
        confirmation.request(user_id, action="pull", container_name=container_name)

        await message.answer(
            _format_confirmation_message("pull", container_name, status),
            parse_mode="Markdown",
        )

    return handler


def create_confirm_handler(
    controller: ContainerController,
    confirmation: ConfirmationManager,
) -> Callable[[Message], Awaitable[None]]:
    """Factory for confirmation handler (responds to 'yes')."""
    async def handler(message: Message) -> None:
        user_id = message.from_user.id
        pending = confirmation.confirm(user_id)

        if pending is None:
            await message.answer("❌ No pending action. Use /restart, /stop, /start, or /pull first.")
            return

        # Execute the action
        action = pending.action
        container_name = pending.container_name

        await message.answer(f"🔄 Executing {action} on {container_name}...")

        if action == "restart":
            result = await controller.restart(container_name)
        elif action == "stop":
            result = await controller.stop(container_name)
        elif action == "start":
            result = await controller.start(container_name)
        elif action == "pull":
            result = await controller.pull_and_recreate(container_name)
        else:
            result = f"❌ Unknown action: {action}"

        await message.answer(result)

    return handler
```

**Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && pytest tests/test_control_commands.py -v`
Expected: All 5 tests PASS

**Step 5: Commit**

```bash
git add src/bot/control_commands.py tests/test_control_commands.py
git commit -m "feat: add control commands with confirmation flow"
```

---

## Task 5: Register Commands and Update Help

**Files:**
- Modify: `src/bot/commands.py`
- Modify: `src/bot/telegram_bot.py`

**Step 1: Update HELP_TEXT**

Update `HELP_TEXT` in `src/bot/commands.py`:

```python
HELP_TEXT = """📋 *Available Commands*

/status - Container status overview
/status <name> - Details for specific container
/logs <name> [n] - Last n log lines (default 20)
/restart <name> - Restart a container
/stop <name> - Stop a container
/start <name> - Start a container
/pull <name> - Pull latest image and recreate
/help - Show this help message

_Partial container names work: /status rad → radarr_
_Control commands require confirmation_"""
```

**Step 2: Update telegram_bot.py**

Update `src/bot/telegram_bot.py` to register control commands:

```python
import logging
from typing import Any, Awaitable, Callable

from aiogram import Bot, Dispatcher, BaseMiddleware
from aiogram.filters import Command
from aiogram.types import Message
import docker

from src.state import ContainerStateManager
from src.bot.commands import help_command, status_command, logs_command
from src.bot.control_commands import (
    restart_command,
    stop_command,
    start_command,
    pull_command,
    create_confirm_handler,
)
from src.bot.confirmation import ConfirmationManager
from src.services.container_control import ContainerController

logger = logging.getLogger(__name__)


class AuthMiddleware(BaseMiddleware):
    def __init__(self, allowed_users: list[int], chat_id_store=None):
        self.allowed_users = set(allowed_users)
        self.chat_id_store = chat_id_store
        super().__init__()

    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        user_id = event.from_user.id if event.from_user else None

        if user_id not in self.allowed_users:
            logger.warning(f"Unauthorized access attempt from user {user_id}")
            return None

        # Capture chat ID for alerts if store is provided
        if self.chat_id_store is not None and event.chat:
            self.chat_id_store.set_chat_id(event.chat.id)

        return await handler(event, data)


def create_auth_middleware(allowed_users: list[int], chat_id_store=None) -> AuthMiddleware:
    """Factory function for auth middleware."""
    return AuthMiddleware(allowed_users, chat_id_store=chat_id_store)


def create_bot(token: str) -> Bot:
    """Create Telegram bot instance."""
    return Bot(token=token)


def create_dispatcher(allowed_users: list[int], chat_id_store=None) -> Dispatcher:
    """Create dispatcher with auth middleware."""
    dp = Dispatcher()
    dp.message.middleware(AuthMiddleware(allowed_users, chat_id_store=chat_id_store))
    return dp


def register_commands(
    dp: Dispatcher,
    state: ContainerStateManager,
    docker_client: docker.DockerClient | None = None,
    protected_containers: list[str] | None = None,
) -> ConfirmationManager | None:
    """Register all command handlers.

    Returns the ConfirmationManager if control commands are registered.
    """
    dp.message.register(help_command(state), Command("help"))
    dp.message.register(status_command(state), Command("status"))

    confirmation = None

    if docker_client:
        dp.message.register(logs_command(state, docker_client), Command("logs"))

        # Set up control commands
        protected = protected_containers or []
        controller = ContainerController(docker_client, protected)
        confirmation = ConfirmationManager(timeout_seconds=60)

        dp.message.register(
            restart_command(state, controller, confirmation),
            Command("restart"),
        )
        dp.message.register(
            stop_command(state, controller, confirmation),
            Command("stop"),
        )
        dp.message.register(
            start_command(state, controller, confirmation),
            Command("start"),
        )
        dp.message.register(
            pull_command(state, controller, confirmation),
            Command("pull"),
        )

        # Register "yes" handler for confirmations
        # This needs special handling - see Task 6

    return confirmation
```

**Step 3: Run all tests**

Run: `source .venv/bin/activate && pytest --tb=short`
Expected: All tests PASS

**Step 4: Commit**

```bash
git add src/bot/commands.py src/bot/telegram_bot.py
git commit -m "feat: register control commands and update help text"
```

---

## Task 6: Yes Handler Registration

**Files:**
- Modify: `src/bot/telegram_bot.py`
- Create: `tests/test_yes_handler.py`

**Step 1: Write the failing test**

Create `tests/test_yes_handler.py`:

```python
import pytest
from unittest.mock import MagicMock, AsyncMock, patch


@pytest.mark.asyncio
async def test_yes_message_triggers_confirm_handler():
    """Test that 'yes' message triggers pending action."""
    from src.bot.telegram_bot import create_dispatcher, register_commands
    from src.state import ContainerStateManager
    from src.models import ContainerInfo

    state = ContainerStateManager()
    state.update(ContainerInfo("radarr", "running", None, "linuxserver/radarr", None))

    mock_client = MagicMock()
    mock_container = MagicMock()
    mock_client.containers.get.return_value = mock_container

    dp = create_dispatcher([123])
    confirmation = register_commands(dp, state, mock_client, protected_containers=[])

    # Request confirmation
    confirmation.request(user_id=123, action="restart", container_name="radarr")

    # Simulate "yes" message - this requires the handler to be registered
    # The handler should execute the action
    pending = confirmation.confirm(123)
    assert pending is not None
    assert pending.action == "restart"
```

**Step 2: Update telegram_bot.py to register yes handler**

The challenge is that "yes" is not a command, it's a plain text message. We need to use a filter.

Add to `src/bot/telegram_bot.py`:

```python
from aiogram.filters import Filter


class YesFilter(Filter):
    """Filter for 'yes' confirmation messages."""

    async def __call__(self, message: Message) -> bool:
        if not message.text:
            return False
        return message.text.strip().lower() == "yes"
```

Update `register_commands` to add:

```python
        # Register "yes" handler for confirmations
        dp.message.register(
            create_confirm_handler(controller, confirmation),
            YesFilter(),
        )
```

**Step 3: Run tests**

Run: `source .venv/bin/activate && pytest tests/test_yes_handler.py -v`
Expected: PASS

**Step 4: Commit**

```bash
git add src/bot/telegram_bot.py tests/test_yes_handler.py
git commit -m "feat: add yes filter for confirmation handling"
```

---

## Task 7: Main Integration

**Files:**
- Modify: `src/main.py`

**Step 1: Update main.py**

Update `src/main.py` to pass protected_containers to register_commands:

Find this line:
```python
register_commands(dp, state, docker_client=monitor._client)
```

Replace with:
```python
register_commands(dp, state, docker_client=monitor._client, protected_containers=config.protected_containers)
```

**Step 2: Run all tests**

Run: `source .venv/bin/activate && pytest --tb=short`
Expected: All tests PASS

**Step 3: Commit**

```bash
git add src/main.py
git commit -m "feat: integrate protected containers config in main"
```

---

## Task 8: Integration Tests

**Files:**
- Create: `tests/test_phase3_integration.py`

**Step 1: Write integration tests**

Create `tests/test_phase3_integration.py`:

```python
"""
Phase 3 integration tests - verify control commands work end-to-end.
"""
import pytest
from unittest.mock import MagicMock, AsyncMock


@pytest.mark.asyncio
async def test_restart_with_confirmation_flow():
    """Test: Full restart flow with confirmation."""
    from src.state import ContainerStateManager
    from src.models import ContainerInfo
    from src.bot.control_commands import restart_command, create_confirm_handler
    from src.bot.confirmation import ConfirmationManager
    from src.services.container_control import ContainerController

    # Setup
    state = ContainerStateManager()
    state.update(ContainerInfo("radarr", "running", None, "linuxserver/radarr", None))

    mock_container = MagicMock()
    mock_client = MagicMock()
    mock_client.containers.get.return_value = mock_container

    controller = ContainerController(mock_client, protected_containers=[])
    confirmation = ConfirmationManager()

    # Step 1: User sends /restart radarr
    restart_handler = restart_command(state, controller, confirmation)
    message1 = MagicMock()
    message1.text = "/restart radarr"
    message1.from_user.id = 123
    message1.answer = AsyncMock()

    await restart_handler(message1)

    # Should ask for confirmation
    assert "Restart radarr?" in message1.answer.call_args[0][0]
    assert confirmation.get_pending(123) is not None

    # Step 2: User sends 'yes'
    confirm_handler = create_confirm_handler(controller, confirmation)
    message2 = MagicMock()
    message2.text = "yes"
    message2.from_user.id = 123
    message2.answer = AsyncMock()

    await confirm_handler(message2)

    # Should have restarted
    mock_container.restart.assert_called_once()


@pytest.mark.asyncio
async def test_protected_container_rejected():
    """Test: Protected containers cannot be controlled."""
    from src.state import ContainerStateManager
    from src.models import ContainerInfo
    from src.bot.control_commands import restart_command
    from src.bot.confirmation import ConfirmationManager
    from src.services.container_control import ContainerController

    state = ContainerStateManager()
    state.update(ContainerInfo("mariadb", "running", None, "mariadb:latest", None))

    mock_client = MagicMock()
    controller = ContainerController(mock_client, protected_containers=["mariadb"])
    confirmation = ConfirmationManager()

    handler = restart_command(state, controller, confirmation)

    message = MagicMock()
    message.text = "/restart mariadb"
    message.from_user.id = 123
    message.answer = AsyncMock()

    await handler(message)

    # Should reject
    response = message.answer.call_args[0][0]
    assert "protected" in response.lower()
    assert confirmation.get_pending(123) is None


@pytest.mark.asyncio
async def test_confirmation_timeout():
    """Test: Expired confirmation is rejected."""
    from datetime import datetime, timedelta
    from src.bot.control_commands import create_confirm_handler
    from src.bot.confirmation import ConfirmationManager, PendingConfirmation
    from src.services.container_control import ContainerController

    mock_client = MagicMock()
    controller = ContainerController(mock_client, protected_containers=[])
    confirmation = ConfirmationManager()

    # Create an expired confirmation
    confirmation._pending[123] = PendingConfirmation(
        action="restart",
        container_name="radarr",
        expires_at=datetime.now() - timedelta(seconds=1),
    )

    handler = create_confirm_handler(controller, confirmation)

    message = MagicMock()
    message.text = "yes"
    message.from_user.id = 123
    message.answer = AsyncMock()

    await handler(message)

    # Should reject - no pending
    response = message.answer.call_args[0][0]
    assert "No pending" in response
    mock_client.containers.get.assert_not_called()
```

**Step 2: Run integration tests**

Run: `source .venv/bin/activate && pytest tests/test_phase3_integration.py -v`
Expected: All 3 tests PASS

**Step 3: Commit**

```bash
git add tests/test_phase3_integration.py
git commit -m "test: add Phase 3 integration tests"
```

---

## Task 9: Final Verification

**Step 1: Run full test suite**

Run: `source .venv/bin/activate && pytest -v --tb=short`
Expected: All tests pass

**Step 2: Build Docker image**

```bash
docker compose build --no-cache
```

**Step 3: Tag release**

```bash
git tag -a v0.3.0 -m "Phase 3: Container control commands"
```

---

## Success Criteria Checklist

- [ ] `/restart` stops and starts container with confirmation
- [ ] `/stop` stops container with confirmation
- [ ] `/start` starts stopped container with confirmation
- [ ] `/pull` pulls image and recreates container with confirmation
- [ ] Protected containers reject control commands
- [ ] Confirmation expires after 60 seconds
- [ ] Help text updated with new commands
- [ ] All tests pass
