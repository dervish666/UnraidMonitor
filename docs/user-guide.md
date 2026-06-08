# UnraidMonitor User Guide

This guide walks you through everything the bot can do, from first-run setup to advanced features.

---

## Table of Contents

- [Getting Started](#getting-started)
- [Understanding Alerts](#understanding-alerts)
- [Container Management](#container-management)
- [Unraid Server Monitoring](#unraid-server-monitoring)
- [Alert Control](#alert-control)
- [AI Features](#ai-features)
- [Optional Features](#optional-features)
- [Tips & Best Practices](#tips--best-practices)

---

## Getting Started

### First Run

After installing the bot container (see [README](../README.md#installation) for installation steps), message your bot on Telegram and send `/start`.

The **setup wizard** will guide you through:

1. **Unraid connection** — Enter your Unraid server IP. The wizard auto-detects whether to use HTTPS or HTTP and tests the connection.

2. **Container classification** — The wizard scans your running Docker containers and sorts them into categories:
   - **Priority** — Critical containers that should never be killed during memory pressure (e.g., databases)
   - **Protected** — Cannot be controlled via Telegram commands (safety net)
   - **Watched** — Logs are actively monitored for errors
   - **Killable** — Can be killed during memory pressure events
   - **Ignored** — Hidden from status reports

   If you have an AI provider configured, unknown containers get classified automatically. Otherwise, they default to sensible categories based on name patterns (e.g., `mariadb` is auto-classified as priority).

3. **Review & adjust** — Toggle containers between categories using inline buttons, then confirm. The bot saves `config.yaml` and restarts itself.

### After Setup

Once running, the bot immediately begins:
- Watching Docker events (crashes, starts, stops)
- Streaming logs from watched containers for error detection
- Polling resource usage (CPU/memory) at configured intervals
- Monitoring Unraid server health (if configured)

You'll receive a startup notification confirming how many containers are being watched.

### Re-Running Setup

Send `/setup` anytime to reconfigure. It merges non-destructively with your existing config — thresholds, Unraid connection details, and custom settings are preserved.

---

## Understanding Alerts

The bot sends different types of alerts, each with tappable inline buttons so you can respond instantly.

### Crash Alerts

**What it looks like:**
```
🔴 CONTAINER CRASHED: radarr

Exit code: 137 (OOM killed)
Image: linuxserver/radarr:latest
Uptime: 2h 34m
```

**What it means:** A container stopped unexpectedly. The exit code tells you why:
- **137** — OOM killed (ran out of memory)
- **143** — Received SIGTERM (graceful shutdown signal)
- **139** — Segmentation fault (crash in the application)
- **1** — General application error

**What to do:** Tap the buttons below the alert:
- **🔄 Restart** — Restart the container immediately
- **📋 Logs** — View recent log output to see what happened
- **🔍 Diagnose** — Get an AI-powered analysis of what went wrong
- **🔕 Mute 1h / 24h** — Silence alerts for this container temporarily

### Recovery Alerts

**What it looks like:**
```
✅ radarr recovered and is running again.
```

**What it means:** A container that previously crashed has started successfully. This closes the loop — you know the problem resolved itself (or your restart worked).

Recovery alerts have a 5-minute cooldown to prevent spam if a container is flapping between crashed and running states.

### Restart Loop Alerts

**What it looks like:**
```
🔄🔴 RESTART LOOP: radarr

Crashed 5 times in the last 10 minutes!
```

**What it means:** This is an escalated alert. The container keeps crashing and restarting. Something is fundamentally wrong — maybe a config issue, missing dependency, or corrupted data.

**What to do:** Don't just keep restarting it. Tap **🔍 Diagnose** for AI analysis, or **📋 Logs** to read the full output. You may need to check the container's config or volume mounts.

### Log Error Alerts

**What it looks like:**
```
⚠️ ERRORS IN: sonarr

Found 3 errors in the last 15 minutes

Latest: Database connection failed: timeout
```

**What it means:** The container is running, but its logs contain error messages matching your configured patterns (e.g., "error", "exception", "fatal").

**What to do:**
- **🔇 Ignore Similar** — If this is a known harmless error, tap this to create an AI-generated ignore pattern
- **📋 Logs** — Read more context around the error
- **🔍 Diagnose** — Get AI analysis of what the errors mean

### Resource Alerts

**What it looks like:**
```
⚠️ HIGH MEMORY USAGE: plex

Memory: 92% (threshold: 85%)
        7.4GB / 8.0GB limit
Exceeded for: 3 minutes
```

**What it means:** A container has exceeded its CPU or memory threshold for longer than the sustained period (default: 2 minutes). This isn't necessarily a problem — Plex transcoding is expected to use lots of CPU, for example.

**What to do:** If this is expected behavior, either raise the threshold in `config.yaml` or mute the container. If unexpected, diagnose or restart.

---

## Container Management

### Viewing Status

**`/status`** — Shows all running containers with a summary of their state.

**`/status plex`** — Shows details for a specific container: image, uptime, ports, and resource usage.

Partial names work: `/status rad` matches `radarr`.

### Viewing Resources

**`/resources`** — Shows CPU and memory usage for all containers with progress bars.

**`/resources plex`** — Detailed stats for one container including configured thresholds.

### Reading Logs

**`/logs radarr`** — Shows the last 20 lines of container logs.

**`/logs radarr 100`** — Shows the last 100 lines.

You can also tap the **📋 Logs** button on any alert.

### AI Diagnostics

**`/diagnose radarr`** — Sends recent logs to your configured AI provider for analysis.

The response comes in two parts:

1. **Brief analysis** — A quick summary of what's happening, shown immediately with action buttons:
   - **📋 More Details** — Tap for an in-depth analysis with root causes and fix suggestions
   - **🔄 Restart** — Quick restart if the diagnosis suggests it
   - **📋 Logs** — View the raw logs

2. **Detailed analysis** — Only loaded when you tap More Details, to save AI API costs.

**Tip:** You can reply `/diagnose` directly to any crash, error, or restart loop alert — the bot automatically extracts the container name.

### Controlling Containers

**`/restart radarr`** — Shows a confirmation prompt:

```
🔄 Restart radarr?

Current status: running

[✅ Confirm]  [❌ Cancel]
```

Tap **✅ Confirm** to proceed, or **❌ Cancel** to abort. The same pattern applies to `/stop`, `/start`, and `/pull`.

**`/pull radarr`** — Pulls the latest image and recreates the container with the same configuration. This is effectively an update. The bot preserves all container settings (volumes, ports, environment variables, etc.) during recreation.

**Protected containers** (listed in `config.yaml`) cannot be controlled via Telegram — this prevents accidentally stopping critical services like databases.

---

## Unraid Server Monitoring

These commands require `UNRAID_API_KEY` to be configured.

### Server Overview

**`/server`** — Quick overview: CPU usage, memory, CPU temperature.

**`/server detailed`** — Full breakdown including per-core temperatures, individual RAM stick info, and more.

### Array Status

**`/array`** — Shows array state (started/stopped), total capacity, usage, and a summary of disk health.

**`/disks`** — Detailed per-disk information: capacity, usage, temperature, and SMART status.

### Server Alerts

The bot automatically monitors and alerts on:
- CPU temperature exceeding threshold
- CPU usage sustained above threshold
- Memory usage exceeding threshold
- Disk temperatures exceeding threshold
- Array usage exceeding threshold
- UPS battery below threshold

All thresholds are configurable in the `unraid.thresholds` section of `config.yaml`.

---

## Alert Control

### Muting Alerts

Temporarily silence alerts without fixing the underlying issue.

**`/mute radarr 2h`** — Mute radarr alerts for 2 hours.

Duration formats: `30m` (minutes), `2h` (hours), `1d` (days), `1w` (weeks).

**`/unmute radarr`** — Remove the mute early.

**`/mute-server 1d`** / **`/mute-array 1d`** — Mute all server or array alerts.

**`/mutes`** — View all active mutes with their expiry times.

Mute expiry is shown contextually:
- Same day: "until 14:30"
- Tomorrow: "until tomorrow 14:30"
- Further out: "until Feb 26 14:30"

You can also mute directly from alert buttons — every alert includes **🔕 Mute 1h** and **🔕 Mute 24h** options.

### Ignoring Errors

For recurring harmless errors, create ignore patterns so they stop triggering alerts.

#### From an Alert

When you receive a log error alert, tap **🔇 Ignore Similar**. The bot uses AI to generate a regex pattern that matches similar errors without being too broad.

#### From Recent Errors

**`/ignore`** — Shows recent errors from all watched containers as a selection UI:

```
🔇 Recent errors in radarr (last 15 min):

1. Authentication token expired
2. Database connection timeout
3. Failed to parse XML response

[☑ 1]  [☐ 2]  [☐ 3]
[Select All]  [Deselect All]
[✅ Ignore Selected]  [❌ Cancel]
```

Toggle individual errors by tapping their number buttons (☐ → ☑ and back). Use **Select All** to grab everything, then **✅ Ignore Selected** to generate patterns.

#### Managing Ignores

**`/ignores`** — List all active ignore patterns.

You can also manage ignores through the `/manage` dashboard (see below).

### The Manage Dashboard

**`/manage`** — Opens an interactive dashboard with buttons:

- **📊 Status** — Quick container overview
- **📈 Resources** — Resource usage summary
- **🖥️ Server** — Unraid server info
- **💾 Disks** — Disk status
- **📝 Manage Ignores** — Browse and delete ignore patterns
- **🔕 Manage Mutes** — Browse and remove active mutes
- **⚙️ Features** — Turn optional monitors on or off (see [Optional Features](#optional-features))

Each sub-view includes per-item **🗑** delete buttons and a **⬅️ Back** button to return to the dashboard.

---

## AI Features

AI features require at least one LLM provider: Anthropic Claude, OpenAI, or Ollama. The bot works without AI — you still get all alerts and commands, but `/diagnose`, smart ignore patterns, and natural language chat won't be available.

### What Uses AI

| Feature | Description |
|---------|-------------|
| `/diagnose` | Analyzes container logs and suggests fixes |
| Smart ignore patterns | Generates regex patterns from error examples |
| Natural language chat | Understands questions like "what's wrong with plex?" |
| Container classification | Helps categorize unknown containers during setup |

### Natural Language Chat

Instead of memorizing commands, just ask questions:

- "Is anything crashing?"
- "Why is plex using so much memory?"
- "Show me the last 50 lines from sonarr"
- "Restart radarr" — the bot will show confirmation buttons

Follow-up questions work too. After asking about a container, you can say "restart it" or "show me the logs" without repeating the name.

### Switching Providers

**`/model`** — Shows your configured providers with their available models:

1. Tap a provider (e.g., Anthropic, OpenAI, Ollama)
2. Choose a model from the list
3. Models without tool support are marked "(no tools)" — NL chat actions may be limited

You can also type the choice directly:

- **`/model sonnet`** — set the global default model (family names `sonnet`, `haiku`, `opus` resolve to the latest available release)
- **`/model chat sonnet`** — use a different model just for natural-language chat
- **`/model diagnose haiku`** — use a cheaper model for `/diagnose`
- **`/model analyze haiku`** — use a cheaper model for ignore-pattern generation
- **`/model chat default`** — reset a feature back to the global default

Per-feature overrides let you spend on a capable model where it matters (chat) and a cheap one where it doesn't (pattern analysis). Your selection is persisted across bot restarts.

---

## Optional Features

Two monitors are off by default. You can turn them on from Telegram — open **`/manage`** and tap **⚙️ Features** — or by editing `config.yaml`.

### Image-Update Detection

Checks once a day whether a newer image is available for your containers, then sends a single digest listing what's outdated, each with a one-tap **Pull** button.

**Enable it:** `/manage` → **⚙️ Features** → **✅ Enable image updates**. Because the check only runs at startup, the bot restarts itself to apply the change.

What you'll see:

```
🔄 Image updates available (3)

• radarr — linuxserver/radarr:latest
• sonarr — linuxserver/sonarr:latest
• plex — plexinc/pms-docker:latest

[⬇️ Pull radarr] [⬇️ Pull sonarr] [⬇️ Pull plex]
```

Tapping **Pull** runs the same confirm-then-recreate flow as `/pull`, preserving all container settings. The same update isn't re-announced after a restart.

**In `config.yaml`:**

```yaml
image_updates:
  enabled: true
  poll_interval_hours: 24   # minimum 1
```

### Auto-Heal

Automatically restarts a container when it reports an unhealthy Docker HEALTHCHECK — useful for apps that occasionally wedge but recover on a restart. A **storm guard** stops restart loops: after `max_restarts` attempts within `window_minutes`, it gives up and sends an escalation alert instead of restarting forever. Protected containers are never auto-healed.

**Enable it:** `/manage` → **⚙️ Features** → **🩹 Configure auto-heal**, then tap to select which containers should be healed. Tap **💾 Save** — this applies live, with no restart.

**In `config.yaml`:**

```yaml
auto_heal:
  enabled: true
  containers:
    - radarr
    - sonarr
  max_restarts: 3       # give up after this many restarts in the window
  window_minutes: 60    # rolling window for the restart count
```

When auto-heal gives up on a container, you get an alert so you can investigate manually rather than the bot silently looping.

---

## Tips & Best Practices

### Partial Name Matching

Most commands accept partial container names. `/logs rad` matches `radarr`. If multiple containers match, the bot shows all matches and asks you to be more specific.

### Protected Containers

Add critical containers to `protected_containers` in `config.yaml`:

```yaml
protected_containers:
  - unraid-monitor-bot  # Don't let the bot restart itself
  - mariadb
  - postgresql14
```

Protected containers cannot be restarted, stopped, started, or pulled via Telegram — even through natural language chat.

### Multiple Users

Multiple Telegram users can control the bot. Add all user IDs to `TELEGRAM_ALLOWED_USERS`:

```
TELEGRAM_ALLOWED_USERS=123456789,987654321
```

Mutes and ignores are global — if one user mutes a container, it's muted for everyone. Each user's NL chat history and pending confirmations are tracked separately.

### Memory Pressure Management

Enable with caution in `config.yaml`:

```yaml
memory_management:
  enabled: true
```

When system memory exceeds the critical threshold:
1. The bot warns you and starts a countdown
2. After the delay, it kills the lowest-priority killable container
3. If memory is still critical, it waits and kills the next one
4. When memory drops below the safe threshold, the bot offers to restart killed containers

Use `/cancel-kill` to abort a pending kill during the countdown.

### Keeping Noise Down

- **Raise thresholds** for containers that naturally run hot (e.g., Plex transcoding)
- **Use ignore patterns** for known harmless log errors
- **Mute during maintenance** — `/mute-server 1h` before you start working on the server
- **Tune the cooldown** — `cooldown_seconds` in log watching controls how often the same container can trigger error alerts (default: 15 minutes)

### Getting Help

Send `/help` to see command categories. Tap any category button to see its commands, and use **⬅️ Back** to return.

Send `/health` to check bot version, uptime, and whether all monitors are running correctly.
