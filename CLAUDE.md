# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Unraid Server Monitor Bot - A Docker-based monitoring service for Unraid servers that:
- Monitors Docker container health and events via the Docker socket
- Parses container logs for errors/warnings with configurable filters
- Uses Claude API to analyze issues and suggest fixes
- Provides Telegram bot interface for alerts and interaction
- Executes approved actions (restart containers, etc.)

## Commands

```bash
# Run the application
python -m src.main

# Run tests
pytest tests/

# Run a single test file
pytest tests/test_<module>.py

# Run with coverage
pytest --cov=src tests/

# Type checking
mypy src/

# Linting
ruff check src/

# Build Docker image
docker build -t unraid-monitor-bot .

# Run with Docker Compose
docker-compose up -d
```

## Architecture

### Core Data Flow
```
Docker Socket → Event/Health/Log Monitors → Event Queue → Claude Analysis → Telegram Bot → User
                                                                                    ↓
                                                                          Docker Actions
```

### Key Components

**Monitors** (`src/monitors/`):
- `docker_events.py` - Subscribes to Docker events via socket (die, start, health_status, oom)
- `docker_health.py` - Periodic polling for container status, resource usage, restart counts
- `log_watcher.py` - Streams container logs with error pattern filtering and rate limiting

**Analysis** (`src/analysis/`):
- `claude_client.py` - Claude API integration with rate limiting and debouncing

**Bot** (`src/bot/`):
- `telegram_bot.py` - aiogram 3.x handlers with conversation context
- `commands.py` - Command implementations (/status, /logs, /restart, etc.)
- `formatters.py` - Alert message formatting with emoji and quick actions

**Actions** (`src/actions/`):
- `docker_actions.py` - Container control with safety features (confirmations, cooldowns, whitelists)

**Storage** (`src/storage/`):
- `database.py` - SQLite with aiosqlite for events, mutes, and audit log

### Async Pattern
All components use async/await - Docker events, Telegram, and Claude API are handled concurrently. The event queue coordinates between monitors and the analysis engine.

## Environment Variables

```bash
TELEGRAM_BOT_TOKEN=    # Required - from @BotFather
ANTHROPIC_API_KEY=     # Required - for Claude analysis
CONFIG_PATH=           # Optional - defaults to /app/config/config.yaml
DATABASE_PATH=         # Optional - defaults to /app/data/monitor.db
LOG_LEVEL=             # Optional - defaults to INFO
```

## Configuration

`config/config.yaml` controls:
- Telegram allowed users whitelist
- Watched/ignored containers
- Log error patterns and ignore patterns
- Claude API rate limits
- Alert cooldown periods

## Key Design Decisions

- Docker socket mounted read-only for monitoring, but actions require write access
- Graceful degradation: send basic alerts without analysis if Claude API unavailable
- Partial container name matching for commands (e.g., `/logs rad` matches `radarr`)
- Timestamps displayed in Europe/London timezone
- Deduplication and rate limiting to prevent alert spam
