# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Codebase Structure Index

The file map below provides instant orientation. For detailed export signatures and dependencies, read the relevant `.claude/structure/*.yaml` file for the directory you're working in.

After adding, removing, or renaming source files or public classes/functions, update both the file map below and the relevant structure YAML file.

### File Map

<!-- One line per source file: relative path - brief description -->

# Root
src/main.py - Composition root, bot startup, monitor wiring, AlertManagerProxy
src/config.py - Settings, configuration loading, YAML parsing, ConfigWriter
src/models.py - ContainerInfo dataclass for Docker container state
src/state.py - ContainerStateManager for thread-safe container state tracking

# Alerts
src/alerts/manager.py - AlertManager for sending formatted alerts to Telegram
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
src/bot/commands.py - /help, /status, /logs command handlers
src/bot/control_commands.py - /restart, /stop, /start, /pull with confirmations
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
src/bot/confirmation.py - ConfirmationManager for pending control action confirmations
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
src/utils/formatting.py - Bytes/uptime formatting, container name extraction
src/utils/rate_limiter.py - PerUserRateLimiter for per-user API rate limiting
src/utils/sanitize.py - Prompt injection prevention, sensitive data redaction
src/utils/telegram_retry.py - Telegram API retry logic for rate limit handling

## Project Overview

Unraid Server Monitor Bot (v0.8.3) - A Docker-based Telegram bot for monitoring Unraid servers. Monitors Docker containers (events, logs, resources) and Unraid server health (CPU, memory, disks, array, UPS). Uses multi-provider LLM support (Anthropic, OpenAI, Ollama) for AI-powered diagnostics and natural language interaction. Sends alerts via Telegram with quick-action buttons.

## Commands

```bash
# Run the application
python -m src.main

# Run tests (uses pytest-asyncio with auto mode)
pytest tests/
pytest tests/test_<module>.py
pytest tests/test_<module>.py -k "test_name"
pytest --cov=src tests/

# Type checking (strict mode, Python 3.11)
mypy src/

# Linting (line-length 100, target py311)
ruff check src/

# Docker (target is Unraid x86_64 -- always build for linux/amd64)
docker buildx build --platform linux/amd64 -t dervish/unraidmonitorbot:latest --push .
docker-compose up -d
```

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

### Startup & Wiring (`src/main.py`)

`main.py` is the composition root. It instantiates all components and wires them together:
- **First-run path:** If no `config.yaml` exists, starts the setup wizard which guides users through Unraid connection and container classification via Telegram, then restarts via `os.execv`
- **Normal path:** Loads config and starts all monitors immediately
- `AlertManagerProxy` wraps `AlertManager` to lazily resolve the Telegram chat ID (set on first `/start` command). Queues up to 50 alerts until a user sends `/start`, then delivers them.
- Background tasks for each monitor run concurrently via `asyncio.create_task`
- Telegram bot uses aiogram 3.x polling
- `ProviderRegistry` manages LLM providers (Anthropic, OpenAI, Ollama) with per-feature model overrides and JSON persistence
- `AuthMiddleware` on both message and callback_query dispatchers restricts access to `TELEGRAM_ALLOWED_USERS`

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
- `mem_kill:container_name`, `mem_restart_yes:container_name`
- `nl_confirm:action_id`, `nl_cancel`
- `manage:section` (e.g., `manage:status`, `manage:ignores`)
- `model_provider:provider_name`, `model_select:provider:model`, `model:back`
- `setup:confirm`, `setup:toggle:container_name`, `setup:adjust:category`

Callback handlers are registered with `F.data.startswith("prefix:")` filters in `register_commands()`.

### Handler Registration Order

In `register_commands()` (`src/bot/telegram_bot.py`), order matters:
1. Command handlers (`Command("status")`, etc.)
2. Custom filter handlers (YesFilter, DetailsFilter, IgnoreSelectionFilter, ManageSelectionFilter)
3. Callback query handlers (`F.data.startswith(...)`)
4. **NL handler must be last** — it catches all non-command text via `NLFilter`

Stateful filters (IgnoreSelectionFilter, ManageSelectionFilter) only match when the user has a pending selection, preventing them from swallowing unrelated numeric input.

### Telegram Message Formatting

Messages use Markdown parse mode. Always wrap in try/except for `TelegramBadRequest` with `"can't parse entities"`, falling back to plain text. Use `escape_markdown()` from `src/utils/formatting.py` for dynamic content.

### LLM Provider Architecture

`ProviderRegistry` is the single source of truth for which LLM provider/model to use. AI consumers call `registry.get_provider(feature="...")` and receive a ready-to-use `LLMProvider` instance.

- **`LLMProvider` protocol** (`src/services/llm/provider.py`) — normalized `chat()` interface returning `LLMResponse` with `.text`, `.stop_reason`, `.tool_calls`
- **Provider implementations** — `AnthropicProvider`, `OpenAIProvider`, `OllamaProvider` translate the normalized interface to each SDK's format
- **Tool call translation** — Anthropic uses `input_schema`/content blocks; OpenAI uses `function`/`parameters`; normalized via `ToolCall` dataclass
- **Model discovery** — Ollama models discovered at startup via `/api/tags` endpoint
- **Per-feature overrides** — `feature_models` dict in config allows different models per AI feature (e.g., cheap model for pattern analysis, capable model for NL chat)
- **Runtime switching** — `/model` command changes the global default; persisted to `data/model_selection.json`

### Patterns

- **All async** - Every component uses async/await. Tests use `pytest-asyncio` with `asyncio_mode = "auto"`
- **Partial name matching** - Container commands accept partial names (e.g., `/logs rad` matches `radarr`)
- **Graceful degradation** - Bot works without any LLM API keys; AI features just disable. Models without tool support get a note appended to NL responses
- **JSON persistence** - Mutes and ignores stored in `data/*.json` files with `batch_updates()` context manager to defer saves
- **Protected containers** - Listed in `config.yaml`, cannot be controlled via Telegram
- **Confirmation prompts** - Destructive actions (restart, stop, pull) require button confirmation via `ConfirmationManager`
- **Europe/London timezone** - All displayed timestamps use this timezone
- **Prompt caching** - Anthropic API calls use `cache_control` on system prompts and tool definitions for cost savings

### Testing Conventions

- No `conftest.py` — tests are self-contained with inline imports
- Mock pattern: `MagicMock` for sync objects, `AsyncMock` for async methods (e.g., `message.answer = AsyncMock()`)
- Test the factory return value directly: `handler = my_command(state); await handler(message)`
- `ContainerInfo` and `ContainerStateManager` are constructed directly in tests (no fixtures)

## Environment Variables

```bash
TELEGRAM_BOT_TOKEN=           # Required - from @BotFather
TELEGRAM_ALLOWED_USERS=       # Required - comma-separated Telegram user IDs (e.g., 123456,789012)
ANTHROPIC_API_KEY=            # Optional - enables AI features via Claude models
OPENAI_API_KEY=               # Optional - enables AI features via OpenAI models
OLLAMA_HOST=                  # Optional - Ollama server URL (default: http://localhost:11434)
UNRAID_API_KEY=               # Optional - enables /server, /array, /disks commands
CONFIG_PATH=                  # Optional - defaults to config/config.yaml
LOG_LEVEL=                    # Optional - defaults to INFO
```

## Configuration

`config/config.yaml` - Created by the setup wizard on first run (or via `/setup`). Key sections:
- `ai` - Claude model names, token limits, NL processor settings
- `log_watching` - Watched containers, error/ignore patterns, cooldown
- `unraid` - Host, polling intervals, alert thresholds (CPU temp, disk temp, memory, etc.)
- `protected_containers` / `ignored_containers` - Safety and visibility controls
- `memory_management` - System memory pressure thresholds and kill policy
- `resource_monitoring` - Per-container CPU/memory alert thresholds
