# AI Diagnostics Design

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:writing-plans to create the implementation plan.

**Goal:** Add `/diagnose` command that uses Claude API to analyze container crashes and provide actionable fix suggestions.

**Architecture:** DiagnosticService gathers logs + metadata, sends to Claude Haiku, returns brief summary with optional detailed follow-up.

**Tech Stack:** Python 3.11+, anthropic SDK, aiogram (existing), docker SDK (existing)

---

## Command Usage

| Usage | Description |
|-------|-------------|
| `/diagnose <name>` | Analyze any container by name |
| `/diagnose <name> <lines>` | Analyze with custom log line count |
| Reply `/diagnose` to crash alert | Extract container from alert message |

Default: 50 log lines. User can request more: `/diagnose overseerr 200`

---

## Data Collected

| Field | Source |
|-------|--------|
| Container name | Command argument or alert message |
| Last N log lines | `container.logs(tail=N)` |
| Exit code | `container.attrs['State']['ExitCode']` |
| Image | `container.image.tags[0]` |
| Uptime before exit | `container.attrs['State']['StartedAt']` |
| Restart count | `container.attrs['RestartCount']` |

---

## Response Format

**Initial response (brief):**
```
🔍 *Diagnosis: overseerr*

The container crashed due to a database connection timeout. MariaDB
appears to have been unreachable when overseerr started. Restart
MariaDB first, then restart overseerr.

_Want more details?_
```

**Follow-up response (if requested):**
```
📋 *Detailed Analysis*

*Root Cause:*
The logs show "SQLITE_BUSY" errors followed by connection pool
exhaustion...

*Fix Steps:*
1. Check MariaDB status: `/status mariadb`
2. Restart MariaDB: `/restart mariadb`
3. Wait 30 seconds for it to initialize
4. Restart overseerr: `/restart overseerr`

*Prevention:*
Add a health check dependency in docker-compose...
```

---

## Architecture

### New Components

| Component | Location | Purpose |
|-----------|----------|---------|
| `DiagnosticService` | `src/services/diagnostic.py` | Gather context, call Claude API, format response |
| `diagnose_command` | `src/bot/commands.py` | Handle command, parse args, detect reply context |
| `DetailsFilter` | `src/bot/telegram_bot.py` | Detect "yes"/"more"/"details" follow-up |

### DiagnosticService

```python
class DiagnosticService:
    def __init__(self, docker_client, anthropic_client):
        self._docker = docker_client
        self._anthropic = anthropic_client
        self._pending: dict[int, DiagnosticContext] = {}  # user_id -> context

    async def diagnose(self, container_name: str, lines: int = 50) -> DiagnosticResult:
        """Gather context and get brief analysis."""

    async def get_details(self, user_id: int) -> str | None:
        """Get detailed follow-up for user's last diagnosis."""

    def store_context(self, user_id: int, context: DiagnosticContext) -> None:
        """Store context for potential follow-up."""

    def clear_stale_contexts(self) -> None:
        """Clear contexts older than 10 minutes."""
```

### Claude Prompts

**Brief analysis:**
```
You are a Docker container diagnostics assistant. Analyze this container issue and provide a brief, actionable summary.

Container: {name}
Image: {image}
Exit Code: {exit_code}
Uptime before exit: {uptime}
Restart Count: {restart_count}

Last {n} log lines:
```
{logs}
```

Respond with 2-3 sentences: What happened, the likely cause, and how to fix it. Be specific and actionable. If you see a clear command to run, include it.
```

**Detailed follow-up:**
```
Based on your previous analysis, provide:
1. Detailed root cause analysis
2. Step-by-step fix instructions
3. How to prevent this in future

Container: {name}
Your brief analysis: {brief_summary}

Logs:
```
{logs}
```
```

---

## Reply Detection

To extract container name from replied-to crash alert:

1. Check if `message.reply_to_message` exists
2. Parse replied message for "Container Crash: {name}" pattern
3. Extract container name

**Edge cases:**
- No reply + no argument → Show usage help
- Reply to non-alert → "Please reply to a crash alert or use `/diagnose <container>`"
- Both reply and argument → Use explicit argument

---

## Configuration

**Environment (.env):**
```
ANTHROPIC_API_KEY=sk-ant-...  # Already exists
```

**Optional config.yaml:**
```yaml
diagnostics:
  default_log_lines: 50
  model: claude-3-haiku-20240307
```

---

## Follow-up Detection

**DetailsFilter** matches messages like:
- "yes", "more", "details", "expand", "tell me more"
- Case-insensitive, trimmed

**Flow:**
1. User sends "yes" (or similar)
2. DetailsFilter matches
3. Handler checks if user has pending diagnosis context
4. If yes: fetch details, respond, clear context
5. If no: ignore (don't respond - might be unrelated conversation)

---

## Cost Estimate

Using Claude 3 Haiku:
- Input: ~$0.25 / million tokens
- Output: ~$1.25 / million tokens

Per diagnosis (~2K input tokens, ~200 output tokens):
- Brief: ~$0.001
- Detailed follow-up: ~$0.001
- Total with follow-up: ~$0.002

At 10 diagnoses/day = ~$0.60/month

---

## Success Criteria

- [ ] `/diagnose <name>` returns brief AI analysis
- [ ] `/diagnose <name> <lines>` respects custom line count
- [ ] Reply `/diagnose` to crash alert extracts container name
- [ ] "Want more details?" follow-up works
- [ ] Handles container not found gracefully
- [ ] Handles API errors gracefully
- [ ] All tests pass
