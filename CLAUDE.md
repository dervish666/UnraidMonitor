# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Codebase Structure Index

The file map below provides instant orientation. For detailed export signatures and dependencies, read the relevant `.claude/structure/*.yaml` file for the directory you're working in (files: alerts.yaml, bot.yaml, llm.yaml, monitors.yaml, nut.yaml, root.yaml, services.yaml, unraid.yaml, utils.yaml).

After adding, removing, or renaming source files or public classes/functions, update both the file map below and the relevant structure YAML file.

### File Map

<!-- One line per source file: relative path - brief description -->

# Root
src/main.py - Entry point, signal handling, wizard setup (delegates to startup.py)
src/startup.py - start_monitoring() orchestrator, provider/monitor initialization
src/alert_proxy.py - AlertManagerProxy for multi-user alert delivery with queuing
src/background.py - _BackgroundTasks tracker for graceful shutdown of monitors
src/restart.py - restart_bot() graceful in-place process restart via os.execv (applies config that needs a fresh start)
src/monitor_callbacks.py - Factory functions for monitor alert/mute callbacks
src/constants.py - Named constants for all configuration defaults and thresholds
src/config.py - Settings, configuration loading, YAML parsing, ConfigWriter
src/models.py - ContainerInfo dataclass for Docker container state
src/state.py - ContainerStateManager for thread-safe container state tracking

# Alerts
src/alerts/manager.py - AlertManager, AlertSender protocol, ChatIdStore
src/alerts/rate_limiter.py - RateLimiter for deduplicating container alerts
src/alerts/mute_manager.py - MuteManager for temporary container alert mutes
src/alerts/server_mute_manager.py - ServerMuteManager for Unraid server/array mutes
src/alerts/array_mute_manager.py - ArrayMuteManager for array/disk alert mutes
src/alerts/base_mute_manager.py - BaseMuteManager with JSON persistence for mutes
src/alerts/ignore_manager.py - IgnoreManager for ignoring regex patterns in logs
src/alerts/recent_errors.py - RecentErrorsBuffer for tracking recent container errors

# Analysis
src/analysis/pattern_analyzer.py - AI-powered pattern generation for ignores

# LLM Providers
src/services/llm/provider.py - LLMProvider protocol, LLMResponse, ToolCall, ModelInfo
src/services/llm/anthropic_provider.py - Anthropic Claude provider (tool use, prompt caching)
src/services/llm/openai_provider.py - OpenAI GPT provider (function calling translation)
src/services/llm/ollama_provider.py - Ollama local provider with model discovery
src/services/llm/registry.py - ProviderRegistry for model selection and per-feature overrides

# Bot
src/bot/telegram_bot.py - Bot initialization, dispatcher, command registration, global error + catch-all callback handlers
src/bot/command_menu.py - Telegram "/" autocomplete menu, derived from the Command filters actually registered
src/bot/commands.py - /help (sectioned), /status, /logs command handlers
src/bot/control_commands.py - /restart, /stop, /start, /pull with inline button confirmations
src/bot/diagnose_command.py - /diagnose command for AI-powered log analysis
src/bot/resources_command.py - /resources command for per-container CPU/memory stats
src/bot/unraid_commands.py - /server, /array, /disks commands for Unraid monitoring
src/bot/mute_command.py - /mute, /unmute, /mutes commands for alert muting
src/bot/ignore_command.py - /ignore, /ignores, /ignore-similar for pattern management
src/bot/manage_command.py - /manage dashboard with status, resources, ignores, mutes
src/bot/alert_callbacks.py - Quick-action button handlers for alert buttons
src/bot/nl_handler.py - Natural language message handler with NL filter
src/bot/health_command.py - /health command, collect_status() collector, collapsed startup message renderer (build_startup_lines, ai_summary)
src/bot/setup_wizard.py - Interactive first-run setup for Unraid and containers
src/bot/memory_commands.py - /cancel-kill command for canceling memory-based kills
src/bot/model_command.py - /model command for runtime LLM provider/model switching
src/bot/ups_command.py - /ups command and the mute buttons on UPS alerts

# Monitors
src/monitors/docker_events.py - DockerEventMonitor with CrashTracker for container events
src/monitors/log_watcher.py - LogWatcher for streaming container logs, error detection
src/monitors/resource_monitor.py - ResourceMonitor for per-container CPU/memory polling
src/monitors/memory_monitor.py - MemoryMonitor for system memory pressure management
src/monitors/image_update_monitor.py - ImageUpdateMonitor for daily image digest polling and batched update alerts

# NUT (UPS)
src/nut/__init__.py - NUT client and UPS monitoring exports
src/nut/client.py - Async NUT protocol client (written against the protocol, not a wrapper)
src/nut/monitor.py - UpsMonitor poll loop, alert ladder, "unavailable is not healthy" state

# Services
src/services/docker_client.py - SharedDockerClient wrapper for reconnectable Docker access
src/services/nl_processor.py - NLProcessor for Claude-powered natural language chat
src/services/nl_tools.py - Tool definitions and executor for Claude tool use
src/services/container_control.py - ContainerController for safe restart/stop/start
src/services/container_classifier.py - ContainerClassifier using patterns and AI for roles
src/services/diagnostic.py - DiagnosticService for AI container log analysis
src/services/auto_healer.py - AutoHealer for opt-in auto-restart of unhealthy containers with storm guard

# Unraid
src/unraid/client.py - UnraidClientWrapper with direct GraphQL API access
src/unraid/monitors/system_monitor.py - UnraidSystemMonitor for CPU/memory/temp alerts
src/unraid/monitors/array_monitor.py - ArrayMonitor for disk health, usage, and parity-operation progress
src/unraid/monitors/notification_monitor.py - UnraidNotificationMonitor relaying Unraid's own notification feed (opt-in, importance floor)

# Utils
src/utils/api_errors.py - LLM API error handling (Anthropic + OpenAI) with user-friendly messages
src/utils/formatting.py - Bytes/uptime formatting, safe_reply/safe_edit, format_mute_expiry
src/utils/heartbeat.py - heartbeat_loop() touching the liveness file the Docker HEALTHCHECK reads
src/utils/rate_limiter.py - PerUserRateLimiter for per-user API rate limiting
src/utils/sanitize.py - Prompt injection prevention, sensitive data redaction
src/utils/telegram_format.py - markdown_to_telegram_html: escape-first rendering of LLM output for HTML parse mode
src/utils/telegram_retry.py - Telegram API retry logic for rate limit handling
src/utils/version_store.py - read/write data/announced_version.json for startup What's new gate (atomic)

## Project Overview

Unraid Server Monitor Bot (v0.22.3) - A Docker-based Telegram bot for monitoring Unraid servers. Monitors Docker containers (events, logs, resources), Unraid server health (CPU, memory, disks, array) and UPS state over NUT. Uses multi-provider LLM support (Anthropic, OpenAI, Ollama) for AI-powered diagnostics and natural language interaction. Sends alerts via Telegram with quick-action buttons.

UPS has a history worth knowing: the README once advertised UPS alerts that did not exist (removed in v0.19.0), and v0.21.0 built them for real against a NUT server (`src/nut/`). Do not reintroduce the "UPS was never implemented" line.

**Version string lives in TWO places** — `pyproject.toml` (`version`) and `src/__init__.py` (`__version__`). Both must be bumped together on release; `tests/test_version.py` guards against drift. `BOT_VERSION` (in `src/bot/health_command.py`) resolves from installed package metadata when available, else falls back to `src.__version__` — the package is not installed in the Docker image, so the `__version__` fallback is what runs in production.

## Commands

```bash
# Run the application
uv run python -m src.main

# Run tests (uses pytest-asyncio with auto mode, configured in pyproject.toml)
uv run pytest tests/
uv run pytest tests/test_<module>.py
uv run pytest tests/test_<module>.py -k "test_name"
uv run pytest --cov=src tests/

# Type checking (strict mode, Python 3.11)
uv run mypy src/

# Linting (line-length 100, target py311)
uv run ruff check src/

# Docker (target is Unraid x86_64 -- always build for linux/amd64)
docker buildx build --platform linux/amd64 -t dervish/unraidmonitorbot:latest --push .
docker-compose up -d
```

## Docker Deployment

The container runs as non-root via `entrypoint.sh`:
- `PUID`/`PGID` env vars control the runtime user (default: 99:100, Unraid's `nobody:users`)
- `gosu` drops privileges from root to `appuser` after fixing directory ownership
- umask is 0022 — config and data files are owner-writable only
- Base image is `python:3.11-slim` pinned by SHA digest in the Dockerfile

## Architecture

### Data Flow
```
Docker Socket ──→ DockerEventMonitor ──→ AlertManagerProxy ──→ Telegram Bot ──→ User
                  LogWatcher ──────────→ (RateLimiter,       ↗                   ↓
                  ResourceMonitor ─────→  MuteManager,      /              Docker Actions
                                          IgnoreManager)   /
Unraid API ────→ UnraidSystemMonitor ──→ AlertManagerProxy/
                 ArrayMonitor ─────────→
```

### Startup & Wiring

Startup is split across three files:
- **`src/main.py`** — Entry point. Creates bot/dispatcher, handles first-run wizard setup vs normal boot, signal handlers, and graceful shutdown.
- **`src/startup.py`** — `start_monitoring()` orchestrates all component initialization: LLM providers, monitors, state managers, and bot command registration.
- **`src/monitor_callbacks.py`** — Factory functions (`make_log_error_handler`, `make_server_alert_handler`, etc.) that create the async callbacks wired into monitors.

Key behaviours:
- **First-run path:** If no `config.yaml` exists, calls `register_setup_wizard()` instead of `register_commands()`. `SetupModeMiddleware` blocks all non-wizard commands during setup. The wizard writes config via `ConfigWriter`, then restarts via `os.execv`.
- **Normal path:** Loads config and calls `start_monitoring()` which initializes all monitors.
- `AlertManagerProxy` (`src/alert_proxy.py`) wraps `AlertManager` to lazily resolve chat IDs. Queues alerts until a user sends `/start`.
- `_BackgroundTasks` (`src/background.py`) tracks all running monitors for graceful shutdown.
- `ProviderRegistry` manages LLM providers (Anthropic, OpenAI, Ollama) with per-feature model overrides and JSON persistence.
- `AuthMiddleware` on both message and callback_query dispatchers restricts access to `TELEGRAM_ALLOWED_USERS`.
- **NL chat requires Docker:** The NL handler is only registered when both `nl_processor` and `controller` (Docker client) are available.

### Handler Factory Pattern (Critical)

Every bot command/callback is a **factory function** that captures dependencies via closure and returns the actual async handler. This is the primary dependency injection mechanism — there is no DI container.

```python
# Pattern: factory takes deps, returns handler
def my_command(state: ContainerStateManager, controller: ContainerController) -> Callable:
    async def handler(message: Message) -> None:
        # use state, controller via closure
        ...
    return handler

# Registration in telegram_bot.py:
dp.message.register(my_command(state, controller), Command("mycommand"))
```

To add a new command: (1) create the factory in the appropriate `src/bot/*.py` file, (2) register it in `register_commands()` in `src/bot/telegram_bot.py`.

### Callback Data Conventions

Inline keyboard buttons use `prefix:data` format, parsed with `split(":", 1)` to handle container names containing colons:
- `restart:container_name`, `logs:container_name:line_count` (e.g., `logs:plex:50`), `diagnose:container_name`
- `mute:container_name:duration` (e.g., `mute:plex:60`)
- `ignore_similar:container_name`
- `ctrl_confirm:action:container_name`, `ctrl_cancel`
- `diag_details:container_name`
- `ign_toggle:index`, `ign_all`, `ign_done`, `ign_cancel`
- `mdi:container:index` (manage delete ignore), `mdm:type:key` (manage delete mute)
- `help:section_key`, `help:back`
- `res_limit:container_name:metric:threshold` (show threshold options; the CPU ladder runs to `cores * 100`, from `cpu_threshold_steps()` in `src/constants.py`)
- `res_set:container_name:metric:value` (apply threshold, 0 = reset to default)
- `mem_kill:container_name`, `mem_restart:container_name` (restart to reclaim memory), `mem_restart_yes:container_name`, `mem_restart_no:container_name`, `mem_cancel_kill` (exact match, no container)
- `nl_confirm:action:container` (e.g., `nl_confirm:restart:plex`), `nl_cancel`
- `manage:section` (e.g., `manage:status`, `manage:resources`, `manage:server`, `manage:disks`, `manage:ignores`, `manage:mutes`, `manage:features`, `manage:back`), plus `manage:ignores:container_name` (one container's ignores)
- `feat:notif:on` / `feat:notif:off` (enable/disable the Unraid notification relay; persists + restarts) / `feat:notif:level` (cycle WARNING→ALERT→INFO, applies live)
- `feat:img:on` / `feat:img:off` (enable/disable image updates from /manage → Features; persists + restarts), `feat:heal` (open auto-heal container picker), `feat:memres` (open memory-restart container picker)
- `fh_tog:container_name` (toggle a container in the auto-heal picker), `fh_save` (live-apply auto-heal container list, no restart)
- `mr_tog:container_name` (toggle a container in the memory-restart picker), `mr_save` (live-apply memory restart list, no restart)
- `arr_mute:minutes` (mute array alerts, e.g., `arr_mute:60`)
- `arr_thresh:metric:current` (show array threshold options, e.g., `arr_thresh:capacity:85`)
- `arr_set:metric:value` (apply array threshold, e.g., `arr_set:array_usage:90`, 0 = reset to default)
- `srv_mute:minutes` (mute server alerts, e.g., `srv_mute:60`)
- `srv_thresh:metric:current` (show server threshold options, e.g., `srv_thresh:cpu_temp:80`)
- `srv_set:metric:value` (apply server threshold, e.g., `srv_set:cpu_temp:90`, 0 = reset to default)
- `ups_mute:minutes` (mute UPS alerts, e.g., `ups_mute:60`) — shares `category_mute_callback` with `arr_mute:`/`srv_mute:` (30-day clamp). Mutes the UPS category only, but the undo hint points at `/unmute-server`, which clears system + array + UPS together. There is no UPS-only unmute yet.
- `feat:ups:on` / `feat:ups:off` (enable/disable UPS monitoring from /manage → Features; persists + restarts)
- `model:provider_name`, `model_select:provider:model`, `model:back`
- `setup:confirm`, `setup:toggle:category:container_name` (e.g., `setup:toggle:watched:plex`), `setup:adjust:category`, `setup:adjust_done`
- `pull:container_name` (image-update digest -> shows pull confirmation)

Callback handlers are registered with `F.data.startswith("prefix:")` filters in `register_commands()` and its `_register_*` helpers (same file).

### Handler Registration Order

In `register_commands()` (`src/bot/telegram_bot.py`), order matters:
1. Command handlers (`Command("status")`, etc.)
2. Callback query handlers (`F.data.startswith(...)`)
3. **NL handler must be last** — it catches all non-command text via `NLFilter`
4. **`unhandled_callback` must be the last callback registration** — it has no filter, so anything registered after it is unreachable. It answers callbacks nothing else claimed, because an unanswered callback leaves the button spinning in the client forever.

After registration, `publish_command_menu(bot, dp)` (called from `start_monitoring`) reads the dispatcher's `Command` filters and pushes them to Telegram's "/" menu. A new command shows up automatically once it has an entry in `COMMAND_DESCRIPTIONS` (`src/bot/command_menu.py`); hyphenated names like `/mute-server` work when typed but cannot appear in the menu.

**A disk status is not a verdict without its context.** `DISK_INVALID` means "being written to" during a parity sync and "broken" outside one; the array API has no per-disk rebuild flag, so `parityCheckStatus` is the only way to tell. Alerting on `status != DISK_OK` alone paged the user for a routine rebuild (confirmed live, v0.20.0). Related: the same payload returns `running`/`paused`/`errors` as **null** while `status` correctly reads RUNNING — verify which fields a live server actually populates before keying logic on them.

**A rendered button needs a registered handler in the same conditions.** Alerts are built in `src/monitor_callbacks.py` from one set of flags and handlers are registered in `register_commands()` from another; when those disagree the button silently does nothing. `mem_kill:` is the cautionary tale — it was rendered whenever Unraid monitoring was on but registered only when `memory_management.enabled`. `MemoryMonitor` is now constructed unconditionally (inert until `start()`) with `bg.memory_monitor` as the sole signal for "the pressure loop is running".

**A reply arrives with the formatting stripped.** Telegram puts the plain text in `Message.text` and the markup in `entities`, so an alert sent as `*ERRORS IN:* plex` comes back as `ERRORS IN: plex`. Any pattern matching a replied-to alert must target the rendered text, never the source. `/diagnose` kept a private pattern list that required literal asterisks and therefore matched nothing in production for months, while its tests passed because they fed it raw source strings. One shared list now lives in `extract_alert_container()` (`src/utils/formatting.py`), and `tests/test_alert_reply_extraction.py` drives real `AlertManager` sends through a render step so a headline change in `manager.py` fails a test.

Two rules that list depends on: anchor patterns to the start of a line, and require the label's colon and real case. Unanchored case-insensitive matching extracted the container name `"4"` from "Crashed 4 times in the last 10 minutes" and `"was"` from "Container was unhealthy".

**CPU percentages are summed across cores, not capped at 100.** Docker reports a container using four full cores as 400%, so an 8-core host tops out at 800%. Anything that renders or bounds a CPU figure has to know the core count: the threshold picker (`cpu_threshold_steps(cores)`), the progress bars (`format_progress_bar(..., max_percent=cores * 100)`) and the "approaching threshold" marker in `/resources`, which now compares against each container's own threshold instead of a hardcoded 70. The core count comes from `ContainerStats.cpu_cores` (Docker's `online_cpus`) where stats are at hand, and from `host_cpu_cores()` at command registration where they are not.

**Every monitor state must handle both directions, and shared limiters need distinct keys.** The 2026-09-22 audit found four ways alerting went quiet for good, none visible to single-transition tests: the memory monitor parked in RECOVERING or CRITICAL with no way out; a log-error alert silencing the crash alert that followed because both keyed the shared `RateLimiter` on the bare container name (crashes now use `name:crash`, resources `name:metric`); UPS flags raised during a mute never alerting after it; and the log watcher re-following a stopped container 4,645 times in half a second because it only slept on exceptions. Test sequences (critical, kill, decline, critical again), not single steps.

**Destructive quick actions confirm, same as the commands.** An alert sits in the chat for days, so a one-tap Restart on a stale alert is a mis-tap waiting to happen. The `restart:` alert button routes through `build_confirmation()` and the shared `ctrl_confirm:` handler, exactly like `/restart`.

### Telegram Message Formatting

Messages use Markdown parse mode. Use `safe_reply()` and `safe_edit()` from `src/utils/formatting.py` which automatically catch `TelegramBadRequest` and fall back to plain text. Use `escape_markdown()` for dynamic content.

### LLM Provider Architecture

`ProviderRegistry` is the single source of truth for which LLM provider/model to use. AI consumers call `registry.get_provider(feature="...")` and receive a ready-to-use `LLMProvider` instance.

- **`LLMProvider` protocol** (`src/services/llm/provider.py`) — normalized `chat()` interface returning `LLMResponse` with `.text`, `.stop_reason`, `.tool_calls`
- **Provider implementations** — `AnthropicProvider`, `OpenAIProvider`, `OllamaProvider` translate the normalized interface to each SDK's format
- **Tool call translation** — Anthropic uses `input_schema`/content blocks; OpenAI uses `function`/`parameters`; normalized via `ToolCall` dataclass
- **Model discovery** — Ollama models discovered at startup via `/api/tags` endpoint
- **Per-feature overrides** — `feature_models` dict in config allows different models per AI feature (e.g., cheap model for pattern analysis, capable model for NL chat)
- **Model families** — Config and `/model` accept family names (`sonnet`, `haiku`, `opus`) resolved to latest available model at startup via `client.models.list()` (hardcoded fallback if API unavailable)
- **Runtime switching** — `/model` sets global default; `/model <feature> <model>` sets per-feature override (features: `chat`, `diagnose`, `analyze`); `/model <feature> default` resets to global. Persisted to `data/model_selection.json`
- **Auto-selection priority** — When no default is set: anthropic (sonnet) → openai (gpt-4o) → ollama (first available)

### Patterns

- **All async** - Every component uses async/await. Tests use `pytest-asyncio` with `asyncio_mode = "auto"`
- **Partial name matching** - Container commands accept partial names (e.g., `/logs rad` matches `radarr`)
- **Graceful degradation** - Bot works without any LLM API keys; AI features just disable. Models without tool support get a note appended to NL responses
- **JSON persistence** - Mutes and ignores stored in `data/*.json` files (IgnoreManager uses `batch_updates()` context manager to defer saves)
- **Protected containers** - Listed in `config.yaml`, cannot be controlled via Telegram
- **Confirmation prompts** - Destructive actions (restart, stop, start, pull) require inline button confirmation (✅ Confirm / ❌ Cancel)
- **Europe/London timezone** - Default timezone for displayed timestamps (configurable via `TZ` env var)
- **Prompt caching** - Anthropic API calls use `cache_control` on system prompts and tool definitions for cost savings

### Testing Conventions

- No `conftest.py` — each test file imports its own helpers and creates mocks inline for full isolation
- Mock pattern: `MagicMock` for sync objects, `AsyncMock` for async methods (e.g., `message.answer = AsyncMock()`)
- Test the factory return value directly: `handler = my_command(state); await handler(message)`
- `ContainerInfo` and `ContainerStateManager` are constructed directly in tests (no fixtures)

## Terminal Multiplexer (Orca)

This project runs inside **Orca**. Use `orca --help` for the full list, and prefer these over tmux/screen:

- `orca terminal create` / `orca terminal split` to open a pane for the bot (`uv run python -m src.main`) next to a test run (`uv run pytest tests/`)
- `orca terminal list`, `orca terminal read` and `orca terminal wait` to check output or wait for a run to finish
- `orca terminal send --terminal <handle> --text "..." --enter` to type into a live pane

Work in the main checkout on a feature branch. Do not create worktrees (`orca worktree create` or `git worktree add`), a hook blocks them.

## Environment Variables

```bash
TELEGRAM_BOT_TOKEN=           # Required - from @BotFather
TELEGRAM_ALLOWED_USERS=       # Required - comma-separated Telegram user IDs (e.g., 123456,789012)
ANTHROPIC_API_KEY=            # Optional - enables AI features via Claude models
OPENAI_API_KEY=               # Optional - enables AI features via OpenAI models
OLLAMA_HOST=                  # Optional - Ollama server URL (default: http://localhost:11434)
DEFAULT_MODEL=                # Optional - override default AI model (e.g. qwen2.5:7b, gpt-4o)
UNRAID_API_KEY=               # Optional - enables /server, /array, /disks commands
NUT_USERNAME=                 # Optional - only if your NUT server gates reads
NUT_PASSWORD=                 # Optional - only if your NUT server gates reads
CONFIG_PATH=                  # Optional - defaults to config/config.yaml
LOG_LEVEL=                    # Optional - defaults to INFO
TZ=                           # Optional - timezone for timestamps (default: Europe/London)
```

Environment variables can also be set via `config/.env` (loaded by pydantic-settings).

## Configuration

`config/config.yaml` - Created by the setup wizard on first run (or via `/setup`). Key sections:
- `ai` - LLM model names, token limits, NL processor settings
- `log_watching` - Watched containers, error/ignore patterns, cooldown
- `unraid` - Host, polling intervals, alert thresholds (CPU temp, disk temp, memory, etc.)
- `protected_containers` / `ignored_containers` - Safety and visibility controls
- `memory_management` - System memory pressure thresholds, kill policy, and `restart_containers` (offered a one-tap Restart button on pressure alerts; edited via /manage → Features)
- `resource_monitoring` - Per-container CPU/memory alert thresholds
- `image_updates` - Opt-in daily digest of containers with newer images (enabled, poll_interval_hours)
- `unraid.notifications` - Opt-in relay of Unraid's own notification feed (enabled, min_importance); floor cycles live from /manage → Features, enable/disable needs a restart
- `auto_heal` - Opt-in auto-restart of unhealthy containers (enabled, containers, max_restarts, window_minutes)
- `nut` - UPS monitoring over NUT (enabled, host, port, ups_name, poll_seconds, thresholds.battery_charge, thresholds.load). On by default but dormant: `host` falls back to the Unraid host, and a host that never answers is logged, not alerted about, so it stays quiet for the majority who run no UPS.

Data files in `data/`: `mutes.json`, `server_mutes.json`, `array_mutes.json`, `ignored_errors.json`, `model_selection.json` (runtime LLM choice), `chat_ids.json` (persistent Telegram chat IDs for alert delivery), `announced_version.json` (last-announced bot version for startup What's new gate), `announced_updates.json` (image-update dedup map so restarts don't re-announce), `announced_notifications.json` (relayed Unraid notification ids, bounded at 500)

## Design Context

See `.impeccable.md` for the full design system (brand personality, color palette, emotional goals, references). Key principles: clarity over cleverness, action-oriented alerts with buttons, respectful of attention (precise severity levels), professional warmth, progressive disclosure.
