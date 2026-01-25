# Phase 2 Design: Alerts & Log Watching

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add automatic crash alerts and log error monitoring with rate-limited notifications.

**Architecture:** Extend existing event monitor to trigger alerts on crashes. Add log watcher for error pattern matching. Rate limit alerts to prevent spam.

**Tech Stack:** Python 3.11+, aiogram (existing), docker SDK (existing)

---

## Features

### 1. Automatic Crash Alerts

When a container crashes (exits with non-zero code), send immediate Telegram alert.

**Alert format:**
```
🔴 CONTAINER CRASHED: radarr

Exit code: 137 (OOM killed)
Image: linuxserver/radarr:latest
Uptime: 2h 34m

/status radarr - View details
/logs radarr - View recent logs
```

**Excluded from alerts:**
- Containers in `ignored_containers` config
- Normal stops (exit code 0)

### 2. Log Watching

Stream logs from critical containers, scan for error patterns.

**Default error patterns:** `error`, `exception`, `fatal`, `failed`, `critical`, `panic`, `traceback` (case-insensitive)

**Default watched containers:**
- plex, radarr, sonarr, lidarr, readarr, prowlarr
- qbit, sab, tautulli, overseerr
- mariadb, postgresql14, redis
- Brisbooks

**Rate limiting:** 15-minute cooldown per container. First error alerts immediately, subsequent errors suppressed until cooldown expires.

**Alert format:**
```
⚠️ ERRORS IN: radarr

Found 3 errors in the last 15 minutes

Latest: "Database connection failed: timeout"

/logs radarr 50 - View last 50 lines
```

### 3. New Command: /logs

```
/logs <name> [n] - Show last n lines of container logs (default 20)
```

Example: `/logs radarr 50`

---

## Architecture

```
Docker Events ──► Event Monitor ──► Crash? ──► Alert Manager ──► Telegram
                       │
                       ▼
                 State Manager
                       ▲
                       │
Container Logs ──► Log Watcher ──► Error? ──► Alert Manager ──► Telegram
                                      │
                                      ▼
                                Rate Limiter
                              (15 min cooldown)
```

### New Components

| Component | Location | Purpose |
|-----------|----------|---------|
| `AlertManager` | `src/alerts/manager.py` | Send alerts to Telegram, track chat ID |
| `LogWatcher` | `src/monitors/log_watcher.py` | Stream logs, pattern matching |
| `RateLimiter` | `src/alerts/rate_limiter.py` | Track cooldowns, prevent spam |

### Modified Components

| Component | Changes |
|-----------|---------|
| `DockerEventMonitor` | Trigger alert on crash events |
| `config.yaml` | Add `log_watching` section |
| `commands.py` | Add `/logs` command |

---

## Configuration

Updated `config/config.yaml`:

```yaml
monitoring:
  health_check_interval: 60

# Containers to ignore (won't trigger crash alerts)
ignored_containers:
  - Kometa

# Log watching configuration
log_watching:
  # Containers to watch (override defaults by setting this)
  containers:
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
    - Brisbooks

  # Error patterns to match (case-insensitive)
  error_patterns:
    - "error"
    - "exception"
    - "fatal"
    - "failed"
    - "critical"
    - "panic"
    - "traceback"

  # Patterns to ignore (skip lines matching these)
  ignore_patterns:
    - "DeprecationWarning"
    - "DEBUG"

  # Rate limit cooldown in seconds
  cooldown_seconds: 900  # 15 minutes
```

Config changes require restart to take effect.

---

## Error Handling

| Scenario | Behavior |
|----------|----------|
| Container removed while watching | Stop watching, no error |
| Container restarted | Reconnect log stream |
| Watched container doesn't exist | Skip silently, log warning |
| Telegram API fails | Retry 3x with backoff |
| No chat ID stored | Log warning, user must message bot first |
| Log stream disconnects | Reconnect after 5 seconds |
| Bot restarts | Rate limit state resets (acceptable) |

---

## Testing Strategy

### Unit Tests
- `AlertManager` - Message formatting, chat ID storage
- `LogWatcher` - Pattern matching, ignore patterns
- `RateLimiter` - Cooldown logic, reset after timeout

### Integration Tests
- Crash alert: Mock Docker event → Verify message sent
- Log error: Mock log stream → Verify alert triggered
- Rate limiting: Multiple errors → Only first alert sent

### Manual Testing
- `docker kill --signal=SIGKILL <name>` → Should get crash alert
- Trigger error in watched container → Should get log alert
- Multiple errors → Only one alert per 15 min

---

## Success Criteria

- [ ] Crash alerts sent when container exits with non-zero code
- [ ] Log errors detected and alerted with rate limiting
- [ ] `/logs` command shows container logs
- [ ] Ignored containers don't trigger alerts
- [ ] Config file controls watched containers and patterns
