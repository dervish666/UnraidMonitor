# Phase 1 MVP Design: Core Infrastructure + Basic Bot

## Goal

Build a minimal working system that proves Docker socket monitoring and Telegram bot integration work on Unraid, before investing in advanced features.

## Scope

**Included:**
- Docker event subscription (start, die, health_status)
- In-memory container state cache
- Telegram bot with `/status` and `/help` commands
- Security middleware (allowed users only)
- Partial container name matching

**Excluded (later phases):**
- Database/persistence
- Alerts on container crashes
- Claude API analysis
- Container actions (restart/stop)
- Log watching

## Project Structure

```
unraid-monitor-bot/
├── src/
│   ├── __init__.py
│   ├── main.py              # Async orchestrator
│   ├── config.py            # Pydantic settings
│   └── models.py            # ContainerInfo, Event dataclasses
├── src/monitors/
│   ├── __init__.py
│   └── docker_events.py     # Docker socket subscription
├── src/bot/
│   ├── __init__.py
│   ├── telegram_bot.py      # aiogram setup, middleware
│   └── commands.py          # /status, /help handlers
├── config/
│   └── config.yaml
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── pyproject.toml
```

## Configuration

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Yes | From @BotFather |
| `ANTHROPIC_API_KEY` | No | Enables Claude analysis (Phase 3) |
| `TELEGRAM_ALLOWED_USERS` | Yes | Comma-separated user IDs |

### config/config.yaml

```yaml
monitoring:
  health_check_interval: 60  # Used in Phase 2

ignored_containers:
  - Kometa
```

### Implementation

Pydantic `BaseSettings` loads environment variables automatically. YAML file loaded and merged for non-sensitive settings.

## Docker Event Monitor

### Approach

The `docker` Python SDK is synchronous. Run in `asyncio.to_thread()` to avoid blocking the Telegram bot.

### Container State Cache

```python
container_states: dict[str, ContainerInfo] = {}

@dataclass
class ContainerInfo:
    name: str
    status: str          # running, exited, paused
    health: str | None   # healthy, unhealthy, starting, None
    image: str
    started_at: datetime | None
```

### Startup Sequence

1. Connect to Docker socket (`unix:///var/run/docker.sock`)
2. Call `docker.containers.list(all=True)` to populate initial state
3. Subscribe to events, update cache on each event

### Events Captured

| Event | Action |
|-------|--------|
| `start` | Update status to "running" |
| `die` | Update status to "exited" |
| `health_status` | Update health field |

## Telegram Bot

### Framework

aiogram 3.x - async, actively maintained, good documentation.

### Security Middleware

Check `message.from_user.id` against allowed list. Unauthorized users receive no response (silent ignore).

### Commands

| Command | Response |
|---------|----------|
| `/status` | Summary: X running, Y stopped, Z unhealthy. Lists stopped/unhealthy containers. |
| `/status <name>` | Detailed info for matching container(s) |
| `/help` | List available commands |

### Partial Name Matching

`/status rad` matches `radarr`. Multiple matches prompt user to be more specific.

### Message Format Example

```
📊 Container Status

✅ Running: 58
🔴 Stopped: 2
⚠️ Unhealthy: 1

Stopped: Kometa, backup-job
Unhealthy: radarr

Use /status <name> for details
```

## Main Entry Point

### Startup

1. Load config (validate required settings)
2. Connect to Docker socket
3. Populate container state cache
4. Start Docker event monitor (background task)
5. Start Telegram bot polling

### Shutdown

Handle `SIGTERM`/`SIGINT`:
1. Stop Telegram bot
2. Cancel event monitor task
3. Close Docker client

### Error Handling

| Error | Behavior |
|-------|----------|
| Docker socket unavailable | Exit with error (cannot function) |
| Telegram API error | Retry with backoff, log |
| Event processing error | Log and continue |

## Dependencies

```
docker>=7.0.0
aiogram>=3.4.0
pyyaml>=6.0
pydantic>=2.0
pydantic-settings>=2.0
```

## Testing Strategy

1. **Local:** Test bot commands with mocked Docker client
2. **Unraid:** Deploy container, verify real container data appears

## Success Criteria

- [ ] Bot responds to `/status` with accurate container counts
- [ ] `/status <name>` shows details for specific container
- [ ] Unauthorized users get no response
- [ ] Container state updates when containers start/stop
