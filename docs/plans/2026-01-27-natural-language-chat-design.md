# Natural Language Chat Design

## Overview

Add natural language chat to the Unraid Monitor Bot. Users can ask open questions like "what's wrong with plex?" or "why is my server so slow?" instead of memorizing commands. Any text that isn't a `/command` is processed by Claude, which gathers relevant data and responds conversationally.

## Goals

- Handle both troubleshooting ("what's wrong with X?") and general queries ("how's everything running?")
- Gather information using predefined tool functions
- Execute actions (restart, stop, start, pull) with user confirmation
- Support follow-up questions with short conversation memory

## Architecture

```
User Message
     │
     ▼
┌─────────────────┐
│ Telegram Bot    │
│ Message Handler │
└────────┬────────┘
         │
         ▼
   Is it a /command?
    ┌────┴────┐
   Yes        No
    │          │
    ▼          ▼
 Existing   ┌──────────────┐
 Commands   │ NL Processor │
            └──────┬───────┘
                   │
                   ▼
            ┌─────────────┐
            │ Claude API  │
            │ (with tools)│
            └──────┬──────┘
                   │
          ┌────────┴────────┐
          ▼                 ▼
    Tool calls          Response
    (gather data,       to user
     propose actions)
```

### New Components

| File | Purpose |
|------|---------|
| `src/bot/nl_handler.py` | Routes non-command messages to NL processor |
| `src/services/nl_processor.py` | Manages Claude conversation with tool definitions |
| `src/services/nl_tools.py` | Tool implementations wrapping existing service code |

### Key Principle

NL tools reuse existing code from `container_control.py`, `diagnostic.py`, and the monitors. No duplication of Docker/Unraid logic.

## Tool Definitions

### Read-only Tools

| Tool | Parameters | Returns |
|------|------------|---------|
| `get_container_list` | none | List of all containers with status (running/stopped) |
| `get_container_status` | `name: str` | Detailed status: uptime, image, restart count, health |
| `get_container_logs` | `name: str, lines: int = 50` | Recent log lines |
| `get_resource_usage` | `name: str?` | CPU/memory for one or all containers |
| `get_server_stats` | none | Unraid CPU, memory, temperatures |
| `get_array_status` | none | Array health, disk status |
| `get_recent_errors` | `name: str?` | Recent logged errors from the alert system |

### Action Tools

| Tool | Parameters | Behavior |
|------|------------|----------|
| `restart_container` | `name: str` | Triggers confirmation flow |
| `stop_container` | `name: str` | Triggers confirmation flow |
| `start_container` | `name: str` | Executes immediately (safe) |
| `pull_container` | `name: str` | Triggers confirmation flow |

### Tool Behavior

- Container `name` uses partial matching ("rad" → "radarr")
- Action tools on protected containers return an error message
- `start_container` skips confirmation (starting a stopped container is safe)

## Conversation Memory

### Structure

```python
class ConversationMemory:
    user_id: int
    messages: list[dict]  # Last 5 exchanges (user + assistant pairs)
    last_activity: datetime
    pending_action: dict | None  # Action awaiting confirmation
```

Memory stored in-memory (dict by user_id). Lost on restart, which is acceptable for short-lived conversations.

### Message Flow

1. User sends "what's wrong with plex?"
2. `nl_handler.py` detects non-command, retrieves/creates memory for user
3. `nl_processor.py` builds prompt with system prompt, conversation history (up to 5 exchanges), and current message
4. Claude responds, possibly calling tools
5. Tool results fed back to Claude for final response
6. If action tool called, set `pending_action` and return confirmation buttons
7. Response sent to user, conversation memory updated
8. Entries older than 5 exchanges dropped

### Confirmation Handling

When user clicks "Yes" on confirmation button:
- Check `pending_action` matches
- Execute the action
- Clear `pending_action`
- Add result to conversation ("Done, plex has been restarted")

## System Prompt

```
You are an assistant for monitoring an Unraid server. You help users
understand what's happening with their Docker containers and server,
and can take actions to fix problems.

## Your capabilities
- Check container status, logs, and resource usage
- View server stats (CPU, memory, temperatures)
- Check array and disk health
- Restart, stop, start, or pull containers (with user confirmation)

## Guidelines
- Be concise. Users are on mobile Telegram.
- When investigating issues, gather relevant data before responding.
- For "what's wrong" questions: check status, recent errors, and logs.
- For performance questions: check resource usage first.
- Suggest actions when appropriate, but explain why.
- If a container is protected, explain you can't control it.
- If you can't help, suggest relevant /commands.

## Container name matching
Partial names work: "plex", "rad" for "radarr", etc.
```

### Response Style

- Keep responses under ~500 characters when possible (Telegram readability)
- Use emoji sparingly to match existing bot style
- When proposing actions, be clear: "I can restart plex if you'd like"

## Error Handling

### LLM API Failures

- Respond: "Sorry, I can't process that right now. Try using /commands instead."
- Log the error
- No automatic retry

### Tool Execution Failures

- Return error to Claude
- Claude explains naturally: "I couldn't find a container matching 'plox'. Did you mean plex?"

### Unrecognized Queries

- Suggest relevant commands: "I'm not sure how to help with that. You might try `/status` or `/help`."

### Ambiguous Container Names

- Same as existing commands: return error listing matches
- Claude presents options: "I found multiple matches: radarr, radar-sync. Which one?"

### Pending Action Conflicts

- New question clears pending action
- User has moved on

### Message Too Long

- Truncate with "... (showing last N lines)" to stay within Telegram limits (~4096 chars)

## Testing

### Unit Tests (`tests/test_nl_processor.py`)

- Tool definitions are valid schema
- Conversation memory: adding, trimming to 5, clearing
- Pending action state management
- Mock Claude responses to test tool call parsing

### Tool Tests (`tests/test_nl_tools.py`)

- Each tool function tested independently
- Protected container blocking
- Partial name matching
- Mock Docker/Unraid APIs

### Integration Tests (`tests/test_nl_integration.py`)

- End-to-end with mocked Claude API
- Question → tool calls → response
- Action → confirmation → execution
- Follow-up uses conversation context

### Manual Testing Scenarios

1. "What's wrong with plex?" - checks status, errors, logs
2. "Why is the server slow?" - checks resource usage, server stats
3. "Restart it" (after discussing plex) - knows "it" = plex
4. "Stop mariadb" - refuses (protected)

## Implementation Summary

### New Files

| File | Purpose | Lines (est.) |
|------|---------|--------------|
| `src/bot/nl_handler.py` | Route non-commands to NL processor | ~50 |
| `src/services/nl_processor.py` | Claude conversation management | ~200 |
| `src/services/nl_tools.py` | Tool implementations | ~150 |
| `tests/test_nl_processor.py` | Unit tests | ~150 |
| `tests/test_nl_tools.py` | Tool tests | ~100 |
| `tests/test_nl_integration.py` | Integration tests | ~100 |

### Modified Files

| File | Change |
|------|--------|
| `src/bot/telegram_bot.py` | Add catch-all handler for non-commands |
| `src/main.py` | Initialize NL processor with config |
| `CLAUDE.md` | Document NL capability |

### Dependencies

None - already using `anthropic` SDK for diagnostics.

### Configuration

No changes needed - uses existing `protected_containers` and `ANTHROPIC_API_KEY`.
