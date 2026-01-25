# Resource Monitoring Design

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:writing-plans to create the implementation plan.

**Goal:** Add `/resources` command and background monitoring to track container CPU/memory usage with threshold alerts.

**Architecture:** ResourceMonitor polls Docker stats periodically, tracks sustained threshold violations, and sends alerts via existing AlertManager.

**Tech Stack:** Python 3.11+, docker SDK (existing), aiogram (existing)

---

## Command Usage

| Usage | Description |
|-------|-------------|
| `/resources` | Show CPU/memory summary for all running containers |
| `/resources <name>` | Show detailed stats for specific container |

**Summary view:**
```
📊 Container Resources

radarr      CPU: 12%  MEM: 45% (1.2GB)
sonarr      CPU: 8%   MEM: 32% (0.8GB)
plex        CPU: 65%  MEM: 78% (4.2GB) ⚠️
overseerr   CPU: 2%   MEM: 15% (0.3GB)

⚠️ = approaching threshold
```

**Detailed view:**
```
📊 Resources: plex

CPU:    65% ████████████░░░░ (threshold: 80%)
Memory: 78% ██████████████░░ (threshold: 85%)
        4.2GB / 5.4GB limit

Status: Running for 3d 12h
```

---

## Alert Format

**CPU alert:**
```
⚠️ *HIGH RESOURCE USAGE: plex*

CPU: 92% (threshold: 80%)
Exceeded for: 2 minutes

Memory: 4.2GB / 5.4GB (78%)

_Use /resources plex or /diagnose plex for details_
```

**Memory alert:**
```
⚠️ *HIGH MEMORY USAGE: radarr*

Memory: 95% (threshold: 85%)
        3.8GB / 4.0GB limit
Exceeded for: 3 minutes

CPU: 45% (normal)

_Use /resources radarr or /diagnose radarr for details_
```

---

## Configuration

**config.yaml:**
```yaml
resource_monitoring:
  enabled: true
  poll_interval_seconds: 60
  sustained_threshold_seconds: 120  # Alert after 2 min exceeded

  # Global defaults
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

---

## Architecture

### New Components

| Component | Location | Purpose |
|-----------|----------|---------|
| `ResourceMonitor` | `src/monitors/resource_monitor.py` | Poll stats, track violations, trigger alerts |
| `ResourceConfig` | `src/config.py` | Parse resource_monitoring config section |
| `resources_command` | `src/bot/commands.py` | Handle `/resources` command |

### ResourceMonitor

```python
@dataclass
class ContainerStats:
    name: str
    cpu_percent: float
    memory_percent: float
    memory_bytes: int
    memory_limit: int

@dataclass
class ViolationState:
    metric: str  # "cpu" or "memory"
    started_at: datetime
    current_value: float
    threshold: float

class ResourceMonitor:
    def __init__(
        self,
        docker_client: docker.DockerClient,
        config: ResourceConfig,
        alert_manager: AlertManager,
        rate_limiter: RateLimiter,
    ):
        self._docker = docker_client
        self._config = config
        self._alert_manager = alert_manager
        self._rate_limiter = rate_limiter
        self._violations: dict[str, dict[str, ViolationState]] = {}
        self._running = False

    async def start(self) -> None:
        """Start the monitoring loop."""

    def stop(self) -> None:
        """Stop monitoring."""

    async def get_stats(self, container_name: str | None = None) -> list[ContainerStats]:
        """Get current stats for one or all containers."""

    def _check_thresholds(self, stats: ContainerStats) -> None:
        """Check if container exceeds thresholds, update violations."""

    async def _send_alert(self, container: str, violation: ViolationState, stats: ContainerStats) -> None:
        """Send threshold exceeded alert."""
```

---

## Data Flow

```
┌─────────────────┐
│ ResourceMonitor │
│   (async loop)  │
└────────┬────────┘
         │ every 60s
         ▼
┌─────────────────┐
│  Docker Stats   │
│  (all running)  │
└────────┬────────┘
         │
         ▼
┌─────────────────┐     ┌──────────────┐
│ Check thresholds├────►│ ViolationMap │
│ (per container) │     │ start times  │
└────────┬────────┘     └──────────────┘
         │
         │ sustained exceeded?
         ▼
┌─────────────────┐     ┌──────────────┐
│  RateLimiter    │────►│ AlertManager │
│  (cooldown)     │     │ (telegram)   │
└─────────────────┘     └──────────────┘
```

---

## CPU Calculation

Docker stats provide cumulative CPU usage. To get percentage:

```python
def calculate_cpu_percent(stats: dict) -> float:
    cpu_delta = stats["cpu_stats"]["cpu_usage"]["total_usage"] - \
                stats["precpu_stats"]["cpu_usage"]["total_usage"]
    system_delta = stats["cpu_stats"]["system_cpu_usage"] - \
                   stats["precpu_stats"]["system_cpu_usage"]
    num_cpus = stats["cpu_stats"]["online_cpus"]

    if system_delta > 0 and cpu_delta > 0:
        return (cpu_delta / system_delta) * num_cpus * 100.0
    return 0.0
```

---

## Success Criteria

- [ ] `/resources` shows all containers with CPU/memory
- [ ] `/resources <name>` shows detailed view with progress bars
- [ ] Background monitor polls at configured interval
- [ ] Alerts only after sustained threshold violation
- [ ] Per-container threshold overrides work
- [ ] Rate limiting prevents alert spam
- [ ] Graceful handling when containers stop/start during monitoring
- [ ] All tests pass
