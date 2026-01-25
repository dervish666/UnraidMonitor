# Unraid Server Monitor Bot - Implementation Plan

## Project Overview

Build a Docker-based monitoring service for an Unraid server that:
1. Monitors Docker container health and events
2. Parses and filters logs for errors/warnings
3. Uses Claude API to analyse issues and suggest fixes
4. Provides a Telegram bot interface for alerts and interaction
5. Can execute approved actions (restart containers, etc.)

## Target Environment

- **Server**: Unraid 7.2.0, Intel i7-10700T, 32GB RAM
- **Existing Services**: ~60 Docker containers including Plex, *arr stack, various databases
- **Network**: Custom Docker network `docknet` at 192.168.0.190
- **Existing Tools**: syslog-ng, Apprise, InfluxDB (available for metrics)

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        UNRAID SERVER                            │
│                                                                 │
│  ┌──────────────┐    ┌─────────────────────────────────────┐   │
│  │ Docker       │    │  unraid-monitor-bot (new container) │   │
│  │ Containers   │    │                                     │   │
│  │ (60+)        │───▶│  ┌─────────────┐  ┌──────────────┐  │   │
│  └──────────────┘    │  │ Event       │  │ Log Watcher  │  │   │
│         │            │  │ Monitor     │  │ (filtered)   │  │   │
│         │            │  └──────┬──────┘  └──────┬───────┘  │   │
│         ▼            │         │                │          │   │
│  ┌──────────────┐    │         ▼                ▼          │   │
│  │ Docker       │    │  ┌────────────────────────────┐     │   │
│  │ Socket       │───▶│  │     Event Queue            │     │   │
│  │ /var/run/    │    │  │     (in-memory/SQLite)     │     │   │
│  │ docker.sock  │    │  └─────────────┬──────────────┘     │   │
│  └──────────────┘    │                │                    │   │
│                      │                ▼                    │   │
│                      │  ┌────────────────────────────┐     │   │
│                      │  │     Claude API Client      │     │   │
│                      │  │     (analysis engine)      │     │   │
│                      │  └─────────────┬──────────────┘     │   │
│                      │                │                    │   │
│                      │                ▼                    │   │
│                      │  ┌────────────────────────────┐     │   │
│                      │  │     Telegram Bot           │◀───┼───┼──▶ You
│                      │  │     (aiogram)              │     │   │
│                      │  └────────────────────────────┘     │   │
│                      │                                     │   │
│                      └─────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

---

## Tech Stack

- **Language**: Python 3.11+
- **Docker SDK**: `docker` (official Python SDK)
- **Telegram**: `aiogram` (async Telegram bot framework)
- **Claude API**: `anthropic` (official SDK)
- **Database**: SQLite (for event history, state persistence)
- **Async**: `asyncio` for concurrent event handling
- **Config**: Environment variables + YAML config file

---

## Project Structure

```
unraid-monitor-bot/
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── config/
│   └── config.yaml           # Container watchlist, alert thresholds
├── src/
│   ├── __init__.py
│   ├── main.py               # Entry point, orchestration
│   ├── config.py             # Configuration loader
│   ├── models.py             # Data models (Event, Alert, etc.)
│   ├── monitors/
│   │   ├── __init__.py
│   │   ├── docker_events.py  # Docker event subscription
│   │   ├── docker_health.py  # Periodic health checks
│   │   └── log_watcher.py    # Log parsing and filtering
│   ├── analysis/
│   │   ├── __init__.py
│   │   └── claude_client.py  # Claude API integration
│   ├── bot/
│   │   ├── __init__.py
│   │   ├── telegram_bot.py   # Telegram bot handlers
│   │   ├── commands.py       # Command implementations
│   │   └── formatters.py     # Message formatting
│   ├── actions/
│   │   ├── __init__.py
│   │   └── docker_actions.py # Container restart, etc.
│   └── storage/
│       ├── __init__.py
│       └── database.py       # SQLite for history/state
└── tests/
    └── ...
```

---

## Component Specifications

### 1. Docker Event Monitor (`monitors/docker_events.py`)

**Purpose**: Subscribe to Docker events in real-time

**Key Events to Capture**:
- `container.die` - Container stopped/crashed
- `container.start` - Container started
- `container.health_status` - Health check changes
- `container.oom` - Out of memory kills

**Implementation Notes**:
```python
# Connect to Docker socket
client = docker.DockerClient(base_url='unix://var/run/docker.sock')

# Subscribe to events (async generator)
for event in client.events(decode=True, filters={'type': 'container'}):
    # Process event
```

**Output**: Structured event objects pushed to event queue

---

### 2. Docker Health Monitor (`monitors/docker_health.py`)

**Purpose**: Periodic polling for container status

**Checks**:
- Container running state
- Resource usage (CPU, memory) via `docker stats`
- Restart count (detect restart loops)
- Health check status

**Frequency**: Every 60 seconds (configurable)

**Alert Conditions**:
- Container not running that should be
- Memory usage > 90% of limit
- Restart count increased
- Health status unhealthy

---

### 3. Log Watcher (`monitors/log_watcher.py`)

**Purpose**: Monitor container logs for errors

**Approach**:
- Use `docker logs --follow --tail 0` for real-time streaming
- Filter lines containing error patterns
- Rate-limit to avoid spam (max N errors per container per minute)

**Error Patterns** (configurable):
```yaml
error_patterns:
  - "error"
  - "exception"
  - "fatal"
  - "failed"
  - "critical"
  - "panic"
  - "traceback"
  
ignore_patterns:
  - "DeprecationWarning"
  - "rate limit"  # Often noisy
```

**Containers to Watch** (priority list from Sam's setup):
- plex
- radarr, sonarr, lidarr, readarr
- prowlarr
- qbit
- tautulli
- overseerr
- All databases (mariadb, postgresql14, redis, CouchDB)

---

### 4. Claude Analysis Client (`analysis/claude_client.py`)

**Purpose**: Analyse events and provide actionable insights

**API Configuration**:
- Model: `claude-sonnet-4-20250514` (good balance of speed/cost/quality)
- Max tokens: 1024 (keep responses concise)
- Temperature: 0.3 (more deterministic for technical analysis)

**System Prompt**:
```
You are a server monitoring assistant for an Unraid home server running ~60 Docker containers (media server stack including Plex, *arr apps, databases).

When analysing errors or events:
1. Identify the likely root cause
2. Assess severity (critical/warning/info)
3. Suggest specific remediation steps
4. Note if this might affect other services

Keep responses concise and actionable. Use technical language appropriate for a home lab administrator.

If you can suggest a Docker command to fix the issue, provide it.
```

**Context to Include**:
- Container name and image
- Event type (crash, health fail, error log)
- Recent logs (last 50 lines, truncated)
- Resource usage if relevant
- Recent restart count

**Rate Limiting**:
- Debounce similar events (don't analyse same error repeatedly)
- Max 10 API calls per hour (configurable)
- Queue events during rate limit, summarise when limit resets

---

### 5. Telegram Bot (`bot/telegram_bot.py`)

**Framework**: aiogram 3.x (async, modern)

**Commands**:

| Command | Description |
|---------|-------------|
| `/status` | Overview of all containers (running/stopped counts) |
| `/status <name>` | Detailed status of specific container |
| `/logs <name> [n]` | Last n lines of container logs (default 20) |
| `/restart <name>` | Restart a container (with confirmation) |
| `/start <name>` | Start a stopped container |
| `/stop <name>` | Stop a container (with confirmation) |
| `/errors` | Recent errors from last hour |
| `/analyse <text>` | Ask Claude to analyse provided text/error |
| `/mute <name> [duration]` | Mute alerts for container (default 1h) |
| `/unmute <name>` | Unmute alerts |
| `/help` | Show available commands |

**Alert Format**:
```
🔴 CONTAINER CRASH: radarr

Container 'radarr' exited unexpectedly (exit code 137)

📊 Analysis:
Exit code 137 indicates the container was killed by the OOM killer. 
The container was using 482MB of memory. This typically happens when:
- A large library scan is running
- Too many concurrent API requests

💡 Suggested Actions:
1. Restart with increased memory limit
2. Check for ongoing library scans
3. Review recent API activity in logs

🔧 Quick Actions:
/restart radarr - Restart the container
/logs radarr 50 - View last 50 log lines
/mute radarr 1h - Mute alerts for 1 hour
```

**Conversation Context**:
- Maintain short conversation history for follow-ups
- Allow natural language queries: "what's wrong with plex?"
- Pass to Claude for interpretation if not a recognised command

---

### 6. Docker Actions (`actions/docker_actions.py`)

**Available Actions**:
- `restart_container(name)` - Restart a container
- `start_container(name)` - Start a stopped container
- `stop_container(name)` - Stop a running container
- `get_logs(name, lines)` - Retrieve recent logs
- `get_stats(name)` - Get resource usage

**Safety Features**:
- Confirmation required for destructive actions (stop)
- Cooldown period after restart (prevent restart loops)
- Whitelist of containers that can be managed (configurable)
- Audit log of all actions taken

---

### 7. Storage (`storage/database.py`)

**SQLite Tables**:

```sql
-- Event history
CREATE TABLE events (
    id INTEGER PRIMARY KEY,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    container_name TEXT,
    event_type TEXT,
    severity TEXT,
    message TEXT,
    analysis TEXT,
    resolved BOOLEAN DEFAULT FALSE
);

-- Alert mutes
CREATE TABLE mutes (
    container_name TEXT PRIMARY KEY,
    muted_until DATETIME
);

-- Action audit log
CREATE TABLE actions (
    id INTEGER PRIMARY KEY,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    action_type TEXT,
    container_name TEXT,
    triggered_by TEXT,
    result TEXT
);
```

---

## Configuration File (`config/config.yaml`)

```yaml
# Telegram settings
telegram:
  allowed_users:
    - 123456789  # Your Telegram user ID

# Monitoring settings
monitoring:
  health_check_interval: 60  # seconds
  log_tail_lines: 100
  max_errors_per_minute: 5  # per container

# Containers to actively monitor (empty = all)
watched_containers:
  - plex
  - radarr
  - sonarr
  - lidarr
  - readarr
  - prowlarr
  - qbit
  - sab
  - tautulli
  - overseerr
  - mariadb
  - postgresql14
  - redis
  - paperless

# Containers to ignore
ignored_containers:
  - Kometa  # Runs on schedule, often "stopped"

# Error patterns
log_filters:
  error_patterns:
    - "error"
    - "exception"
    - "fatal"
    - "failed"
    - "critical"
  ignore_patterns:
    - "DeprecationWarning"
    - "DEBUG"

# Claude API settings
claude:
  model: "claude-sonnet-4-20250514"
  max_tokens: 1024
  rate_limit_per_hour: 20

# Alert settings
alerts:
  cooldown_seconds: 300  # Min time between alerts for same issue
  include_analysis: true
```

---

## Environment Variables

```bash
# Required
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
ANTHROPIC_API_KEY=your_anthropic_api_key

# Optional
CONFIG_PATH=/app/config/config.yaml
DATABASE_PATH=/app/data/monitor.db
LOG_LEVEL=INFO
```

---

## Docker Compose Configuration

```yaml
version: '3.8'

services:
  unraid-monitor-bot:
    build: .
    container_name: unraid-monitor-bot
    restart: unless-stopped
    environment:
      - TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
      - TZ=Europe/London
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - ./config:/app/config:ro
      - ./data:/app/data
    networks:
      - docknet

networks:
  docknet:
    external: true
```

---

## Dockerfile

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY src/ ./src/
COPY config/ ./config/

# Create data directory
RUN mkdir -p /app/data

# Run as non-root
RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

CMD ["python", "-m", "src.main"]
```

---

## Requirements (`requirements.txt`)

```
docker>=7.0.0
aiogram>=3.4.0
anthropic>=0.18.0
pyyaml>=6.0
aiosqlite>=0.19.0
python-dateutil>=2.8.0
```

---

## Implementation Order

### Phase 1: Core Infrastructure
1. Set up project structure
2. Implement configuration loading
3. Create database schema and basic operations
4. Build Docker event monitor (basic events)
5. Create minimal Telegram bot with `/status` command

### Phase 2: Monitoring
6. Implement health check polling
7. Add log watching with filtering
8. Build event queue and deduplication

### Phase 3: Intelligence
9. Integrate Claude API client
10. Add analysis to alerts
11. Implement conversational queries

### Phase 4: Actions
12. Add container control commands
13. Implement safety confirmations
14. Add mute/unmute functionality

### Phase 5: Polish
15. Improve error handling and resilience
16. Add comprehensive logging
17. Write tests for critical paths
18. Documentation

---

## Telegram Bot Setup Instructions

1. Message @BotFather on Telegram
2. Send `/newbot`
3. Choose a name (e.g., "Unraid Monitor")
4. Choose a username (e.g., "unraid_monitor_bot")
5. Save the token provided
6. Send `/setcommands` to BotFather and set:
   ```
   status - Container status overview
   logs - View container logs
   restart - Restart a container
   errors - Recent errors
   analyse - Analyse an error
   mute - Mute alerts for a container
   help - Show help
   ```
7. Get your user ID by messaging @userinfobot
8. Add your user ID to the config whitelist

---

## API Cost Estimation

Based on typical home server activity:

- **Events per day**: ~10-50 (crashes, errors, health changes)
- **Tokens per analysis**: ~500 input + ~500 output = 1000 tokens
- **Claude Sonnet pricing**: ~$3/million input, ~$15/million output

**Estimated monthly cost**: 
- 50 events/day × 30 days = 1,500 analyses
- ~750K input tokens = ~$2.25
- ~750K output tokens = ~$11.25
- **Total: ~$13.50/month** (likely less with deduplication)

---

## Testing Approach

1. **Unit tests**: Config parsing, message formatting, database operations
2. **Integration tests**: Docker SDK interactions (mock socket)
3. **Manual testing**: Deploy to Unraid, intentionally crash containers

**Test Scenarios**:
- Container crash (kill a test container)
- OOM condition (set low memory limit)
- Health check failure
- Log error patterns
- Telegram command responses
- Rate limiting behaviour

---

## Future Enhancements (Out of Scope for V1)

- Web dashboard alongside Telegram
- InfluxDB metrics integration for trends
- Automated remediation (auto-restart with limits)
- Integration with Unraid notifications API
- Multi-server support
- Custom alert rules (e.g., "alert if plex down for >5 min")

---

## Notes for Implementation

1. **Docker Socket Security**: The socket is mounted read-only where possible, but actions require write access. Consider security implications.

2. **Async Throughout**: Use async/await consistently - Docker events, Telegram, and Claude API all benefit from async handling.

3. **Graceful Degradation**: If Claude API is unavailable, still send basic alerts without analysis.

4. **Telegram Rate Limits**: Telegram has rate limits (~30 messages/second). Batch alerts if many containers fail simultaneously.

5. **Container Name Matching**: Support partial matching for commands (e.g., `/logs rad` matches `radarr`).

6. **Time Zones**: Sam is in UK (Europe/London) - ensure timestamps are displayed in local time.
