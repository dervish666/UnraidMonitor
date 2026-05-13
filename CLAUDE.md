# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Codebase Structure Index

The file map below provides instant orientation. For detailed export signatures and dependencies, read the relevant `.claude/structure/*.yaml` file for the directory you're working in (files: alerts.yaml, bot.yaml, llm.yaml, monitors.yaml, root.yaml, services.yaml, unraid.yaml, utils.yaml).

After adding, removing, or renaming source files or public classes/functions, update both the file map below and the relevant structure YAML file.

### File Map

<!-- One line per source file: relative path - brief description -->

# Root
src/main.py - Entry point, signal handling, wizard setup (delegates to startup.py)
src/startup.py - start_monitoring() orchestrator, provider/monitor initialization
src/alert_proxy.py - AlertManagerProxy for multi-user alert delivery with queuing
src/background.py - _BackgroundTasks tracker for graceful shutdown of monitors
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
src/bot/telegram_bot.py - Bot initialization, dispatcher, command registration
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
src/bot/health_command.py - /health command showing bot version, uptime, monitor status
src/bot/setup_wizard.py - Interactive first-run setup for Unraid and containers
src/bot/memory_commands.py - /cancel-kill command for canceling memory-based kills
src/bot/model_command.py - /model command for runtime LLM provider/model switching

# Monitors
src/monitors/docker_events.py - DockerEventMonitor with CrashTracker for container events
src/monitors/log_watcher.py - LogWatcher for streaming container logs, error detection
src/monitors/resource_monitor.py - ResourceMonitor for per-container CPU/memory polling
src/monitors/memory_monitor.py - MemoryMonitor for system memory pressure management

# Services
src/services/docker_client.py - SharedDockerClient wrapper for reconnectable Docker access
src/services/nl_processor.py - NLProcessor for Claude-powered natural language chat
src/services/nl_tools.py - Tool definitions and executor for Claude tool use
src/services/container_control.py - ContainerController for safe restart/stop/start
src/services/container_classifier.py - ContainerClassifier using patterns and AI for roles
src/services/diagnostic.py - DiagnosticService for AI container log analysis

# Unraid
src/unraid/client.py - UnraidClientWrapper with direct GraphQL API access
src/unraid/monitors/system_monitor.py - UnraidSystemMonitor for CPU/memory/temp alerts
src/unraid/monitors/array_monitor.py - ArrayMonitor for disk health and usage alerts

# Utils
src/utils/api_errors.py - LLM API error handling (Anthropic + OpenAI) with user-friendly messages
src/utils/formatting.py - Bytes/uptime formatting, safe_reply/safe_edit, format_mute_expiry
src/utils/rate_limiter.py - PerUserRateLimiter for per-user API rate limiting
src/utils/sanitize.py - Prompt injection prevention, sensitive data redaction
src/utils/telegram_retry.py - Telegram API retry logic for rate limit handling

## Project Overview

Unraid Server Monitor Bot (v0.9.7) - A Docker-based Telegram bot for monitoring Unraid servers. Monitors Docker containers (events, logs, resources) and Unraid server health (CPU, memory, disks, array, UPS). Uses multi-provider LLM support (Anthropic, OpenAI, Ollama) for AI-powered diagnostics and natural language interaction. Sends alerts via Telegram with quick-action buttons.

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
- `restart:container_name`, `logs:container_name`, `diagnose:container_name`
- `mute:container_name:duration` (e.g., `mute:plex:3600`)
- `ignore_similar:container_name`
- `ctrl_confirm:action:container_name`, `ctrl_cancel`
- `diag_details:container_name`
- `ign_toggle:index`, `ign_all`, `ign_done`, `ign_cancel`
- `mdi:container:index` (manage delete ignore), `mdm:type:key` (manage delete mute)
- `help:section_key`, `help:back`
- `res_limit:container_name:metric:threshold` (show threshold options)
- `res_set:container_name:metric:value` (apply threshold, 0 = reset to default)
- `mem_kill:container_name`, `mem_restart_yes:container_name`
- `nl_confirm:action_id`, `nl_cancel`
- `manage:section` (e.g., `manage:status`, `manage:ignores`, `manage:back`)
- `arr_mute:minutes` (mute array alerts, e.g., `arr_mute:60`)
- `arr_thresh:metric:current` (show array threshold options, e.g., `arr_thresh:capacity:85`)
- `arr_set:metric:value` (apply array threshold, e.g., `arr_set:array_usage:90`, 0 = reset to default)
- `model:provider_name`, `model_select:provider:model`, `model:back`
- `setup:confirm`, `setup:toggle:container_name`, `setup:adjust:category`

Callback handlers are registered with `F.data.startswith("prefix:")` filters in `register_commands()`.

### Handler Registration Order

In `register_commands()` (`src/bot/telegram_bot.py`), order matters:
1. Command handlers (`Command("status")`, etc.)
2. Callback query handlers (`F.data.startswith(...)`)
3. **NL handler must be last** — it catches all non-command text via `NLFilter`

### Telegram Message Formatting

Messages use Markdown parse mode. Use `safe_reply()` and `safe_edit()` from `src/utils/formatting.py` which automatically catch `TelegramBadRequest` and fall back to plain text. Use `escape_markdown()` for dynamic content.

### LLM Provider Architecture

`ProviderRegistry` is the single source of truth for which LLM provider/model to use. AI consumers call `registry.get_provider(feature="...")` and receive a ready-to-use `LLMProvider` instance.

- **`LLMProvider` protocol** (`src/services/llm/provider.py`) — normalized `chat()` interface returning `LLMResponse` with `.text`, `.stop_reason`, `.tool_calls`
- **Provider implementations** — `AnthropicProvider`, `OpenAIProvider`, `OllamaProvider` translate the normalized interface to each SDK's format
- **Tool call translation** — Anthropic uses `input_schema`/content blocks; OpenAI uses `function`/`parameters`; normalized via `ToolCall` dataclass
- **Model discovery** — Ollama models discovered at startup via `/api/tags` endpoint
- **Per-feature overrides** — `feature_models` dict in config allows different models per AI feature (e.g., cheap model for pattern analysis, capable model for NL chat)
- **Runtime switching** — `/model` command changes the global default; persisted to `data/model_selection.json`
- **Auto-selection priority** — When no default is set: anthropic → openai → ollama (first available)

### Patterns

- **All async** - Every component uses async/await. Tests use `pytest-asyncio` with `asyncio_mode = "auto"`
- **Partial name matching** - Container commands accept partial names (e.g., `/logs rad` matches `radarr`)
- **Graceful degradation** - Bot works without any LLM API keys; AI features just disable. Models without tool support get a note appended to NL responses
- **JSON persistence** - Mutes and ignores stored in `data/*.json` files with `batch_updates()` context manager to defer saves
- **Protected containers** - Listed in `config.yaml`, cannot be controlled via Telegram
- **Confirmation prompts** - Destructive actions (restart, stop, start, pull) require inline button confirmation (✅ Confirm / ❌ Cancel)
- **Europe/London timezone** - All displayed timestamps use this timezone
- **Prompt caching** - Anthropic API calls use `cache_control` on system prompts and tool definitions for cost savings

### Testing Conventions

- No `conftest.py` — each test file imports its own helpers and creates mocks inline for full isolation
- Mock pattern: `MagicMock` for sync objects, `AsyncMock` for async methods (e.g., `message.answer = AsyncMock()`)
- Test the factory return value directly: `handler = my_command(state); await handler(message)`
- `ContainerInfo` and `ContainerStateManager` are constructed directly in tests (no fixtures)

## Terminal Multiplexer (cmux)

This project uses **cmux** as the terminal multiplexer. Use `cmux --help` to discover available commands and maximise usage (e.g., `list-workspaces`, `new-split`, `read-screen`, `send`, `send-key`, `list-panes`, etc.). Prefer cmux commands over tmux/screen equivalents.

## Environment Variables

```bash
TELEGRAM_BOT_TOKEN=           # Required - from @BotFather
TELEGRAM_ALLOWED_USERS=       # Required - comma-separated Telegram user IDs (e.g., 123456,789012)
ANTHROPIC_API_KEY=            # Optional - enables AI features via Claude models
OPENAI_API_KEY=               # Optional - enables AI features via OpenAI models
OLLAMA_HOST=                  # Optional - Ollama server URL (default: http://localhost:11434)
DEFAULT_MODEL=                # Optional - override default AI model (e.g. qwen2.5:7b, gpt-4o)
UNRAID_API_KEY=               # Optional - enables /server, /array, /disks commands
CONFIG_PATH=                  # Optional - defaults to config/config.yaml
LOG_LEVEL=                    # Optional - defaults to INFO
```

## Configuration

`config/config.yaml` - Created by the setup wizard on first run (or via `/setup`). Key sections:
- `ai` - LLM model names, token limits, NL processor settings
- `log_watching` - Watched containers, error/ignore patterns, cooldown
- `unraid` - Host, polling intervals, alert thresholds (CPU temp, disk temp, memory, etc.)
- `protected_containers` / `ignored_containers` - Safety and visibility controls
- `memory_management` - System memory pressure thresholds and kill policy
- `resource_monitoring` - Per-container CPU/memory alert thresholds

Data files in `data/`: `muted_containers.json`, `server_mutes.json`, `array_mutes.json`, `ignore_patterns.json`, `model_selection.json` (runtime LLM choice)

## Design Context

See `.impeccable.md` for the full design system (brand personality, color palette, emotional goals, references). Key principles: clarity over cleverness, action-oriented alerts with buttons, respectful of attention (precise severity levels), professional warmth, progressive disclosure.
