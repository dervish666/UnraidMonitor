# Changelog

All notable changes to UnraidMonitor will be documented in this file.

## [0.14.1] - 2026-06-05

### Fixed
- **Restart-to-apply never came back** — enabling image updates (and the setup-wizard restart) called `dp.stop_polling()` before `os.execv`, which unwound `main()`'s polling loop and ran its shutdown `finally`, cancelling the restart coroutine before it re-exec'd. The bot shut down cleanly but never restarted (it only recovered if Docker's restart policy happened to be set). `restart_bot()` now re-execs directly without stopping polling or closing the session — `os.execv` replaces the whole process, and Python's close-on-exec defaults drop the in-flight long-poll so Telegram frees the getUpdates slot for the fresh process.

## [0.14.0] - 2026-06-05

### Added
- **Features panel in `/manage`** — a new ⚙️ Features section explains image-update detection and auto-heal, and lets you enable them from Telegram instead of editing `config.yaml`
- **Enable image updates from Telegram** — one tap persists `image_updates.enabled` and restarts the bot to apply it
- **Auto-heal container picker** — choose which containers get auto-restarted when unhealthy via a tappable picker (protected containers excluded); applies live with no restart, since the running `AutoHealer` shares the config object
- **Discoverability hint** — the startup card and `/health` now point to `/manage` → ⚙️ Features whenever image updates or auto-heal is off

### Changed
- `ImageUpdatesConfig` and `AutoHealConfig` gained runtime persistence (`set_enabled` / `set_containers`) mirroring the existing `ResourceConfig`/`UnraidConfig` pattern
- Extracted a reusable `restart_bot()` helper (`src/restart.py`); the setup-wizard restart paths now use it

## [0.13.0] - 2026-06-04

### Added
- **Alert-context-aware diagnostics** — `/diagnose` and the Diagnose alert button now pass the triggering alert type (crash / errors / restart-loop / high-usage) into the analysis, so the AI grounds its answer in what actually fired
- **Richer diagnostic context** — `DiagnosticService` now gathers full container state (status, running, OOM-killed, Docker error, health check, volumes, ports, restart policy) and secret-filtered environment variables, producing far more accurate root-cause analysis

### Changed
- **Status-aware diagnosis prompt** — the prompt states the container's current status (won't claim a running container exited) and instructs the model to check existing volumes/env before suggesting config changes
- **Diagnostic token limits raised** — brief 300→500, detail 800→1000 to accommodate the richer context

### Security
- **Env var redaction in diagnostics** — environment variables are filtered against a secret-name pattern (key/secret/password/token/credential/auth) before being shown to the LLM

## [0.12.0] - 2026-06-03

### Added
- **Image-update detection** (opt-in: `image_updates.enabled`) — polls registries every 24 hours (configurable via `image_updates.poll_interval_hours`), sends a batched digest alert when newer images are available, with per-container Pull buttons
- **Auto-heal** (opt-in: `auto_heal.containers`) — automatically restarts opted-in containers that report a Docker HEALTHCHECK `unhealthy` status; storm guard prevents restart loops (configurable `max_restarts` / `window_minutes`); protected containers are never touched
- **Richer startup message** — on first boot after a version upgrade, shows a "What's new" section with user-facing highlights; persists last-announced version to `data/announced_version.json`
- **CI test pipeline** — GitHub Actions workflow runs tests, ruff lint, and mypy type-check on every push and pull request

## [0.11.1] - 2026-05-17

### Changed
- **Dependencies upgraded** — aiogram 3.26→3.28, aiohttp 3.13.3→3.13.5, anthropic 0.85→0.102, openai 2.29→2.37, pydantic 2.12→2.13, mypy 1.20→2.1
- **Removed unused `unraid-api` dependency** — project uses direct GraphQL via aiohttp; removes unnecessary transitive deps
- **Tightened openai constraint** — lower bound raised from >=1.50.0 to >=2.0.0 to prevent accidental v1 downgrades
- **Added mypy to dev dependencies** — was previously installed but undeclared in pyproject.toml

## [0.11.0] - 2026-05-14

### Security
- **Prompt injection defense** — Tool results and error messages fed to LLM now sanitized via `sanitize_for_prompt()` to prevent prompt injection from container logs
- **MemoryMonitor race condition** — Kill/cancel/confirm operations now use `asyncio.Lock` to prevent TOCTOU races between concurrent button presses
- **SSRF protection in setup wizard** — Host input validated to block loopback, link-local (cloud metadata), and malformed addresses
- **File permissions** — Ignore manager files written with `0o644` instead of `0o666`
- **Mute duration cap** — Container, array, and server mute durations capped at 30 days
- **API key redaction** — Unraid API key redacted from connection error logs

### Fixed
- **ChatIdStore atomic writes** — Chat ID persistence now uses tempfile + `os.replace` to prevent corruption on crash
- **CrashTracker memory leak** — Stale entries evicted every 100 crash records; `_unhealthy_alerted` cleared on reconnect
- **Pattern cache unbounded growth** — LogWatcher regex cache now bounded to 64 entries
- **Provider re-instantiation** — LLM providers now cached by `(provider_name, model_name)` key; cache cleared on model switch
- **Thread pool exhaustion** — LogWatcher uses a dedicated `ThreadPoolExecutor` instead of the shared default pool
- **Parse duration unbounded** — Mute duration parsing capped at 7 days
- **NL processor message copying** — Fixed unnecessary list copy on each tool-use iteration

### Changed
- **`register_commands` refactored** — Split 365-line function into 4 focused helpers (`_register_ignore_commands`, `_register_unraid_commands`, `_register_memory_commands`, `_register_manage_commands`)
- **`start_monitoring` refactored** — Extracted `_init_alert_infrastructure`, `_init_nl_processor`, `_send_startup_notification` helpers
- **YAML persistence deduplicated** — Three identical atomic-write blocks replaced with shared `atomic_yaml_write()` in config.py
- **Threshold picker deduplicated** — Array and server threshold callbacks consolidated into shared `_show_threshold_picker` / `_apply_threshold` helpers
- **Monitor encapsulation** — All 6 monitors expose `is_running` property; health command no longer accesses private `_running` attributes
- **Startup parallelized** — Anthropic model discovery and Ollama model discovery now run concurrently via `asyncio.gather`
- **Private attribute encapsulation** — `NLProcessor.set_controller()` replaces direct `_executor._controller` mutation
- **Module-level mutable dict safety** — `_MODEL_FAMILIES` copied to instance level in `ProviderRegistry`
- **Threshold steps centralized** — `CPU_THRESHOLD_STEPS` and `MEMORY_THRESHOLD_STEPS` moved to `constants.py`
- **Dev dependencies** — Added `pytest-cov`, `anthropic`, and `openai` to `[project.optional-dependencies] dev`
- **Version detection** — `/health` command reads version from package metadata instead of hardcoded string

## [0.10.3] - 2026-05-14

### Fixed
- **Tool-calling chat messages rejected by API** — Assistant messages with tool calls used a normalized `tool_calls` key that the Anthropic API rejects (`Extra inputs are not permitted`). `_translate_messages` now converts these to proper Anthropic `tool_use` content blocks. This only affected messages that triggered tool use (e.g. "What containers are running?"); direct-answer messages (e.g. "What can you do?") were unaffected.

## [0.10.2] - 2026-05-14

### Fixed
- **Model alias chain resolution** — Retired model aliases (e.g. `claude-sonnet-4-5-20250929`) resolved to the family name `"sonnet"` but stopped there, sending an invalid model name to the API. `_resolve_model` now recursively chains aliases through families to concrete IDs
- **Config template** — Default config template now uses family names (`haiku`, `sonnet`) instead of dated model IDs, so new installs are resilient to model retirements

## [0.10.1] - 2026-05-14

### Fixed
- **Dynamic provider resolution** — NL processor and diagnostic service now resolve their LLM provider dynamically from the registry on each call, so `/model` changes take effect immediately without restart
- **`/model` not updating config.yaml** — Model changes via `/model` now persist to both `model_selection.json` (runtime) and `config.yaml` (permanent), keeping the config file in sync

## [0.10.0] - 2026-05-14

### Added
- **Server alert action buttons** — CPU temperature and CPU usage alerts now include Mute (1h/24h) and Adjust Threshold buttons with `srv_mute:`, `srv_thresh:`, `srv_set:` callback patterns
- **Mute expiry re-mute buttons** — Mute expiry notifications now include Re-mute 1h/24h buttons, adapting callback data to the mute type (container, array, or server)
- **Model family system** — Config and `/model` accept family names (`sonnet`, `haiku`, `opus`) instead of exact model IDs; resolved to latest available via `client.models.list()` at startup with hardcoded fallbacks
- **Per-feature model switching** — `/model chat sonnet`, `/model diagnose haiku`, `/model analyze opus` set models per AI feature; `/model <feature> default` resets to global; persisted to `model_selection.json`
- **Retired model aliases** — Automatically maps discontinued model IDs (e.g. `claude-sonnet-4-5`) to current replacements with a logged warning

### Changed
- **Default model constants** — Defaults now use family names (`haiku`, `sonnet`) instead of dated model IDs, making config resilient to model retirements
- **`/model` command** — Shows per-feature model status, supports quick-set syntax (`/model sonnet`), and displays family names in provider picker
- **UnraidConfig.set_threshold** — Extended to persist `cpu_temp` and `cpu_usage` thresholds in addition to array thresholds

## [0.9.7] - 2026-05-13

### Added
- **Array alert action buttons** - Array capacity and disk temperature alerts now include inline Mute (1h/24h) and Adjust Threshold buttons, matching the UX of container alerts
- **Runtime array threshold adjustment** - Capacity and disk temp thresholds can be changed via alert buttons and persist to config.yaml

### Changed
- **UnraidConfig persistence** - Array thresholds now support runtime modification with automatic config.yaml persistence

## [0.9.6] - 2026-04-22

### Fixed
- **Alerts lost after restart** - ChatIdStore was in-memory only; after any bot restart, all alerts silently dropped until user sent a message. Now persists chat IDs to `data/chat_ids.json` so alerts resume immediately
- **Self-monitoring feedback loop** - Log watcher's self-log filter missed `__main__` logger, causing the bot to detect its own "Alert queue full" warnings as errors, filling the queue faster

## [0.9.5] - 2026-03-18

### Changed
- **OpenAI SDK** - Bumped version pin to support v2.x (1.x → 2.29.0), enabling latest API features
- **psutil** - Bumped version pin to support v7.x (6.x → 7.2.2), improved macOS memory accuracy
- **Anthropic SDK** - Updated to v0.85.0

## [0.9.3] - 2026-03-12

### Fixed
- **Ruff lint errors** - Resolved all 20 pre-existing ruff lint violations across the codebase

### Changed
- **`.env.example` updated** - Added `OPENAI_API_KEY` and `OLLAMA_HOST` entries with improved comments reflecting multi-provider support

## [0.9.2] - 2026-03-07

### Added
- **Health check alerts** - Detects containers transitioning to unhealthy via Docker health_status events, sends alert with Restart/Logs/Diagnose buttons
- **Quick-action buttons on /status** - Single container detail view now shows Logs, Diagnose, Restart inline buttons
- **Mute expiry notifications** - Telegram notification sent when a mute expires, via periodic 5-minute flush task
- **Log drop metrics in /health** - Shows total dropped log lines when log storm protection activates
- **Alert queue depth in /health** - Shows pending alert count when alerts are queued before first /start
- **Mute non-existent warning** - Warns when muting a container name that doesn't match any known container
- **AlertManagerProxy tests** - 7 new tests covering proxy queuing, multi-user delivery, flush, and shutdown

### Fixed
- **Disk size unit conversion** - Fixed decimal-to-binary conversion for disk sizes (now uses 1024^3 for TiB)
- **IgnoreManager batch_updates race** - Moved `_save_runtime_ignores()` inside lock to prevent concurrent modification
- **Mute file permissions** - Changed from 0o666 to 0o644 for JSON persistence files
- **Defensive state copies** - `ContainerStateManager.get()`, `get_all()`, `find_by_name()` now return copies via `dataclasses.replace()`
- **Naive datetime consistency** - Strips timezone info when loading mute expiry times to prevent comparison errors
- **Graceful shutdown for alert queue** - Sentinel-based drain in DockerEventMonitor prevents alert loss during shutdown
- **Unraid auto-reconnect** - GraphQL client reconnects automatically on connection loss
- **Log drop rate limiting** - Warns once per 60s per container instead of flooding logs during storms
- **Network reconnect tracking** - ContainerController tracks failed network reconnections and appends warning to user message
- **Memory monitor decline fix** - Only resets `_restart_prompted` when no killed containers remain
- **PatternAnalyzer eviction** - Cache now evicts oldest entry by timestamp instead of arbitrary key
- **NL tool line clamping** - Line counts clamped to valid range `max(1, min(...))`
- **Config threshold clamping** - Unraid config values clamped to valid ranges at load time

### Changed
- **Removed dead code** - Removed backward-compat `handle_anthropic_error` alias and unreachable `return None` in telegram_retry
- **Narrowed exception catches** - `ignore_command.py` catches `TelegramBadRequest` specifically instead of broad `Exception`
- **Removed UPS from server mute categories** - No UPS monitoring exists; `CATEGORIES` now only `("server", "array")`
- **OpenAI version constraint** - Aligned to `>=1.50.0,<2.0.0`

## [0.9.1] - 2026-03-03

### Security
- **Pinned Docker base image** - `python:3.11-slim` now uses SHA256 digest to prevent supply chain attacks
- **Fixed overly permissive umask** - Changed from `0000` to `0022` in entrypoint.sh so created files are no longer world-writable
- **Moved AI SDKs to optional deps** - `anthropic` and `openai` are now optional dependencies under `[ai]` extra, reducing attack surface for non-AI deployments
- **Escape Markdown in container names** - All alert and command messages now use `escape_markdown()` for container names, preventing Telegram formatting injection from specially-crafted names
- **Callback data truncation** - `truncate_callback_data()` enforces Telegram's 64-byte limit on inline button callback data, preventing silent failures with long container names

### Fixed
- **Multi-user alert delivery** - Alerts are now sent to all authorized users instead of only the most recently active one. Affects server alerts, memory alerts, restart prompts, startup notifications, and wizard completion messages
- **Thread-safe RateLimiter** - Added `threading.Lock` to prevent race conditions between Docker event thread and async loop
- **Thread-safe IgnoreManager reads** - `get_all_ignores()`, `get_runtime_ignores()`, and `get_containers_with_runtime_ignores()` now hold the lock during reads; `defer_save` flag set under lock
- **Dirty flag on mute expiry** - `BaseMuteManager` now sets `_dirty = True` when cleaning expired mutes, ensuring the change is persisted
- **Memory threshold validation** - `MemoryConfig.from_dict()` validates that critical > warning > safe thresholds and falls back to defaults if misordered
- **Polling interval clamping** - Unraid system poll minimum 10s, array poll minimum 30s; prevents tight loops from misconfiguration
- **DEFAULT_LOG_WATCHING mutation** - Fixed shared mutable default dict being modified at runtime; now copies before use
- **Manage dashboard uses safe\_edit** - All manage sub-view callbacks now use `safe_edit()` instead of raw `answer()`, preventing Markdown parse failures
- **Unraid connectivity verification** - `UnraidClientWrapper.connect()` now verifies the server is reachable before setting `_connected = True`
- **Server alert formatting** - Server alerts now use `parse_mode="Markdown"` with `escape_markdown()` consistently
- **Dynamic cooldown text** - Error alerts show the configured cooldown duration instead of hardcoded "15 minutes"
- **Removed "Brisbooks" from defaults** - Removed author-specific container from default watched list

### Changed
- **Duration parser supports days** - Mute duration parser now accepts `"d"` suffix (e.g., `3d` for 3 days) in addition to `"m"` and `"h"`
- **PatternAnalyzer cache bounded** - LRU-style eviction at 256 entries prevents unbounded memory growth
- **NLProcessor user locks cleanup** - Stale (unlocked) entries pruned when dict exceeds 100 entries
- **Split SystemMonitor cache timestamps** - Each metric type (cpu, memory, temp) has its own cache timestamp for independent refresh
- **OpenAI/Ollama env vars in docker-compose** - Added `OPENAI_API_KEY` and `OLLAMA_HOST` to docker-compose.yml environment

### Removed
- Dead UPS mute methods (`mute_ups`, `unmute_ups`, `is_ups_muted`) from `ServerMuteManager` — no UPS monitoring exists

### Added
- `escape_markdown()` utility in formatting.py for safe Telegram message content
- `truncate_callback_data()` utility for safe inline button callback data
- 132 new tests: `test_formatting_utils.py` (53), `test_per_user_rate_limiter.py` (10), expanded `test_sanitize.py` (14 new), expanded `test_unraid_client.py` and `test_unraid_system_monitor.py`
- Total test count: 1020 (up from 888)

## [0.9.0] - 2026-02-25

### Added
- **Container recovery notifications** - When a previously crashed container starts successfully, the bot sends a brief "✅ recovered" alert. Includes 5-minute cooldown to prevent spam and automatically clears crash history on recovery
- **`/help` section buttons** - Help is now organized into 4 navigable categories (Containers, Server, Alerts, Setup) with inline keyboard buttons instead of a wall of text
- **Typing indicators** - Long operations (diagnose, resources, Unraid commands, control actions) show "typing..." in chat while processing
- **`safe_reply` / `safe_edit` helpers** - Centralized Markdown-safe messaging with automatic `TelegramBadRequest` fallback to plain text, used across all command handlers
- **`format_mute_expiry` helper** - Mute expiry times now show contextual dates: "until 14:30" (same day), "until tomorrow 14:30", or "until Feb 26 14:30" (further out)
- **Back button in `/manage` sub-views** - All manage sub-views (ignores, mutes, ignore details) now include a ⬅️ Back button to return to the dashboard

### Changed
- **Control confirmations use inline buttons** - `/restart`, `/stop`, `/start`, `/pull` now show ✅ Confirm / ❌ Cancel buttons instead of requiring a text "yes" reply. Removed `ConfirmationManager` and `YesFilter`
- **Diagnose "More Details" is a button** - After a `/diagnose` brief, users click a 📋 More Details button instead of typing "more details". Also shows Restart and Logs quick-action buttons. Removed `DetailsFilter`
- **Diagnose matches all alert types** - Replying `/diagnose` to an alert now works for CRASHED, ERRORS IN, and RESTART LOOP alerts (previously only matched CRASHED)
- **Ignore selection uses toggle buttons** - `/ignore` now shows ☐/☑ toggle buttons per error with Select All, Done, and Cancel instead of numbered text selection
- **Manage remove uses delete buttons** - `/manage` → Ignores and Mutes views show per-item 🗑 delete buttons instead of numbered text input
- **Styled usage messages** - `/restart`, `/stop`, `/start`, `/pull`, and `/logs` usage hints now use code formatting and show partial name examples

### Removed
- `src/bot/confirmation.py` - Replaced by inline button confirmation in control_commands.py
- `YesFilter`, `DetailsFilter`, `IgnoreSelectionFilter`, `ManageSelectionFilter` classes - All replaced by callback query handlers
- `tests/test_yes_handler.py`, `tests/test_details_handler.py`, `tests/test_confirmation.py` - Tests for removed components

## [0.8.3] - 2026-02-17

### Fixed
- **Memory restart prompt spam** - After killing a container for memory pressure, the "Restart X?" prompt was sent every 10 seconds instead of once, flooding the chat with duplicate messages
- **Memory restart buttons missing** - The restart prompt was plain text with no interactive buttons, so users couldn't actually accept or decline the restart. Added Yes/No inline keyboard buttons that properly confirm or decline the restart

## [0.8.2] - 2026-02-12

### Fixed
- **Self-monitoring loop** - Bot no longer alerts on its own internal Python log output when watching its own container, preventing feedback loops where errors trigger alerts about those same errors
- **Pattern analyzer noise** - JSON parse failures from Haiku responses now log at WARNING instead of ERROR, since they are model output quality issues, not system errors

## [0.8.1] - 2026-02-10

### Added
- **Restart loop detection** - Detects containers crashing 5+ times in 10 minutes and sends escalated alerts with crash count, separate from normal rate-limited crash alerts
- **`/health` command** - Shows bot version, uptime, all monitor statuses (running/stopped/disabled), Unraid connection state, and recent crash activity
- **Startup notification** - Bot sends a message on startup with container count, watched count, and Unraid status

### Fixed
- **Concurrent NL requests** - Per-user `asyncio.Lock` prevents interleaved Claude API calls from corrupting conversation memory
- **`signal.SIGALRM` crash on non-main thread** - Regex timeout for ignore patterns now uses daemon thread + `join(timeout)` instead of signals
- **Image pull hangs forever** - `pull_and_recreate()` now has a 5-minute timeout on Docker image pulls
- **Docker `load_initial_state()` blocking event loop** - Wrapped in `asyncio.to_thread()` on startup
- **Stale rate limiter entries** - `cleanup_stale()` called at start of each resource monitor poll cycle
- **Container names breaking Markdown** - Escaped underscores/special chars in `/status` and `/logs` multi-match responses
- **YAML parse errors crash startup** - `load_yaml_config()` catches `yaml.YAMLError` and raises descriptive `ValueError`
- **Unraid connection failure silent** - Now sends Telegram notification when Unraid connection fails on startup
- **`os.execv` restart leaking polling** - `dp.stop_polling()` called before exec in wizard completion
- **Concurrent wizard sessions corrupt state** - Only one user can run the setup wizard at a time
- **SSL verification disabled insecurely** - Replaced `ssl=False` with proper `SSLContext` in wizard connection test
- **Double signal handler shutdown** - Guard prevents re-entrant `_graceful_shutdown()` calls
- **Pattern cache XOR collision** - Changed cache key from `id() ^ id()` to tuple `(id(), id())`
- **`call_soon_threadsafe` crash during shutdown** - Wrapped with `RuntimeError` catch in log watcher threads
- **`from_user` None crash in ignore handlers** - Early return guard for channel/anonymous messages
- **Fire-and-forget startup task** - Background monitor task now tracked for graceful shutdown
- **Log watcher unbounded queue** - Added `maxsize=10000` with safe-put that drops on overflow (log storm protection)
- **Config `None` for empty YAML lists** - `ignored_containers` and `protected_containers` default to `[]` instead of `None`
- **Atomic config writes** - `save_yaml_config()` uses `tempfile` + `os.replace()` to prevent corruption on crash

## [0.8.0] - 2026-02-10

### Added
- **Telegram-based setup wizard** - On first run (no config.yaml), an interactive wizard guides users through setup via Telegram chat instead of generating a silent default config
- **Container auto-classification** - Pattern matching identifies ~30 common container types (databases, media servers, download clients, etc.) and assigns them to categories (priority, protected, watched, killable, ignored)
- **AI-assisted classification** - Unknown containers are classified by Claude Haiku when an Anthropic API key is available, with AI suggestions marked in the summary
- **Unraid connection testing** - Wizard auto-detects HTTPS/HTTP and port for the Unraid server
- **`/setup` command** - Re-run the setup wizard at any time; merges non-destructively with existing config (preserves thresholds, custom settings, and Unraid connection details)
- **`/cancel` command** - Exit the setup wizard mid-flow
- **Category descriptions** - Each adjust button in the wizard shows a description explaining what the category is for
- **Auto-restart after wizard** - Bot automatically restarts via `os.execv` after setup completes (works regardless of Docker restart policy)
- **Smart re-run behaviour** - `/setup` re-run tests existing Unraid connection and skips the IP prompt if it works; preserves existing container categories from config instead of re-classifying
- `ContainerClassifier` service with pattern rules and batch AI classification
- `ConfigWriter` with `write()` and `merge()` methods for config.yaml management
- `SetupModeMiddleware` blocks non-wizard commands during setup
- 78 new tests across 4 test files

### Fixed
- **ImageNotFound crash on startup** - Containers referencing removed Docker images (common after updates) caused crashes in event monitor, diagnostic service, container control, and wizard container listing
- **Wizard connection test failures** - Added required `apollo-require-preflight` CSRF header, valid GraphQL query with leaf fields, and logging for connection test diagnostics
- **`/setup` re-run overwrote Unraid settings** - Connection test tried HTTPS:443 first and overwrote working HTTP:80 config; now preserves existing connection settings when they work

### Changed
- **`main.py` refactored** - Extracted `start_monitoring()` function and `_BackgroundTasks` class for cleaner startup; first-run path defers all monitoring until wizard completes
- Removed `generate_default_config()` call from startup (wizard replaces it)

## [0.7.4] - 2026-02-10

### Changed
- **PUID/PGID entrypoint for Unraid permissions** - Container now starts as root and uses an entrypoint script to fix ownership of bind-mounted `/app/config` and `/app/data` directories to `PUID:PGID` (defaults to `99:100` = `nobody:users`), then drops privileges via `gosu`. Fixes root-owned appdata folders created by Community Apps on first install.
- **Permissive file creation** - Set `umask 0000` in entrypoint and added `os.fchmod(fd, 0o666)` to mute/ignore JSON writers so all created files (config.yaml, mute/ignore JSON) are `rw-rw-rw-` instead of owner-only

### Added
- `entrypoint.sh` - Privilege-drop entrypoint that sets directory ownership and runs as non-root user
- `PUID` and `PGID` environment variables (default `99`/`100`) for configurable file ownership
- `gosu` package in Docker image for secure privilege dropping

## [0.7.3] - 2026-02-06

### Security
- **Auth middleware on callback queries** - Authentication was only applied to message handlers, not inline button callbacks. Any user with a forwarded alert could invoke actions. Now enforced on all callback queries (P0-4)
- **Protected container bypass via callbacks** - NL confirm callback and alert restart button bypassed the protected container list. Added `is_protected()` checks (P0-2, P0-3)
- **Sanitized error messages** - Raw exception details from Docker SDK no longer leak to Telegram users (P1-6)
- **ReDoS prevention** - Added signal-based regex timeout for AI-generated ignore patterns (P1-14)
- **Docker socket security** - Documented root-equivalent access risk, added `docker-socket-proxy` recommendation (P1-19)

### Fixed
- **`pull_and_recreate()` redesigned with rollback** - Previously deleted the container before recreation with no recovery path and only extracted 5 config properties. Now preserves 30+ properties and rolls back on failure (P0-1)
- **Wrong method calls in manage dashboard** - `unmute()` → `unmute_array()` and `remove_mute("server")` → `unmute_server()` fixed, preventing partial unmutes and runtime crashes (P0-5, P0-8)
- **NL tool array status schema mismatch** - Was checking wrong field names (`status == "healthy"`, `used_bytes`), now matches actual GraphQL schema (`DISK_OK`, `capacity.kilobytes`) (P0-6)
- **CPU temperature always returned 0** - Changed from hardcoded `0` to `None` when unavailable, with graceful handling in display and alert code (P0-7)
- **Async Anthropic client** - All three Claude API callers switched from synchronous to async, no longer blocking the event loop for 2-30 seconds per call (P1-1)
- **Async Docker SDK calls** - Blocking Docker calls wrapped in `asyncio.to_thread()` across 4 files (P1-2)
- **Parallel Docker stats collection** - `get_all_stats()` now uses `asyncio.gather()` instead of sequential calls, reducing poll time from 20-40s to ~2s for 20 containers (P1-3)
- **Thread-unsafe asyncio.Queue** - Fixed with `call_soon_threadsafe()` in log watcher and docker events (P1-4)
- **Default config uses HTTPS** - Changed default Unraid connection from HTTP port 80 to HTTPS port 443 (P1-5)
- **Alert queuing before chat ID** - Alerts during startup are now queued and flushed when first user sends `/start` (P1-7)
- **Null safety for `from_user` and `callback.message`** - Added None guards preventing AttributeError in channel posts (P1-8, P1-9)
- **Unraid system monitor rate limiting** - No longer sends duplicate alerts every 30-second poll cycle (P1-10)
- **Cancellable log watcher threads** - Stream close mechanism prevents orphaned threads on container restarts (P1-11)
- **Async locking for mute/ignore managers** - Added `asyncio.Lock` to prevent race conditions (P1-12)
- **Unraid client HTTP timeout** - Set 10-second timeout, preventing 5-minute stalls on unresponsive server (P1-13)
- **Hardcoded IP removed** - Replaced `192.168.0.190` with `your-unraid-ip` placeholder in config template (P1-15)
- **Running containers filter** - `containers.list()` now filters by status instead of fetching all (P1-18)
- **Memory leak fixes** - TTL-based cleanup for rate_limiter, diagnostic pending dict, confirmation manager, ignore/manage selection states (P2-10 through P2-13)
- **Threading/task bugs** - Removed threading.Lock from async memory_monitor, fixed double task creation in system_monitor, added ghost container cleanup on reconnect (P2-14 through P2-16)
- **Telegram callback data overflow** - UTF-8 byte-length calculation for callback_data, colon-safe splitting, markdown escaping for container names (P2-17, P2-18, P2-26)
- **Log truncation** - Now accounts for header/footer length to stay within Telegram's 4096 char limit (P2-27)

### Changed
- **Dependencies fully declared** - Added all missing runtime deps (aiogram, pyyaml, pydantic, anthropic, psutil, aiohttp, python-dotenv) to pyproject.toml with pinned upper bounds (P1-16, P1-17, P2-19, P2-25)
- **Dockerfile base image** - Added digest pinning comment (P2-20)
- **Config template** - Added `panic` and `traceback` to default error_patterns (P2-22)
- **`unraid-api` version constraint** - Updated from `>=0.1.0,<1.0.0` to `>=1.0.0,<2.0.0` to match available releases

### Removed
- Dead code: unused imports (`asdict`, `Bot`), dead functions (`cancel`, `is_action_tool`, `is_read_only_tool`, `get_vms`, `get_ups_status`), unused variables, empty `__init__` re-exports (P3-1 through P3-9)
- Orphan UPS config fields with no UPS monitor (P3-12 through P3-14)
- Duplicate `format_uptime` and `extract_container_from_alert` implementations, consolidated into shared utils (P3-10, P3-11)

### Added
- 71 new tests covering ContainerController, DockerEventMonitor, AlertManagerProxy, control commands, NLProcessor, MemoryStore, LogWatcher, ConfirmationManager, AlertManager (P2-1 through P2-9)
- Comprehensive codebase audit report (`docs/audit-report-2026-02-06.md`)
- Shared `utils/formatting.py` module for consolidated utility functions

## [0.7.2] - 2026-02-01

### Fixed
- **Missing resource_monitoring in default config** - The auto-generated config.yaml was missing the resource_monitoring section, causing new deployments to use hardcoded defaults instead of configurable values.

### Added
- `.dockerignore` file to exclude config, tests, and dev files from Docker images
- Tests for default config generation (4 new tests verifying YAML validity and section loading)

### Security
- Added `config/config.yaml` to `.gitignore` to prevent accidental commit of user configurations

## [0.7.1] - 2026-02-01

### Fixed
- **High CPU usage from regex ignore patterns** - Regex patterns were being compiled on every log line check, causing 90%+ sustained CPU. Now pre-compiled once when pattern is created.
- Added logging to bare exception handlers in docker_events.py (previously silent failures)
- Added JSONDecodeError handling in Unraid GraphQL client
- Added debug logging for Docker timestamp parsing failures
- Removed unused `monitoring.health_check_interval` config option

### Changed
- Updated CLAUDE.md with accurate environment variables and architecture documentation
- Updated README.md storage section (removed non-existent database reference)

### Added
- Test coverage for alert_callbacks.py (30 tests) - restart, logs, diagnose, mute button handlers
- Test coverage for BaseMuteManager (25 tests) - JSON persistence, expiry logic, edge cases
- Total test count: 502 (up from 447)

## [0.7.0] - 2026-01-28

### Added
- `/manage` command - dashboard with quick access buttons for:
  - Container status overview
  - Resource usage summary
  - Manage runtime ignores (view and remove)
  - Manage active mutes (view and remove container, server, and array mutes)

## [0.6.0] - 2025-01-27

### Added
- Quick action buttons on all alerts (Restart, Logs, Diagnose, Mute)
- Memory pressure management with automatic container killing
- Smart ignore pattern generation using AI (Claude Haiku)
- Persistent storage for mutes and ignore patterns
- `/cancel-kill` command to abort pending memory pressure kills

### Changed
- Error alerts now show "Ignore Similar" button with AI-powered pattern extraction
- Crash alerts include Restart button for one-tap recovery
- All alerts include Mute buttons (1h and 24h options)

## [0.5.0] - 2025-01-26

### Added
- Unraid server monitoring via GraphQL API
- `/server` and `/server detailed` commands for system metrics
- `/array` and `/disks` commands for array/disk status
- Server temperature, memory, and UPS alerts
- Array health monitoring (disk temps, SMART status, parity)
- `/mute-server` and `/mute-array` commands
- Array mute manager for disk/parity alerts

## [0.4.0] - 2025-01-25

### Added
- `/mute` and `/unmute` commands for container alert control
- `/mutes` command to view all active mutes
- `/ignore` command to create ignore patterns from recent errors
- `/ignores` command to list all ignore patterns
- Recent errors buffer for ignore pattern selection
- Persistent mute storage in JSON files

## [0.3.0] - 2025-01-24

### Added
- Resource monitoring with CPU/memory threshold alerts
- `/resources` command for container resource stats
- Per-container threshold configuration
- Sustained threshold checking (alerts after duration exceeded)

### Changed
- Alerts now include resource context (memory/CPU usage)

## [0.2.0] - 2025-01-23

### Added
- AI-powered diagnostics with `/diagnose` command
- Log watching with configurable error patterns
- Log error alerts with rate limiting
- Container control commands (`/restart`, `/stop`, `/start`, `/pull`)
- Protected containers list to prevent accidental control
- Confirmation prompts for destructive actions

## [0.1.0] - 2025-01-22

### Added
- Initial release
- Docker container monitoring via socket
- Crash detection with exit code interpretation
- `/status` and `/logs` commands
- Telegram bot with user authentication
- Basic alert system for container events
