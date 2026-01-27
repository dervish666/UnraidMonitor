# UnraidMonitor

A Telegram bot for monitoring Docker containers on Unraid servers. Get real-time alerts, check container status, view logs, and control containers - all from Telegram.

## Features

- **Container Status** - Overview of all running/stopped containers
- **Resource Monitoring** - CPU/memory usage with threshold alerts
- **Log Watching** - Automatic alerts when errors appear in container logs
- **Crash Alerts** - Instant notifications when containers crash
- **AI Diagnostics** - Claude-powered log analysis for troubleshooting
- **Container Control** - Start, stop, restart, and pull containers remotely

## Commands

| Command | Description |
|---------|-------------|
| `/status` | Container status overview |
| `/status <name>` | Details for specific container |
| `/resources` | CPU/memory usage for all containers |
| `/resources <name>` | Detailed resource stats with thresholds |
| `/logs <name> [n]` | Last n log lines (default 20) |
| `/diagnose <name> [n]` | AI analysis of container logs |
| `/restart <name>` | Restart a container |
| `/stop <name>` | Stop a container |
| `/start <name>` | Start a container |
| `/pull <name>` | Pull latest image and recreate |
| `/help` | Show help message |

Partial container names work: `/status rad` matches `radarr`

## Quick Start

### 1. Create a Telegram Bot

1. Message [@BotFather](https://t.me/BotFather) on Telegram
2. Send `/newbot` and follow the prompts
3. Save the bot token

### 2. Get Your Telegram User ID

1. Message [@userinfobot](https://t.me/userinfobot) on Telegram
2. It will reply with your user ID

### 3. Configure Environment

Create a `.env` file:

```env
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_ALLOWED_USERS=123456789

# Optional: Enable AI diagnostics
ANTHROPIC_API_KEY=your_api_key_here
```

### 4. Configure Settings (Optional)

Create `config/config.yaml`:

```yaml
# Containers to ignore in status reports
ignored_containers:
  - some-temp-container

# Containers that cannot be controlled via Telegram
protected_containers:
  - mariadb
  - postgresql14

# Log watching configuration
log_watching:
  containers:
    - plex
    - radarr
    - sonarr
  error_patterns:
    - error
    - exception
    - fatal
  ignore_patterns:
    - DeprecationWarning
  cooldown_seconds: 900  # 15 minutes between alerts

# Resource monitoring
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
      cpu_percent: 90
      memory_percent: 90
    qbit:
      cpu_percent: 95
```

### 5. Run with Docker

```bash
docker run -d \
  --name unraid-monitor \
  --restart unless-stopped \
  -v /var/run/docker.sock:/var/run/docker.sock:ro \
  -v $(pwd)/.env:/app/.env:ro \
  -v $(pwd)/config:/app/config:ro \
  ghcr.io/dervish666/unraidmonitor:latest
```

Or with Docker Compose:

```yaml
version: '3.8'
services:
  unraid-monitor:
    image: ghcr.io/dervish666/unraidmonitor:latest
    container_name: unraid-monitor
    restart: unless-stopped
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - ./.env:/app/.env:ro
      - ./config:/app/config:ro
```

### 6. Run Locally (Development)

```bash
# Clone repository
git clone https://github.com/dervish666/UnraidMonitor.git
cd UnraidMonitor

# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run
python -m src.main
```

## Storage

All persistent data is stored in bind-mounted volumes:

```
/mnt/user/appdata/unraid-monitor/
├── config/
│   └── config.yaml          # Main configuration
└── data/
    ├── monitor.db           # Event history database
    ├── ignored_errors.json  # Ignore patterns
    ├── mutes.json           # Container mutes
    ├── server_mutes.json    # Server mutes
    └── array_mutes.json     # Array mutes
```

On first run, a default `config.yaml` is created automatically.

### First Run Setup

1. Create the appdata directory:
   ```bash
   mkdir -p /mnt/user/appdata/unraid-monitor/{config,data}
   ```

2. Start the container - it will create a default config

3. Edit `/mnt/user/appdata/unraid-monitor/config/config.yaml` to:
   - Add containers to watch
   - Configure memory management
   - Enable Unraid monitoring

4. Restart the container to apply changes

## Alert Examples

### Crash Alert
```
🔴 CONTAINER CRASHED: radarr

Exit code: 137 (OOM killed)
Image: linuxserver/radarr:latest
Uptime: 2h 34m

/status radarr - View details
/logs radarr - View recent logs
```

### Resource Alert
```
⚠️ HIGH MEMORY USAGE: plex

Memory: 92% (threshold: 85%)
        7.4GB / 8.0GB limit
Exceeded for: 3 minutes

CPU: 45% (normal)

Use /resources plex or /diagnose plex for details
```

### Log Error Alert
```
⚠️ ERRORS IN: sonarr

Found 3 errors in the last 15 minutes

Latest: Database connection failed: timeout

/logs sonarr 50 - View last 50 lines
```

## Requirements

- Python 3.11+
- Docker access (via socket)
- Telegram Bot Token
- (Optional) Anthropic API key for AI diagnostics

## License

MIT
