# UnraidMonitor

A Telegram bot for monitoring Docker containers and Unraid servers. Get real-time alerts, check container status, view logs, and control containers - all from Telegram.

## Features

- **Interactive Setup Wizard** - Guided first-run setup via Telegram with auto-classification of containers
- **Container Monitoring** - Status, health checks, crash detection, and recovery notifications
- **Resource Alerts** - CPU/memory usage with configurable thresholds
- **Log Watching** - Automatic alerts when errors appear in container logs
- **AI Diagnostics** - LLM-powered log analysis and troubleshooting (Anthropic, OpenAI, or Ollama)
- **Smart Ignore Patterns** - AI-generated patterns to filter known errors, with interactive toggle selection
- **Multi-Provider LLM** - Switch between Anthropic Claude, OpenAI GPT, or local Ollama models at runtime
- **Container Control** - Start, stop, restart, and pull containers with inline confirmation buttons
- **Unraid Server Monitoring** - CPU/memory, temperatures, UPS status, and array health
- **Memory Pressure Management** - Automatic container priority handling during high memory
- **Mute System** - Temporarily silence alerts per container, server, or array
- **Natural Language Chat** - Ask questions naturally instead of using commands
- **Interactive Dashboard** - `/manage` hub for status, resources, ignores, and mutes
- **Sectioned Help** - `/help` with navigable category buttons instead of a text wall

## What's New in v0.9.0

This release overhauls the bot's UX with inline keyboard buttons throughout:

- **Button confirmations** - `/restart`, `/stop`, `/start`, `/pull` now show ✅ Confirm / ❌ Cancel buttons
- **Diagnose details button** - After AI diagnosis, tap 📋 More Details instead of typing
- **Toggle ignore selection** - `/ignore` shows ☐/☑ checkboxes per error with Select All
- **Manage with delete buttons** - Remove ignores and mutes with per-item 🗑 buttons
- **Recovery alerts** - Get notified when a crashed container comes back online
- **Sectioned /help** - Browse commands by category (Containers, Server, Alerts, Setup)
- **Back navigation** - All sub-views include ⬅️ Back buttons
- **Smarter mute display** - Shows "until tomorrow 14:30" instead of just a time

See the [changelog](CHANGELOG.md) for full details.

---

## Table of Contents

- [Installation](#installation)
  - [Unraid Community Apps](#unraid-community-apps-recommended)
  - [Docker on Unraid (Manual)](#docker-on-unraid-manual)
  - [Docker on Other Systems](#docker-on-other-systems)
- [Prerequisites](#prerequisites)
- [Configuration](#configuration)
- [Commands](#commands)
- [Alert Examples](#alert-examples)
- [User Guide](#user-guide)
- [Troubleshooting](#troubleshooting)

---

## Installation

### Unraid Community Apps (Recommended)

The easiest way to install on Unraid.

1. **Install from Community Apps**
   - Open the Unraid web UI
   - Go to **Apps** tab
   - Search for "Unraid Monitor Bot"
   - Click **Install**

2. **Configure the template**
   - `TELEGRAM_BOT_TOKEN` - Your bot token ([how to get one](#1-create-a-telegram-bot))
   - `TELEGRAM_ALLOWED_USERS` - Your Telegram user ID ([how to find it](#2-get-your-telegram-user-id))
   - `ANTHROPIC_API_KEY` (optional) - Enables AI features via Claude
   - `OPENAI_API_KEY` (optional) - Enables AI features via OpenAI
   - `OLLAMA_HOST` (optional) - Enables AI features via local Ollama (e.g., `http://192.168.1.100:11434`)
   - `DEFAULT_MODEL` (optional) - Override the default AI model (e.g., `qwen2.5:7b`, `gpt-4o`)
   - `UNRAID_API_KEY` (optional) - Enables server monitoring

3. **Start the container**

4. **Message your bot** on Telegram - send `/start` to begin the setup wizard
   - The wizard will guide you through connecting to your Unraid server
   - It auto-classifies your containers into categories (priority, protected, watched, killable, ignored)
   - When an Anthropic API key is configured, AI assists with classifying unknown containers
   - Review and adjust the categories, then confirm to save
   - The bot restarts automatically and begins monitoring

5. **Re-configure anytime** (optional)
   - Send `/setup` to re-run the wizard (merges non-destructively with existing config)
   - Or edit `/mnt/user/appdata/unraid-monitor/config/config.yaml` directly and restart

---

### Docker on Unraid (Manual)

If not using Community Apps, you can set it up manually.

#### Step 1: Create directories

```bash
mkdir -p /mnt/user/appdata/unraid-monitor/{config,data}
```

#### Step 2: Create the environment file

Create `/mnt/user/appdata/unraid-monitor/config/.env`:

```env
# Required
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_ALLOWED_USERS=123456789

# Optional - AI features (configure at least one for /diagnose, NL chat, smart ignore)
ANTHROPIC_API_KEY=your_anthropic_api_key_here
OPENAI_API_KEY=your_openai_api_key_here
OLLAMA_HOST=http://localhost:11434

# Optional - override the default AI model (e.g. qwen2.5:7b, gpt-4o)
DEFAULT_MODEL=

# Optional - enables Unraid server monitoring
UNRAID_API_KEY=your_unraid_api_key_here
```

#### Step 3: Add the container in Unraid

Go to **Docker** → **Add Container** and configure:

| Field | Value |
|-------|-------|
| Name | `unraid-monitor-bot` |
| Repository | `ghcr.io/dervish666/unraidmonitor:latest` |
| Network Type | `bridge` or your preferred network |

**Add these paths:**

| Container Path | Host Path | Access |
|----------------|-----------|--------|
| `/app/config` | `/mnt/user/appdata/unraid-monitor/config` | Read/Write |
| `/app/data` | `/mnt/user/appdata/unraid-monitor/data` | Read/Write |
| `/var/run/docker.sock` | `/var/run/docker.sock` | Read Only |

**Add these variables:**

| Name | Value |
|------|-------|
| `TELEGRAM_BOT_TOKEN` | Your bot token |
| `TELEGRAM_ALLOWED_USERS` | Your user ID |
| `ANTHROPIC_API_KEY` | (optional) Claude AI features |
| `OPENAI_API_KEY` | (optional) OpenAI AI features |
| `OLLAMA_HOST` | (optional) Ollama URL, e.g., `http://192.168.1.100:11434` |
| `DEFAULT_MODEL` | (optional) Override default model, e.g., `qwen2.5:7b` |
| `UNRAID_API_KEY` | (optional) Unraid server monitoring |
| `TZ` | Your timezone (e.g., `Europe/London`) |

#### Step 4: Start and verify

Start the container and check the logs for any errors. Message your bot on Telegram with `/start` to begin the interactive setup wizard.

---

## Prerequisites

### 1. Create a Telegram Bot

1. Open Telegram and message [@BotFather](https://t.me/BotFather)
2. Send `/newbot`
3. Follow the prompts to name your bot
4. Copy the **bot token** (looks like `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`)

### 2. Get Your Telegram User ID

1. Message [@userinfobot](https://t.me/userinfobot) on Telegram
2. It will reply with your numeric user ID (e.g., `123456789`)

This ID is used to restrict who can control your bot. You can add multiple IDs separated by commas: `123456789,987654321`

### 3. Configure an LLM Provider (Optional)

At least one provider is needed for AI-powered features (`/diagnose`, smart ignore patterns, natural language chat). You can configure multiple providers and switch between them at runtime with `/model`.

**Option A: Anthropic Claude** (recommended)
1. Sign up at [console.anthropic.com](https://console.anthropic.com)
2. Go to API Keys and create a new key
3. Add it as `ANTHROPIC_API_KEY`

**Option B: OpenAI**
1. Sign up at [platform.openai.com](https://platform.openai.com)
2. Go to API Keys and create a new key
3. Add it as `OPENAI_API_KEY`

**Option C: Ollama** (free, runs locally)
1. Install Ollama from [ollama.com](https://ollama.com)
2. Pull a model: `ollama pull llama3.1:8b`
3. Set `OLLAMA_HOST` to your Ollama URL (e.g., `http://192.168.1.100:11434`)

Models are auto-discovered from Ollama at startup. Note: some local models don't support tool calling, so NL chat actions (restart, etc.) may be limited.

### 4. Get an Unraid API Key (Optional)

Required for Unraid server monitoring (CPU, memory, temps, array status).

1. In Unraid web UI, go to **Settings** → **Management Access**
2. Generate an API key
3. Add it as `UNRAID_API_KEY`

---

## Configuration

Configuration is stored in `config/config.yaml`. On first run, the interactive setup wizard creates this file. You can also run `/setup` anytime to reconfigure.

**Location:**
- Unraid: `/mnt/user/appdata/unraid-monitor/config/config.yaml`
- Docker: `./config/config.yaml` (relative to project root)

### Essential Settings

```yaml
# Containers to watch for log errors
log_watching:
  containers:
    - plex
    - radarr
    - sonarr
    - lidarr
  error_patterns:
    - "error"
    - "exception"
    - "fatal"
    - "failed"
    - "critical"
  ignore_patterns:
    - "DeprecationWarning"
    - "DEBUG"
  cooldown_seconds: 900  # 15 min between alerts for same container

# Containers to hide from status reports
ignored_containers:
  - some-temp-container

# Containers that cannot be controlled via Telegram (safety)
protected_containers:
  - unraid-monitor-bot
  - mariadb
  - postgresql14
```

### Resource Monitoring

```yaml
resource_monitoring:
  enabled: true
  poll_interval_seconds: 60
  sustained_threshold_seconds: 120  # Alert after 2 min exceeded

  defaults:
    cpu_percent: 80
    memory_percent: 85

  # Per-container overrides
  containers:
    plex:
      cpu_percent: 95    # Plex often uses high CPU
      memory_percent: 90
    handbrake:
      cpu_percent: 100   # Expected to max out
```

### Memory Pressure Management

Automatically kills low-priority containers when system memory is critical.

```yaml
memory_management:
  enabled: false  # Disabled by default - enable with caution
  warning_threshold: 90      # Notify at this %
  critical_threshold: 95     # Start killing at this %
  safe_threshold: 80         # Offer restart when below this
  kill_delay_seconds: 60     # Warning before killing
  stabilization_wait: 180    # Wait between kills

  # Never kill these (highest priority)
  priority_containers:
    - plex
    - mariadb

  # Kill these in order during memory pressure (lowest priority first)
  killable_containers:
    - handbrake
    - tdarr
```

### Unraid Server Monitoring

```yaml
unraid:
  enabled: true
  host: "192.168.1.100"  # Your Unraid IP
  port: 443
  use_ssl: true
  verify_ssl: false  # Set true if using valid SSL cert

  polling:
    system: 30   # CPU/memory poll interval
    array: 300   # Array status poll interval
    ups: 60      # UPS status poll interval

  thresholds:
    cpu_temp: 80         # Alert above this temp (C)
    cpu_usage: 95        # Alert above this %
    memory_usage: 90     # Alert above this %
    disk_temp: 50        # Alert above this temp (C)
    array_usage: 85      # Alert above this %
    ups_battery: 30      # Alert below this %
```

---

## Commands

### Container Commands

| Command | Description |
|---------|-------------|
| `/status` | Overview of all containers |
| `/status <name>` | Details for a specific container |
| `/resources` | CPU/memory usage for all containers |
| `/resources <name>` | Detailed stats with thresholds |
| `/logs <name> [n]` | Last n log lines (default 20) |
| `/diagnose <name>` | AI log analysis with 📋 More Details button |
| `/restart <name>` | Restart with ✅ Confirm / ❌ Cancel buttons |
| `/stop <name>` | Stop with confirmation buttons |
| `/start <name>` | Start with confirmation buttons |
| `/pull <name>` | Pull latest image and recreate (with confirmation) |

**Tip:** Partial names work — `/status rad` matches `radarr`

### Unraid Server Commands

| Command | Description |
|---------|-------------|
| `/server` | Server overview (CPU, memory, temps) |
| `/server detailed` | Full metrics including per-core temps |
| `/array` | Array status and disk health |
| `/disks` | Detailed disk information |

### Alert Management

| Command | Description |
|---------|-------------|
| `/mute <name> <duration>` | Mute container (e.g., `/mute plex 2h`) |
| `/unmute <name>` | Unmute a container |
| `/mute-server <duration>` | Mute server alerts |
| `/unmute-server` | Unmute server alerts |
| `/mute-array <duration>` | Mute array alerts |
| `/unmute-array` | Unmute array alerts |
| `/mutes` | Show all active mutes |
| `/ignore` | Select errors to ignore with ☐/☑ toggle buttons |
| `/ignores` | List all ignore patterns |
| `/cancel-kill` | Cancel pending memory pressure kill |

**Duration formats:** `30m`, `2h`, `1d`, `1w`

### Setup & Management

| Command | Description |
|---------|-------------|
| `/setup` | Re-run the setup wizard (merges with existing config) |
| `/cancel` | Exit the setup wizard mid-flow |
| `/manage` | Interactive dashboard — status, resources, ignores, mutes |
| `/health` | Bot version, uptime, and monitor status |
| `/model` | Switch LLM provider and model at runtime |
| `/help` | Browse commands by category with navigation buttons |

### Natural Language Chat

Instead of commands, you can ask questions naturally:

- "What's wrong with plex?"
- "Why is my server slow?"
- "Is anything crashing?"
- "Show me radarr logs"
- "Restart sonarr" (shows confirmation buttons)

Follow-up questions work too — say "restart it" after discussing a container.

**Note:** Requires at least one LLM provider to be configured (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, or `OLLAMA_HOST`). Use `/model` to switch providers.

---

## Alert Examples

All alerts include tappable inline buttons for quick actions — no need to type commands.

### Crash Alert
```
🔴 CONTAINER CRASHED: radarr

Exit code: 137 (OOM killed)
Image: linuxserver/radarr:latest
Uptime: 2h 34m

[🔄 Restart] [📋 Logs] [🔍 Diagnose]
[🔕 Mute 1h] [🔕 Mute 24h]
```

### Recovery Alert

Sent automatically when a previously crashed container starts successfully:

```
✅ radarr recovered and is running again.
```

Recovery alerts include a 5-minute cooldown to prevent spam if a container is flapping.

### Restart Loop Alert
```
🔄🔴 RESTART LOOP: radarr

Crashed 5 times in the last 10 minutes!
Exit code: 137 (OOM killed)
Image: linuxserver/radarr:latest

[🔄 Restart] [📋 Logs] [🔍 Diagnose]
[🔕 Mute 1h] [🔕 Mute 24h]
```

### Resource Alert
```
⚠️ HIGH MEMORY USAGE: plex

Memory: 92% (threshold: 85%)
        7.4GB / 8.0GB limit
Exceeded for: 3 minutes

CPU: 45% (normal)

[📋 Logs] [🔍 Diagnose]
[🔕 Mute 1h] [🔕 Mute 24h]
```

### Log Error Alert
```
⚠️ ERRORS IN: sonarr

Found 3 errors in the last 15 minutes

Latest: Database connection failed: timeout

[🔇 Ignore Similar] [🔕 Mute 1h]
[📋 Logs] [🔍 Diagnose]
```

---

## User Guide

For a detailed walkthrough of all features, see the **[User Guide](docs/user-guide.md)**.

It covers:
- First-run setup and the interactive wizard
- Understanding each alert type and what to do
- Container management workflows (diagnose, restart, logs)
- Using the `/manage` dashboard
- Muting alerts and creating ignore patterns
- AI features and switching LLM providers
- Tips and best practices

---

## Troubleshooting

### Bot not responding

1. Check the container is running: `docker ps | grep unraid-monitor`
2. Check logs for errors: `docker logs unraid-monitor-bot`
3. Verify `TELEGRAM_BOT_TOKEN` is correct
4. Verify your user ID is in `TELEGRAM_ALLOWED_USERS`

### "Permission denied" errors

This means the container can't access the Docker socket.

1. Check your Docker socket GID:
   ```bash
   ls -ln /var/run/docker.sock
   ```
   Look at the 4th column (e.g., `281` on Unraid, `999` on Ubuntu)

2. If using docker-compose, set DOCKER_GID in `.env`:
   ```bash
   echo "DOCKER_GID=999" > .env
   ```

3. Rebuild the container:
   ```bash
   docker-compose build --no-cache
   docker-compose up -d
   ```

4. **Last resort:** Edit `docker-compose.yml` and uncomment `user: root`

### AI features not working

- Verify at least one LLM key is set: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, or `OLLAMA_HOST`
- Check logs for API errors
- Use `/model` to see which providers are configured and switch between them
- If using Ollama, ensure the server is reachable and has models pulled
- The bot works without AI — you'll get basic alerts, but `/diagnose` and natural language chat won't work

### Unraid monitoring not working

- Verify `UNRAID_API_KEY` is set
- Check the `unraid` section in `config.yaml` has correct `host` and `port`
- If using self-signed certs, set `verify_ssl: false`

### Container not starting

Check logs immediately after start:
```bash
docker logs unraid-monitor-bot
```

Common issues:
- Missing `TELEGRAM_BOT_TOKEN` or `TELEGRAM_ALLOWED_USERS`
- Invalid configuration in `config.yaml`
- Docker socket permission issues (see above)

### Changes to config.yaml not applying

Restart the container after editing config:
```bash
docker restart unraid-monitor-bot
```

---

## Data Storage

All persistent data is stored in mounted volumes:

```
config/
├── config.yaml           # Main configuration
└── .env                  # Environment variables (secrets)

data/
├── ignored_errors.json   # Ignore patterns
├── mutes.json            # Container mutes
├── server_mutes.json     # Server mutes
├── array_mutes.json      # Array mutes
└── model_selection.json  # Active LLM provider/model choice
```

---

## Requirements

- Docker
- Telegram Bot Token
- (Optional) LLM provider for AI features: Anthropic API key, OpenAI API key, or Ollama instance
- (Optional) Unraid API key for server monitoring

---

## License

MIT
