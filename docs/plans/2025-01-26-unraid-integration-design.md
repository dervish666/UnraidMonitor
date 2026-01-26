# Unraid Integration Design

## Overview

Add comprehensive Unraid server monitoring and management to the existing Docker container monitoring bot. This integrates with the official Unraid GraphQL API (requires Unraid 7.1.4+) via the `unraid-api` Python library.

## Goals

- Proactive alerts for server health issues (CPU temp, memory, disk problems, UPS)
- At-a-glance status commands (`/server`, `/array`, `/disks`, `/vms`, `/ups`)
- Remote VM control (start/stop) with confirmation
- Separate mute controls for server alerts vs container alerts
- Graceful degradation if Unraid API unavailable

## Architecture

### Module Structure

```
src/
├── unraid/                    # New: Unraid integration
│   ├── __init__.py
│   ├── client.py              # UnraidClient wrapper with connection handling
│   ├── models.py              # Pydantic models for Unraid data
│   └── monitors/
│       ├── system_monitor.py  # CPU, memory, temp polling
│       ├── array_monitor.py   # Disk health, parity, capacity
│       └── ups_monitor.py     # UPS status monitoring
├── alerts/
│   ├── unraid_alerts.py       # New: Unraid-specific alert formatting
│   └── mute_manager.py        # Extended: separate server mute tracking
├── bot/
│   ├── unraid_commands.py     # New: /server, /disks, /array, /vms, /ups
│   └── unraid_control.py      # New: VM start/stop with confirmation
```

### Connection Approach

- Single `UnraidClient` instance created at startup
- Async context manager for connection lifecycle
- Reconnection handling on network failures
- Config in `config.yaml` with `UNRAID_API_KEY` in `.env`

### Polling Strategy

| Data | Interval | Reason |
|------|----------|--------|
| System metrics | 30s | CPU, memory, temp - need timely alerts |
| Array status | 5 min | Doesn't wake sleeping disks |
| UPS status | 60s | Power issues need quick response |
| Notifications | 2 min | Forward Unraid's built-in alerts |

## Alert System

### Alert Format (Visually Distinct)

```
🖥️ SERVER ALERT: High CPU Temperature
Temperature: 85°C (threshold: 80°C)
Current load: 45%

🖥️ SERVER ALERT: Memory Critical
Usage: 92% (threshold: 90%)
Used: 58.7 GB / 64 GB

💾 ARRAY ALERT: Disk Problem
Disk 3 (sdd): SMART errors detected
Status: Warning | Temp: 45°C
Errors: 12 read errors

💾 ARRAY ALERT: Parity Check Errors
Parity check completed with 3 errors
Duration: 14h 32m

🔋 UPS ALERT: On Battery Power
Status: On Battery
Charge: 85%
Runtime: ~12 minutes

🔋 UPS ALERT: Low Battery
Charge: 20% (threshold: 30%)
Status: On Battery
```

### Mute Controls (Separate from Container Mutes)

- `/mute-server 2h` - Mute all Unraid alerts
- `/mute-array 4h` - Mute just array/disk alerts
- `/mute-ups 1h` - Mute just UPS alerts
- `/mutes` - Shows both container AND server mutes

## Commands

### Status Commands

| Command | Description |
|---------|-------------|
| `/server` | System overview (CPU, RAM, uptime) |
| `/server detailed` | Full system metrics |
| `/array` | Array status summary |
| `/disks` | List all disks with status |
| `/disk <n>` | Specific disk details (SMART, temp) |
| `/vms` | List VMs with status |
| `/ups` | UPS status |
| `/shares` | Share usage summary |

### Control Commands

| Command | Description |
|---------|-------------|
| `/vm start <name>` | Start a VM (with confirmation) |
| `/vm stop <name>` | Graceful stop (with confirmation) |
| `/vm forceoff <name>` | Force stop (with confirmation) |

## Configuration

### config.yaml

```yaml
unraid:
  enabled: true
  host: "192.168.1.100"
  port: 443

  polling:
    system: 30
    array: 300
    ups: 60
    notifications: 120

  thresholds:
    cpu_temp: 80
    cpu_usage: 95
    memory_usage: 90
    disk_temp: 50
    array_usage: 85
    ups_battery: 30

  forward_notifications: true
  notification_filter:
    - warning
    - alert
```

### Environment (.env)

```
UNRAID_API_KEY=your-api-key-here
```

### Graceful Degradation

- If `unraid.enabled: false` or no API key, Unraid features don't load
- Container monitoring continues independently
- Connection failures log warnings but don't crash bot

## Implementation Phases

### Phase 1: Foundation (v0.8.0)
- UnraidClient wrapper with connection handling
- System monitor (CPU, memory, temp alerts)
- `/server` command
- `/mute-server` support

### Phase 2: Array Monitoring (v0.9.0)
- Array monitor (disk health, parity, capacity)
- `/array`, `/disks`, `/disk <n>` commands
- `/mute-array` support
- Disk temp and SMART error alerts

### Phase 3: UPS & VMs (v0.10.0)
- UPS monitor and alerts
- `/ups` command, `/mute-ups`
- VM listing and control
- `/shares` command

### Phase 4: Notifications (v0.11.0)
- Forward Unraid's built-in notifications
- Notification filtering by importance
- Deduplication with our own alerts

## Dependencies

- `unraid-api` - Python client for Unraid GraphQL API
- Requires Unraid 7.1.4+ with API enabled

## Success Criteria

- [ ] Server health alerts trigger correctly
- [ ] All status commands return accurate data
- [ ] VM control works with confirmation flow
- [ ] Server mutes independent from container mutes
- [ ] Bot continues working if Unraid unavailable
- [ ] No disk spin-up from monitoring
