# Error Ignore Design

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:writing-plans to create the implementation plan.

**Goal:** Allow users to ignore specific recurring errors per-container, via config file and Telegram command.

**Architecture:** Extend log_watching with per-container ignores from config.yaml + runtime ignores from JSON file. Add /ignore command to pick errors to suppress.

**Tech Stack:** Python 3.11+, aiogram (existing), JSON for runtime storage

---

## Configuration

**config.yaml additions:**

```yaml
log_watching:
  containers:
    - plex
    - radarr
    - sonarr
  error_patterns:
    - error
    - exception

  # Global ignore patterns (existing, unchanged)
  ignore_patterns:
    - DeprecationWarning
    - DEBUG

  # NEW: Per-container ignore patterns
  container_ignores:
    plex:
      - "connection timed out"
      - "slow query"
    radarr:
      - "rate limit exceeded"
```

**Runtime ignores (`data/ignored_errors.json`):**

```json
{
  "plex": [
    "Sqlite3 database is locked",
    "Failed to connect to metadata agent"
  ],
  "radarr": [
    "Unable to connect to indexer"
  ]
}
```

Both sources merged at runtime. Matching is case-insensitive substring.

---

## Command Usage

| Command | Description |
|---------|-------------|
| `/ignore` | Reply to error alert - shows recent errors to pick from |
| `/ignores` | List all current ignores with source |

---

## /ignore Flow

1. User receives alert: `⚠️ ERRORS IN: sonarr - Found 6 errors...`
2. User replies with `/ignore`
3. Bot shows recent errors:
   ```
   🔇 Recent errors in sonarr (last 15 min):

   1. Database connection failed: timeout
   2. Unable to connect to indexer
   3. Rate limit exceeded on API
   4. Database connection failed: timeout
   5. SSL handshake error
   6. Unable to connect to indexer

   Reply with numbers to ignore (e.g., "1,3" or "all")
   ```
4. User replies: `1,3`
5. Bot deduplicates and confirms:
   ```
   ✅ Ignored for sonarr:
     • Database connection failed: timeout
     • Rate limit exceeded on API
   ```

**Edge cases:**
- `/ignore` without replying to alert → "Reply to an error alert to ignore it"
- Already ignored → Skip duplicates silently
- `/ignore` reply to non-error message → "Can only ignore error alerts"

---

## /ignores Output

```
🔇 Ignored Errors

plex (3):
  • Sqlite3 database is locked
  • Failed to connect to metadata agent
  • connection timed out (config)

radarr (1):
  • Unable to connect to indexer

_Use /ignore to add more, or edit config.yaml_
```

The `(config)` tag shows which came from config.yaml vs runtime JSON.

---

## Architecture

### Components

| Component | Change |
|-----------|--------|
| `src/config.py` | Add `container_ignores` parsing |
| `src/monitors/log_watcher.py` | Load ignores, check before alerting |
| `src/alerts/rate_limiter.py` | Track recent error messages (not just counts) |
| `src/bot/ignore_command.py` | New - `/ignore` and `/ignores` handlers |
| `src/bot/telegram_bot.py` | Register new commands |
| `data/ignored_errors.json` | New - runtime ignores (auto-created) |

### Data Flow

```
Log line detected as error
        ↓
Check global ignore_patterns (existing)
        ↓
Check container_ignores from config.yaml (new)
        ↓
Check ignored_errors.json (new)
        ↓
If not ignored → Store in recent_errors buffer → Rate limit → Alert
```

### IgnoreManager Class

```python
class IgnoreManager:
    def __init__(self, config_ignores: dict, json_path: str):
        self._config_ignores = config_ignores  # from config.yaml
        self._runtime_ignores = {}  # from JSON file
        self._json_path = json_path
        self._load_runtime_ignores()

    def is_ignored(self, container: str, message: str) -> bool:
        """Check if message should be ignored (substring, case-insensitive)."""

    def add_ignore(self, container: str, message: str) -> bool:
        """Add runtime ignore, save to JSON. Returns False if already exists."""

    def get_all_ignores(self, container: str) -> list[tuple[str, str]]:
        """Get all ignores for container as (message, source) tuples."""

    def _load_runtime_ignores(self) -> None:
        """Load from JSON file."""

    def _save_runtime_ignores(self) -> None:
        """Save to JSON file."""
```

### RecentErrorsBuffer

Extend RateLimiter or create separate buffer:

```python
@dataclass
class RecentError:
    message: str
    timestamp: datetime

class RecentErrorsBuffer:
    def __init__(self, max_age_seconds: int = 900, max_per_container: int = 50):
        self._errors: dict[str, list[RecentError]] = {}

    def add(self, container: str, message: str) -> None:
        """Add error, prune old entries."""

    def get_recent(self, container: str) -> list[str]:
        """Get unique recent error messages for container."""
```

---

## Matching Strategy

- **Substring match** - ignored text appears anywhere in error message
- **Case-insensitive** - "connection" matches "Connection Failed"
- **No regex** - keep it simple

---

## Success Criteria

- [ ] Per-container ignores in `config.yaml` (`container_ignores` section)
- [ ] Runtime ignores in `data/ignored_errors.json`
- [ ] `/ignore` command - reply to alert, pick from recent errors
- [ ] `/ignores` command - list all current ignores with source
- [ ] Substring matching (case-insensitive)
- [ ] Recent errors buffer (15 min, 50 max per container)
- [ ] All tests pass
