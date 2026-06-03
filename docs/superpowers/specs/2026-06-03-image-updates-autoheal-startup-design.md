# Design: Image-update detection, auto-heal, and richer startup message

**Date:** 2026-06-03
**Target version:** 0.12.0
**Status:** Approved (brainstorming) — pending spec review before planning

## Overview

Three related additions, surfaced by a SOTA benchmark against the self-hosted
Docker/server-monitoring field. Two close real capability gaps; the third makes
them discoverable.

1. **Image-update detection** — proactively notify when a newer image is available for a running container (notify-only; the existing `/pull` flow applies it).
2. **Auto-heal** — automatically restart containers that report Docker `HEALTHCHECK` status `unhealthy`, for an opt-in set of containers.
3. **Richer startup message + "What's new"** — enrich the boot broadcast with a tracked-status summary and a version-gated highlights block so users learn about new features.

### Goals
- Fit existing patterns: poll-loop monitors like `ResourceMonitor`, stateful trackers like `CrashTracker`, factory-wired callbacks, config blocks like `memory_management`, `AlertSender` protocol for delivery.
- Safe defaults: nothing destructive happens without explicit opt-in. Notify-first.
- No regressions: existing crash/recovery/unhealthy alerting behaviour is unchanged unless a container is explicitly opted in.

### Non-goals (YAGNI / explicitly out of scope)
- Multi-channel notifications (Discord/Slack/email/ntfy) — separate future feature.
- **Auto-pull** of image updates — detection is notify-only; applying stays a user-confirmed action via the existing `/pull` flow.
- Private-registry credential management beyond whatever the Docker daemon is already authenticated for.
- Persisting image-update dedup across restarts — in-memory for v1.
- Docker-label opt-in for auto-heal — config list chosen instead.
- Parsing `CHANGELOG.md` for "What's new" — a curated one-liner list is chosen instead.

---

## Feature 1 — Image-update detection

### Component
New `src/monitors/image_update_monitor.py` → `ImageUpdateMonitor`, an async poll loop modelled on `ResourceMonitor` (own `start()`/`stop()`, `is_running`, registered in `_BackgroundTasks`).

### Config
```yaml
image_updates:
  enabled: false          # opt-in (default off)
  poll_interval_hours: 24
```
Parsed in `src/config.py` into an `ImageUpdatesConfig`; defaults centralised in `src/constants.py`.

### Flow (per poll cycle)
1. List all **running, non-`ignored`** containers (reuse the shared Docker client from `DockerEventMonitor.shared_client`).
2. For each container, resolve:
   - **Local digest:** `container.image.attrs.get("RepoDigests", [])` → `repo@sha256:…`. Empty (locally built / no registry digest) → **skip gracefully**.
   - **Remote digest:** `docker_client.get_registry_data(image_ref).id` (`sha256:…`). Uses the daemon's existing auth, so private registries work when the daemon is logged in.
3. An update is available when the remote digest is **not** among the local `RepoDigests` **and** differs from the last-notified remote digest for that container (in-memory dedup `dict[str, str]`).
4. Collect all newly-detected updates for the cycle and emit a single **batched digest** alert (below). Record the notified remote digests.

**Mute behaviour (decision):** the update digest excludes `ignored_containers` only. Container *mutes* (which exist to silence short-term alert spam) do **not** suppress the daily update digest — update availability is informational and low-frequency, so a mute on a noisy container shouldn't hide that an update is waiting.

### Alert UX — batched daily digest
One message per poll cycle when there are new updates (never a flood, even on first run):
```
⬇️ Image updates available (3)

• radarr   — linuxserver/radarr:latest
• sonarr   — linuxserver/sonarr:latest
• plex     — plexinc/pms-docker:latest

[⬇️ Pull radarr] [⬇️ Pull sonarr] [⬇️ Pull plex]
```
- One Pull button per container (one per row), capped at a sane maximum (e.g. 10); if more, the message notes "+N more". Each button routes into the **existing pull-and-recreate confirmation flow** (same path as `/pull <name>` — exact `callback_data` to match `control_commands.py`, confirmed during planning).
- Delivered via a new `AlertSender.send_update_alert(updates: list[tuple[str, str]])` on `AlertManager` (+ protocol + `AlertManagerProxy`), mirroring how `send_health_alert` was added.

### Error handling
- Every per-container check is wrapped in try/except: registry timeouts, auth failures, `NotFound`, missing `RepoDigests` → log at debug and **skip that container**, never failing the cycle.
- The poll loop survives a `get_registry_data` outage and retries next cycle.

### Known risk — verify during implementation
Multi-arch images expose a **manifest-list** digest vs a **platform-image** digest; `RepoDigests` and `get_registry_data().id` can differ in form. Validate the comparison against a real multi-arch image (`linuxserver/radarr`) before finalising; if they don't line up, match on the manifest-list digest (e.g. via `Descriptor`/`attrs`). This is the one genuinely unverified integration point.

### Rate limits
Only one `get_registry_data` per container per 24h (lightweight manifest fetch, not a full pull). For a typical homelab this stays well under Docker Hub's anonymous limits. Interval is configurable for users with many containers / shared IPs.

---

## Feature 2 — Auto-heal (HEALTHCHECK-driven auto-restart)

**Already present:** `DockerEventMonitor` detects `health_status: unhealthy` events (`docker_events.py:406-418`), and `send_health_alert` already includes `[🔄 Restart] [📋 Logs] [🔍 Diagnose]` buttons (`manager.py:340-348`). Missing piece is *automatic* restart for opted-in containers.

### Component
New `src/services/auto_healer.py` → `AutoHealer`, a storm-guard + restart class mirroring `CrashTracker`'s shape (owns its own time-windowed state).

### Config
```yaml
auto_heal:
  enabled: true
  containers: []        # opt-in list; empty = no auto-restart (default behaviour unchanged)
  max_restarts: 3       # per window, per container
  window_minutes: 60
```
Parsed into an `AutoHealConfig`; defaults in `src/constants.py`.

### Responsibilities
- `is_enabled(container_name) -> bool`: `auto_heal.enabled` AND name in `containers` AND **not** `controller.is_protected(name)`.
- `async heal(container_name) -> None`:
  1. Record attempt timestamp; prune to `window_minutes`.
  2. If attempts in window **exceed** `max_restarts` → send a one-shot **"⚠️ Auto-heal giving up on <name> after N restarts in M min"** escalation (cooldown-guarded) and stop.
  3. Otherwise `await controller.restart(name)` and send **"🔧 Auto-restarted unhealthy <name> (n/N)"**.
- State: `dict[str, list[datetime]]` of restart times (parallels `CrashTracker._crashes`), with the same periodic stale-cleanup approach.

### Wiring
`ContainerController` is created *after* `DockerEventMonitor` (it consumes the monitor's shared Docker client), so inject the healer via a **setter** — `DockerEventMonitor.set_auto_healer(healer)` — called from `startup.py` once the controller exists. Avoids a constructor chicken-and-egg.

In `_handle_health_event`: if `self._auto_healer and self._auto_healer.is_enabled(name)` → `await self._auto_healer.heal(name)` (which sends its own notification) and **skip** the plain unhealthy alert. Otherwise the existing unhealthy alert fires unchanged. Mute and `ignored_containers` checks still apply first.

### Safety
- `protected_containers` are **never** auto-restarted, even if mistakenly listed in `auto_heal.containers`.
- Storm guard prevents restart loops from colliding with `CrashTracker` escalation.
- Notifications delivered via a new `AlertSender.send_autoheal_alert(...)` (on `AlertManager` + protocol + proxy).

---

## Feature 3 — Richer startup message + "What's new"

### DRY refactor
Extract `/health`'s monitor-summary logic (`health_command.py:62-130`) into a shared helper `build_status_lines(...)` (location: `health_command.py` module scope, or a small `src/utils/status_summary.py` if cleaner — decided in planning). Both `/health` and the startup broadcast call it. One source of truth for "what's being tracked."

### Enriched startup message
`_send_startup_notification` (`startup.py:257`) is expanded from 3 lines to a tracked-status summary plus on/off state for the new features:
```
🟢 Bot started — v0.12.0

📊 Tracking 23 containers · watching logs for 6
   Resources: ✅ · Memory: ⚪ Disabled · Unraid: ✅ Connected
   Image updates: ⚪ Disabled · Auto-heal: 2 containers

✨ What's new in v0.12.0
   • ⬇️ Image-update detection — opt-in via image_updates.enabled
   • 🔧 Auto-heal — opt-in via auto_heal.containers
   • ✅ Tests now run in CI on every change
```

### Version-gated "What's new"
- The highlights block appears **only when `BOT_VERSION` differs from the last-announced version**, persisted to `data/announced_version.json` (atomic write, mirroring `ChatIdStore`/`model_selection.json`). A plain restart on the same version shows only the tracking summary — no nagging.
- Highlights source: a curated `WHATS_NEW: dict[str, list[str]]` in `src/constants.py` (version → 2-4 user-facing one-liners). Maintained by hand at release time. Chosen over parsing `CHANGELOG.md` (dev-facing, verbose, fragile).
- The summary always reflects current on/off state, so even on a same-version restart users see whether image-updates/auto-heal are enabled.

---

## Config schema summary (all additions)
```yaml
image_updates:
  enabled: false
  poll_interval_hours: 24

auto_heal:
  enabled: true
  containers: []
  max_restarts: 3
  window_minutes: 60
```
New data file: `data/announced_version.json` (`{"version": "0.12.0"}`).

## New / changed files
**New**
- `src/monitors/image_update_monitor.py` — `ImageUpdateMonitor`
- `src/services/auto_healer.py` — `AutoHealer`
- `tests/test_image_update_monitor.py`, `tests/test_auto_healer.py`

**Changed**
- `src/config.py` — `ImageUpdatesConfig`, `AutoHealConfig`, parsing
- `src/constants.py` — new defaults + `WHATS_NEW`
- `src/alerts/manager.py` — `send_update_alert`, `send_autoheal_alert` (+ `AlertSender` protocol + `AlertManagerProxy`)
- `src/monitors/docker_events.py` — `set_auto_healer`, auto-heal branch in `_handle_health_event`
- `src/monitor_callbacks.py` — factory for the image-update alert handler
- `src/startup.py` — instantiate/start `ImageUpdateMonitor`, build + inject `AutoHealer`, enriched startup notification + version persistence
- `src/bot/health_command.py` — extract `build_status_lines` (shared)
- `tests/test_docker_events.py` — auto-heal branch coverage
- `README.md`, `CLAUDE.md` (callback conventions + config docs), `CHANGELOG.md`, version bump to `0.12.0`

## Testing strategy
Follows existing conventions (`MagicMock`/`AsyncMock`, no `conftest.py`, factory-return testing).

- **`test_image_update_monitor.py`:** digest match (no alert) / mismatch (alert); dedup (no repeat for same remote digest); batched single alert for multiple updates; skips `ignored_containers`; graceful on missing `RepoDigests` and on `get_registry_data` raising.
- **`test_auto_healer.py`:** opted-in container restarts on unhealthy; protected container never restarts (even if listed); storm guard stops + escalates after `max_restarts` in window; attempt counter/window pruning.
- **`test_docker_events.py` (extend):** unhealthy event for an opted-in container calls the healer and skips the plain alert; non-opted container keeps the existing alert-only behaviour exactly.
- **Startup:** version-gated what's-new shows on version change, hidden on same-version restart; tracking summary reflects monitor on/off state.

## Open risks / verify-during-implementation
1. **Multi-arch digest comparison** (Feature 1) — validate `RepoDigests` vs `get_registry_data().id` against a real multi-arch image; adjust to manifest-list digest if needed. *(highest uncertainty)*
2. **Pull-button `callback_data`** (Feature 1) — match the exact confirmation callback used by `control_commands.py` so the batched-digest Pull buttons route correctly.
3. **Auto-healer late-binding** (Feature 2) — confirm `startup.py` ordering so the controller exists before `set_auto_healer` is called.
