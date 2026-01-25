# Phase 3 Design: Container Control

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:writing-plans to create the implementation plan.

**Goal:** Add container control commands with confirmation and protected container support.

**Architecture:** New control commands with confirmation flow. ContainerController service handles Docker operations. Protected containers configured via YAML.

**Tech Stack:** Python 3.11+, aiogram (existing), docker SDK (existing)

---

## Commands

| Command | Action | Confirmation |
|---------|--------|--------------|
| `/restart <name>` | Stop + start container | Required |
| `/stop <name>` | Stop container | Required |
| `/start <name>` | Start stopped container | Required |
| `/pull <name>` | Pull image, stop, recreate, start | Required |

---

## Confirmation Flow

```
User: /restart radarr
Bot:  ⚠️ Restart radarr?
      Current status: running (uptime 2d 4h)

      Reply 'yes' to confirm (expires in 60s)

User: yes
Bot:  🔄 Restarting radarr...
Bot:  ✅ radarr restarted successfully
```

- Confirmation expires after 60 seconds
- Only the user who initiated can confirm
- One pending confirmation per user at a time

---

## Configuration

Updated `config/config.yaml`:

```yaml
# Containers that cannot be controlled via Telegram
protected_containers:
  - unraid-monitor-bot  # Don't let bot stop itself!
```

---

## Response Messages

### Success

```
✅ radarr restarted successfully
✅ radarr stopped
✅ radarr started
✅ radarr updated (pulled linuxserver/radarr:latest and recreated)
```

### Errors

| Scenario | Message |
|----------|---------|
| Not found | ❌ No container found matching 'xyz' |
| Protected | 🔒 mariadb is protected and cannot be controlled via Telegram |
| Already stopped | ℹ️ radarr is already stopped |
| Already running | ℹ️ radarr is already running |
| Confirmation timeout | (silent - user must re-run command) |
| Docker error | ❌ Failed to restart radarr: connection refused |
| No confirmation pending | ❌ No pending action. Use /restart, /stop, /start, or /pull first |

---

## Architecture

### New Components

| Component | Location | Purpose |
|-----------|----------|---------|
| `ContainerController` | `src/services/container_control.py` | Docker operations |
| `ConfirmationManager` | `src/bot/confirmation.py` | Track pending confirmations |
| Control commands | `src/bot/control_commands.py` | Command handlers |

### ContainerController Methods

```python
class ContainerController:
    def __init__(self, docker_client, protected_containers):
        ...

    async def restart(self, name: str) -> str:
        """Restart container. Returns success/error message."""

    async def stop(self, name: str) -> str:
        """Stop container. Returns success/error message."""

    async def start(self, name: str) -> str:
        """Start container. Returns success/error message."""

    async def pull_and_recreate(self, name: str) -> str:
        """Pull latest image, recreate container. Returns success/error message."""

    def is_protected(self, name: str) -> bool:
        """Check if container is protected."""
```

### ConfirmationManager

```python
@dataclass
class PendingConfirmation:
    action: str  # "restart", "stop", "start", "pull"
    container_name: str
    expires_at: datetime

class ConfirmationManager:
    def __init__(self, timeout_seconds: int = 60):
        self._pending: dict[int, PendingConfirmation] = {}  # user_id -> pending

    def request(self, user_id: int, action: str, container: str) -> None:
        """Store pending confirmation for user."""

    def confirm(self, user_id: int) -> PendingConfirmation | None:
        """Get and clear pending confirmation if valid."""

    def cancel(self, user_id: int) -> bool:
        """Cancel pending confirmation."""
```

---

## Updated Help Text

```
📋 *Available Commands*

/status - Container status overview
/status <name> - Details for specific container
/logs <name> [n] - Last n log lines (default 20)
/restart <name> - Restart a container
/stop <name> - Stop a container
/start <name> - Start a container
/pull <name> - Pull latest image and recreate
/help - Show this help message

_Partial container names work: /status rad → radarr_
_Control commands require confirmation_
```

---

## Security Considerations

1. Only authorized users can issue commands (existing auth middleware)
2. Protected containers cannot be controlled
3. Confirmation prevents accidental actions
4. Bot should protect itself (add to protected by default)
5. Timeout prevents stale confirmations

---

## Success Criteria

- [ ] `/restart` stops and starts container with confirmation
- [ ] `/stop` stops container with confirmation
- [ ] `/start` starts stopped container with confirmation
- [ ] `/pull` pulls image and recreates container with confirmation
- [ ] Protected containers reject control commands
- [ ] Confirmation expires after 60 seconds
- [ ] Help text updated with new commands
- [ ] All tests pass
