# Changelog

All notable changes to UnraidMonitor will be documented in this file.

## [0.21.2] - 2026-08-27

Quick wins from the sixth full audit. Three of these are features that looked like they worked.

### Fixed
- **Replying to an alert picked the wrong container, or none at all.** `/mute`, `/ignore` and `/diagnose` all extract a container name from the alert you replied to, against patterns that had drifted from what `AlertManager` actually sends. Replying `/mute 1h` to a restart-loop alert muted a container called **"4"** (the crash count, matched out of "Crashed 4 times in the last 10 minutes") and cheerfully reported success. An auto-heal alert yielded **"was"**, out of "Container was unhealthy". Unhealthy alerts yielded nothing at all.
  - The patterns are now anchored to the start of a line and require the label's colon and real case, which is what kills both the "4" and the "was".
  - Every alert type that names a container is covered: crash, restart loop, log errors, CPU/memory, unhealthy, and all three auto-heal variants.
- **Reply-to-alert `/diagnose` had never worked on anything except resource alerts.** It kept its own private copy of the patterns requiring literal `*` characters. Telegram strips formatting before handing text back in `Message.text` (the markup arrives separately in `entities`), so those patterns matched nothing in production. Its tests passed the whole time, because they fed it raw source strings with the asterisks still in. There is now one shared `extract_alert_container()` and `tests/test_alert_reply_extraction.py` drives real `AlertManager` sends through a render step, so changing a headline in `manager.py` fails a test instead of quietly breaking a feature.
- **The 🔄 Restart button on an alert now asks first.** It restarted the container on a single tap, while `/restart` has always required a ✅/❌. Alerts sit in the chat for days, which makes a stale one a mis-tap waiting to happen. Same `build_confirmation()` and `ctrl_confirm:` handler as the command.
- **A full array texted you every five minutes, forever.** The capacity alert had no dedup, unlike the disk temperature and disk status checks next to it. It now alerts once per crossing and re-arms when usage drops back under the threshold.
- **The Unraid client leaked an HTTP session on every network drop.** `connect()` assigned a fresh `aiohttp.ClientSession` without closing the dead one, and only shutdown ever closed anything, so a bad afternoon on the NAS leaked one session and its sockets per reconnect on a process that runs for months.
- **`data/model_selection.json` was written non-atomically**, the one persistence site that had skipped the tempfile + `os.replace` house pattern. A crash mid-write lost your runtime `/model` choice.
- **A failed startup-failure notification left no trace.** The one message you most need was sent inside `except Exception: pass`.
- **Startup logged "Unraid array monitoring started" for the notification relay** and nothing for the array monitor, which is unhelpful in exactly the moment you are reading logs.

### Security
- **`config/.env` could be baked into a published Docker image.** `.dockerignore` excluded `.env` and `config/config.yaml` but not `config/.env`, which is where `.env.example` tells you to put your Telegram token and LLM keys, and the Dockerfile copies the whole `config/` directory. One `docker buildx build --push` from a configured machine would have shipped them to Docker Hub. Found by audit rather than by leak, and the build machine has never held a `config/.env` (checked), so nothing that shipped from here carried one. Anyone who built their own image from a configured checkout should rebuild and rotate.

## [0.21.1] - 2026-08-27

### Fixed
- **Memory usage was reported as ~98% when the real figure was ~55%.** `get_system_metrics()` took the percentage from Unraid's `percentTotal` but the byte figure from its `used` field, and those mean different things. `used` is Linux's raw "not free", so it counts the page cache; on a live Tower that was 16.2 GiB of cache, making `used` read 30.6 of 31.1 GB while `percentTotal` and Unraid's own dashboard both said 55%. Nothing in the code compared the two, so a self-contradictory `/server` line ("Memory: 55.1% (30.6 GB)" against a 31 GB total) went unnoticed through five audits.
  - `memory_used` is now derived as `total - available`, the same basis `percentTotal` uses. Verified live: 17.37 of 31.06 GiB, 55.9%, drift between the byte figure and the percentage now 0.00 points.
  - Falls back to the raw `used` field on older API versions that do not expose `available`, and derives the percentage itself if `percentTotal` is missing.
  - Fixes all three consumers at once: the `/server detailed` line, the Memory Critical alert body, and the figures handed to the LLM for natural-language answers.
- **Alert thresholds were never affected.** They read `memory_percent`, which was always correct, so no spurious Memory Critical alerts were firing. Only the displayed gigabytes were wrong.

### Added
- `/server detailed` now reports the total alongside the used figure, and names the reclaimable disk cache on its own line. Without it, "55% used" next to 0.5 GB free is baffling.
- `memory_available` and `memory_cached` on the metrics dict.

### Notes
- **Found by asking the bot for a status update as a fairy tale.** The story said "30.8 of 31.1 gigabytes occupied by the bustling townsfolk", Sam checked it against the Unraid dashboard, and the claim did not hold. Unit tests, strict mypy and five full audits had all passed over it, because every one of them checked the code against itself. Making the bot narrate its own numbers in prose turned out to be a cheap and effective smoke test.

## [0.21.0] - 2026-08-27

UPS monitoring, finally, and over the network rather than over a USB cable.

### Added
- **UPS monitoring via NUT** (`src/nut/`). The bot reads UPS state from a [Network UPS Tools](https://networkupstools.org/) server over TCP 3493, so the UPS does not have to be plugged into the machine running the bot. This is what unblocked the feature: `apcupsd` needs a local USB data link, NUT does not, and NUT covers 199 manufacturers against apcupsd's much narrower set.
  - `src/nut/client.py` speaks the protocol directly (`LIST UPS`, `LIST VAR`, optional `USERNAME`/`PASSWORD`, `LOGOUT`) in about 150 lines. **Deliberately not a dependency:** every Python NUT client on PyPI (PyNUT, nut2, pynut3) is GPLv3, and this project is MIT and ships a public Docker image, so importing one would relicense the lot. None of them are async either.
  - `src/nut/monitor.py` alerts on the status flags that mean something: `OB` (mains lost), `LB` (low battery), `RB` (replace battery, nagged daily rather than every poll), `OVER`, `BYPASS`, `OFF`, `FSD`, `ALARM`. Returning to mains sends a recovery message with how long you were on battery.
  - **`CAL` is deliberately not an alert.** A runtime calibration puts the UPS on battery on purpose, the same reasoning that stopped a parity sync reading as a failed disk in v0.20.0.
  - Threshold alerts for battery charge (only while actually on battery, so recharging after an outage stays quiet) and for UPS load, with the watts worked out from `ups.realpower.nominal` when the UPS reports it.
- **`/ups`** shows model, status, battery percentage, runtime left, load and input voltage. `/ups detailed` dumps every variable the UPS exposes.
- **UPS mute category.** Mute buttons on UPS alerts silence UPS alerts only; `/mute-server` still mutes everything, which finally makes its long-standing "system, array, and UPS" wording true. Re-adds the category removed in v0.13.0 when there was nothing behind it.
- Config: a `nut:` block with `enabled`, `host`, `port`, `ups_name`, `poll_seconds` and `thresholds.{battery_charge,load}`. Credentials go in `config/.env` as `NUT_USERNAME` / `NUT_PASSWORD`, alongside the other secrets, and are only needed if your `upsd` gates reads.
- `/manage` → ⚙️ Features gains a UPS row with an enable/disable button, and `/health` reports the UPS monitor's state.

### Changed
- **On by default, quiet by default.** With no `nut.host` set it falls back to your Unraid host, because the Unraid NUT plugin runs `upsd` on that box and the bot is usually in a container that cannot reach it as `localhost`. If nothing answers, it logs one line and never alerts: most installs have no UPS, and a default-on feature must not message them about it.
- **An unreadable UPS reports as unavailable, never as healthy.** A poll that could not reach `upsd` is tracked separately from a poll that found nothing wrong. `/ups` and `/health` both say "Unavailable" with the underlying error, and losing a previously-working server alerts after three consecutive failures rather than on the first dropped poll. An empty reading that renders as blanks would look like a healthy UPS forever.
- Bad NUT credentials stop the monitor loop after one alert instead of retrying and filling the log with the same rejection.

### Security
- **aiohttp floor raised to 3.14.3** for PYSEC-2026-3545, an out-of-bounds heap read in the C response parser that a malformed server response can trigger in the client. The bot is an aiohttp client, so this one applies. The other two in that batch do not: PYSEC-2026-3546 is server-side request smuggling and PYSEC-2026-3547 needs WebSockets, while the bot long-polls over HTTP and runs no aiohttp server. Verified by reading the advisories rather than by taking the fix-available column at face value.
- **Dev-only pins** for `cryptography>=50.0.0` (PYSEC-2026-3552, a PKCS#7 S/MIME padding oracle, reached only through `types-docker` -> `types-paramiko`) and `pip>=26.2` (PYSEC-2026-3721, needs a malicious package index). Neither package is in the Docker image. Pinned so `pip-audit` stays clean without an ignore list, which is the policy set in v0.18.1.
- `requirements.txt` aiohttp and aiogram floors realigned with `pyproject.toml`; they had drifted to 3.13.5 and 3.4.0.

### Fixed
- **The Features panel dropped rows after an unrelated save.** Saving the auto-heal or memory-restart container picker rebuilt the panel without `unraid_config`, so the Unraid notification row vanished until you reopened `/manage`. Pre-existing since v0.20.0; found while adding the UPS row, which would have had the same problem.

### Notes
- `upsd` binds to `127.0.0.1` only by default. A containerised bot needs `LISTEN 0.0.0.0 3493` in `upsd.conf` before it can connect. This is the setup step most likely to trip people up.
- The orphaned `unraid.polling.ups` and `unraid.thresholds.ups_battery` keys are still ignored. UPS settings live under `nut:` now.
- 74 new tests. The client's happy paths run against a real asyncio TCP server speaking the documented protocol rather than a mocked socket, since the parsing is the part most likely to be wrong and a mock would only replay its own assumptions.

## [0.20.0] - 2026-08-02

Both changes were designed and verified against a live Unraid server (API
introspection plus real query responses) rather than against the published
schema, which caught two things the schema alone would not have.

### Fixed
- **A parity sync was reported as a disk failure.** A sync or rebuild writes to its target, so the array reports that disk as `DISK_INVALID` until it completes — and `array_monitor.py` alerted on anything that wasn't `DISK_OK`. Confirmed live: a server 45% through a parity sync was sending "💾 Parity Disk Problem — Status: DISK_INVALID, Expected: DISK_OK", while Unraid's own feed described the same event as "Parity Sync/Data Rebuild (25.3% completed)". The monitor now requests `parityCheckStatus` (from the `array` query it was already making) and reports progress instead. Suppression is deliberately narrow — only `DISK_INVALID`/`DISK_NEW`, only while an operation is running; a `DISK_DSBL` mid-sync still alerts, and a disk still faulty *after* the sync alerts then.
- **The end of a parity operation was never announced.** `DISK_INVALID` → `DISK_OK` silently cleared the dedup flag, so you were told a rebuild started but never that it finished. Completion, cancellation and failure now each send a message with the error count — reported as "not reported" rather than `0` when the API returns null, which it does in practice.

### Added
- **Unraid notification relay** (`src/unraid/monitors/notification_monitor.py`) — forwards Unraid's own notification feed (SMART, disk errors, share-full, parity results, plugin updates) into Telegram, so there is one place to look instead of two. Opt-in, off by default.
  - **Importance floor**, default `WARNING`. The feed carries a lot of routine INFO chatter (backup finished, parity-tuning pause/resume) that would be noise in a chat window.
  - `/manage` → ⚙️ Features → 🔔 enables it (needs a restart, like image updates) and a second button cycles the floor `WARNING → ALERT → INFO`, which applies **live** — the running monitor reads it off the shared config object each poll.
  - **Primes on first run** rather than replaying the existing backlog into your chat.
  - Dedup on the notification `id` (which embeds a unix timestamp, so ids never repeat), persisted to `data/announced_notifications.json` and bounded at 500 entries, so a restart doesn't re-announce.
  - Capped at 10 relayed per poll with the remainder announced as a count — never silently dropped. A failed send is not marked as seen, so it is retried.
  - An unrecognised importance from a future Unraid release is treated as urgent and relayed, rather than dropped by a filter that has not heard of it.
- `UnraidClientWrapper.get_notifications()` and GraphQL variable support in `_execute_query`.
- Config: `unraid.notifications.{enabled,min_importance}` and `unraid.polling.notifications`. An unknown `min_importance` logs a warning and falls back to `WARNING` rather than silently disabling the filter.

### Notes
- **UPS monitoring is still not implemented and is not in this release.** The schema supports it (`upsDevices` is present on current Unraid API versions), but it is fed by `apcupsd` over USB — with no USB data link there is nothing to read, and a monitor would report "no UPS" forever. If a UPS is connected later, its events arrive through the notification relay above with no code change.

## [0.19.0] - 2026-08-02

Bug-fix release from a multi-lens audit of the inline-button surface. Four of the
fixes below are for buttons that were reachable from a default install and did
nothing — or the wrong thing — during exactly the incidents they exist for.

### Fixed
- **Array capacity threshold options failed after saving.** `_UNRAID_METRICS` is keyed on the picker's metric name (`capacity`) while the button carries the setter's key (`array_usage`), so `_apply_threshold` persisted the new value and *then* raised `KeyError` before confirming — the user saw a spinning button and no reply, with the config already changed. Added the reverse mapping plus a guard that rejects an unknown metric before writing. The whole `arr_set`/`srv_set` family was untested; it now has coverage including a picker→setter round-trip.
- **Stop buttons on Unraid "Memory Critical" alerts did nothing** unless `memory_management.enabled` was true. The alert renders them whenever Unraid monitoring is on, but the `mem_kill:` handler was registered only alongside the pressure-monitoring loop. `MemoryMonitor` is now built regardless (the object is inert until `start()`), so the buttons work in both configurations; `bg.memory_monitor` remains the single signal for starting the kill loop and for reporting the feature as enabled.
- **"Re-mute 1h" muted for 60 hours.** The container branch of `_remute_keyboard` emitted seconds (3600/86400) into `mute_callback`'s minutes API, which clamps rather than rejects — so 1h became 60h and 24h became the 30-day maximum. Now 60/1440, matching the server and array branches.
- **`/pull` silently dropped nvidia GPU access.** `_extract_run_config` preserved `Devices` (Intel QuickSync `/dev/dri`) but not `DeviceRequests`, `Runtime` or `GroupAdd`, so an nvidia container came back healthy and no longer transcoding — and the rollback path reused the same config, so it could not undo it. All three are now carried across.
- **`/manage` Status, Resources, Server and Disks were one-way doors.** Editing a message without `reply_markup` makes Telegram drop the keyboard entirely; those four panels had no way back short of re-typing `/manage`. Each now carries Refresh + Back, like Ignores, Mutes and Features already did.

### Added
- **Telegram command menu** (`src/bot/command_menu.py`) — typing `/` now autocompletes. The menu is derived from the `Command` filters actually registered on the dispatcher, so an install without Docker, Unraid or an LLM key never advertises a command it cannot run. It is published after *all* registration (including `/health`, which is registered in `start_monitoring` rather than `register_commands`), since the menu is a snapshot taken at call time. Hyphenated commands (`/mute-server`, `/cancel-kill`) still work when typed but are logged as ineligible, since Telegram rejects the whole call if any menu name contains a hyphen.
- **Global error handler** (`dp.errors`) — an exception inside a handler now logs with a traceback and replies "⚠️ That failed (…)" instead of vanishing. For a button it answers the callback first so the client stops spinning, in its own guard — an expired callback query must not take the explanation down with it.
- **Catch-all callback handler** — any callback no other handler claims is answered with "That button is no longer available." A stale keyboard or a prefix mismatch can no longer leave a button spinning indefinitely.

### Changed
- `safe_edit` treats Telegram's "message is not modified" as a benign no-op (returns `False`) rather than raising — a Refresh that finds nothing changed is not an error. Other `TelegramBadRequest` failures still propagate.
- `/cancel-kill` still reports "Memory management is not enabled" when the pressure loop is off, now that the monitor object exists either way.

### Known gaps
- **UPS monitoring does not exist** despite being documented in the README (`ups` poll interval, `ups_battery` threshold), the user guide ("UPS battery below threshold") and the server-mute confirmation text. There is no UPS query, monitor or alert in `src/`, and `config.py` does not parse either key. Unraid's GraphQL API exposes `upsDevices`; wiring it up is the obvious next release. Same for SMART, which `/disks` is documented as showing but does not.

## [0.18.1] - 2026-07-18

### Security
- **All aiohttp CVE ignores dropped** — aiogram 3.30 lifted its `aiohttp<3.14` cap, so aiohttp is now 3.14.1, which fixes the two long-ignored advisories (CVE-2026-34993, CVE-2026-47265) *and* the nine new aiohttp PYSEC advisories published 2026-07-18 that turned CI red minutes after the v0.18.0 push. CI pip-audit now runs with **no ignore flags**.
- Bumped `cryptography` (47.0.0 → 49.0.0), `msgpack` (1.1.2 → 1.2.1), and `pydantic-settings` (2.14.1 → 2.14.2) past their respective advisories. `pip-audit`: no known vulnerabilities.

### Changed
- aiogram 3.28.2 → 3.30.0 (floor raised to `>=3.30.0`); full suite re-verified on the new versions.

## [0.18.0] - 2026-07-18

### Added
- **Top memory users in pressure warnings** — the memory warning alert now lists the top 5 memory-consuming containers across *all* running containers (largest first), not just the killable ones, so you can see at a glance who is actually eating the RAM. One bounded snapshot (`_memory_snapshot`, capped by `stats_timeout`) feeds both the text and the button labels; if Docker is slow under pressure the alert falls back to names without figures rather than being delayed.
- **Memory restart list** (`memory_management.restart_containers`) — containers that hog memory but recover after a bounce (classic Plex) can be offered a one-tap "🔄 Restart" button on warning *and* critical memory alerts, as the gentle alternative to stopping. Restarting the container that is mid-countdown for an auto-kill cancels the countdown. Restart buttons are rejected for protected containers and for names not on the list, even if spoofed.
- **Configure memory restarts from Telegram** — `/manage` → ⚙️ Features → 🧠 Configure memory restarts opens a tap-to-toggle container picker (same UX as auto-heal). Saves persist to `config.yaml` and apply live — the running `MemoryMonitor` shares the config object, so no bot restart is needed.

### Changed
- **Stop/Restart buttons on memory warnings are sorted by memory usage, largest first** — the biggest win is always the top button. The *auto-kill* order is unchanged and still follows the configured `killable_containers` priority order.
- **Memory alerts fall back to plain text if Markdown parsing fails** — container names can contain Markdown special characters (underscores are common); a parse failure now resends the alert without formatting instead of silently dropping it during a memory event.

## [0.17.0] - 2026-06-28

### Added
- **Per-container memory on the kill buttons** — the memory-pressure warning/critical alerts now show how much RAM each killable container is using right on its "⏹ Stop" button (and in the alert text), so you can free the most memory first. `MemoryMonitor` now reads this itself for just the killable containers (`get_killable_memory`), so it works even when resource monitoring is disabled and is robust if Docker is slow under pressure (bounded by `stats_timeout`, falls back to names-only).
- **Memory feedback when a container is stopped** — stopping a container (via button or the auto-kill countdown) now reports how much memory it was using and the system memory level afterwards (e.g. "It was using ~1.8GB. System memory now 78% (6.2GB free)."). `kill_container` returns a new `KillResult` carrying this context.

### Changed
- The memory alert handler no longer depends on the resource monitor for button labels — memory figures are supplied by `MemoryMonitor` through the alert itself.
- The auto-kill confirmation reads system memory *after* the stop (it previously reported the pre-kill figure).

## [0.16.1] - 2026-06-25

### Fixed
- **Memory pressure warning lists only running containers** — the "Killable containers" warning alert offered a "⏹ Stop" button for every configured killable container, including ones already stopped (which can't free any memory). It now lists and offers only containers that are killable *and* currently running, and says "none running" when there are none. The running filter is shared with the critical-alert path (`_get_running_killable`), so both stay consistent.

## [0.16.0] - 2026-06-12

Remediation of the June 12 codebase audit (vault: `audits/Unraid Monitor/reports/audit-2026-06-12-0639.md`) — all actionable findings implemented.

### Added
- **Container health check** — the bot now touches a liveness heartbeat file from its event loop every 60s (`src/utils/heartbeat.py`), and the Dockerfile gained a `HEALTHCHECK` that fails when the file goes stale. Bot health shows up in `docker ps` and the Unraid dashboard with no port mapping required. The heartbeat starts before the setup wizard too, so a container mid-setup doesn't report unhealthy.
- 10 new tests (1179 total): flush retry/drop semantics, queue dedup, spoofed `model_select` rejection, heartbeat liveness.

### Fixed
- **Queued alerts are no longer lost on a failed flush** — the pre-`/start` queue was cleared *before* delivery, so a transient Telegram failure during the flush silently discarded boot-time alerts. An alert now counts as delivered once any chat receives it; alerts that fail every chat get one retry on the next flush, then are dropped with a logged warning.
- **Identical consecutive alerts dedup at queue time** — two identical crash alerts queued before `/start` no longer both deliver.
- **Auto-heal save surfaces dropped names** — if `set_containers()` ever filters an invalid name, the `/manage` save answer now says how many were skipped instead of staying silent.

### Security
- **`model_select:` callback validated against the registry** — the last callback family without input validation. A spoofed callback can no longer persist arbitrary provider/model strings into `data/model_selection.json`; unknown pairs get "Invalid selection".
- **pip-audit is now blocking in CI** — it previously ran with `|| true`, which is how the aiohttp CVEs went unnoticed. The two known aiohttp advisories (CVE-2026-34993, CVE-2026-47265; fixed only in 3.14, which aiogram caps below) are explicitly `--ignore-vuln`-ed with justification: neither is exploitable here (no `CookieJar.load()`, no per-request cookies). Any *new* CVE now fails the build. aiohttp floor raised to 3.13.5 with a comment documenting the situation.
- **Diagnostic prompt sanitizes container config** — volume mounts, env vars, and port mappings now pass through `sanitize_for_prompt()` like every other prompt input (defense-in-depth against a compromised container injecting instructions).

### Changed
- **Explicit LLM timeouts** — Anthropic/OpenAI clients are constructed with a 120s timeout (SDK default is ~10 minutes), Ollama gets 300s (local models on CPU are legitimately slow), and startup model discovery is capped at 15s so a slow API can't stall boot.

## [0.15.1] - 2026-06-08

### Changed
- **Documentation refreshed for current features** — the README's "What's New" was still pinned to v0.12.0; replaced it with a v0.15.0 summary, and updated the feature list and command table to reflect the `/manage` Features panel (server/disks/features sections) and per-feature `/model` overrides (`chat`/`diagnose`/`analyze`).
- **User guide expanded** — added an Optional Features section documenting image-update detection and auto-heal (how to enable from `/manage` → ⚙️ Features and via `config.yaml`), documented per-feature model switching, and added the Features panel to the Manage Dashboard walkthrough.
- **Data Storage docs** — listed the previously-undocumented `announced_version.json` and `announced_updates.json` files.

## [0.15.0] - 2026-06-06

Audit remediation release — all 16 items from the June 6 codebase audit (`audit-reports/audit-2026-06-06-1538.md`).

### Fixed
- **Auto-heal now actually retries containers that stay unhealthy** — the `_unhealthy_alerted` dedup set blocked the post-restart unhealthy event from re-entering `heal()`, so a persistently-broken container got exactly one restart attempt and then silence: the documented storm guard (`max_restarts` per `window_minutes`) and the give-up escalation were only reachable for *flapping* containers. The dedup flag is now cleared after each heal attempt (unless the healer has given up), so the storm guard — not the dedup set — bounds the retry loop. `heal()` also checks the result of `controller.restart()` now: a failed restart sends a distinct "❌ Auto-heal failed" alert instead of the success-implying "attempt N/M". Failed attempts count toward the storm guard so a restart that keeps erroring escalates instead of retrying forever.
- **Image-update alerts no longer repeat after a bot restart** — the dedup map (container → announced digest) was in-memory only, and the bot restarts itself routinely (setup wizard, `/manage` feature toggles, `/restart`). It now persists to `data/announced_updates.json` (atomic write, corrupt-file tolerant) and prunes entries for removed containers — never on a failed Docker poll, so a daemon outage can't wipe it.
- **`/diagnose` no longer goes silent after "Analyzing…" when Docker hiccups** — `gather_context()` only handled `NotFound`; an `APIError` from `containers.get` or any failure fetching logs crashed the handler. Daemon errors now degrade to "couldn't gather context", and unavailable logs degrade to a partial diagnosis with `(logs unavailable)`.
- **Concurrent `/restart` triggers no longer send duplicate notices** — a module-level guard with a `finally` reset (a *failed* `os.execv` clears it so retries still work).
- **Corrupted `data/announced_version.json` is removed on read** so "What's new" isn't re-shown on every boot after a bad write.

### Security
- **Unraid API error bodies are now redacted and truncated in all paths** — `_execute_query()` raised exceptions containing the full response body (the connectivity test already redacted; the query paths didn't). New `_safe_body()` helper redacts the API key *before* truncating so a key straddling the cut-off can't partially leak.
- **Auto-heal picker validates container names** — `fh_tog:` callback data is now checked against the live candidate list (every other callback family already validated), and `AutoHealConfig.set_containers()` defensively filters invalid names, so a spoofed callback can't persist junk into config.yaml.
- **Dependency CVE scanning added** (`pip-audit` in dev deps + non-blocking CI step). First run found 6 CVEs: `idna`, `requests`, `urllib3` upgraded in the lockfile. `aiohttp` 3.13.5 (CVE-2026-34993, CVE-2026-47265, fixed in 3.14.0) remains pinned by aiogram 3.28.2 (`aiohttp<3.14`) — exposure is client-only; re-check when aiogram releases.
- **Defense-in-depth**: diagnostic `alert_context` strings pass through `sanitize_for_prompt()`, and `markdown_to_telegram_html` now has explicit regression tests proving model output can't inject `<tg-spoiler>`/`<a href>` tags.

### Changed
- **AutoHealer storm-guard window uses `time.monotonic()`** instead of wall-clock, so NTP steps can't stretch or shrink it; `_recent_count` renamed to `_prune_and_count` to reflect its mutation.
- **`alert_callbacks.py` callback parsing deduplicated** — four shared helpers (`_parse_container_callback`, `_parse_valued_callback`, `_parse_metric_callback`, `_parse_minutes_callback`) replace ~230 lines of repeated parse→validate→lookup boilerplate across 12 handlers (935 → 838 lines). New alert buttons get name validation for free.
- `atomic_yaml_write()` sets 0o644 on fresh files (matching `version_store.py`); `ResourceMonitor._violation_last_seen` initialised in `__init__` instead of lazily; `extract_local_digests` properly typed.

### Added
- `send_autoheal_alert(..., failed=True)` variant across protocol, manager, and proxy with a distinct failure message.
- 23 new tests (1169 total), including an end-to-end storm-guard test (unhealthy → 3 restarts → exactly one give-up escalation → silence) and a restart-survival test for image-update dedup.

## [0.14.4] - 2026-06-05

### Fixed
- **Startup card no longer shows Unraid System/Array as red when they're actually running** — a timing race in the startup notification. Each monitor sets `is_running` synchronously at the top of its `start()` coroutine, but `asyncio.create_task()` only *schedules* that coroutine. The Unraid system/array monitors are created *after* `await client.connect()` with nothing yielding before the card is built, so their tasks hadn't run yet and rendered 🔴 (they were running fine moments later — `/health` always showed green). Also fixed the latent case where, with Unraid not configured, there's no `connect()` await at all and *every* monitor could show red. `_start_background_monitors` now yields one event-loop turn (`await asyncio.sleep(0)`) before returning, so all monitors report their real state when the card snapshots them.

## [0.14.3] - 2026-06-05

### Changed
- **Friendlier natural-language chat** — the NL assistant now plays along with creative, on-topic requests instead of refusing them. "Tell me a story about the server", "status as a captain's log", "roast my containers", "how's it doing in haiku" now get fulfilled by pulling the real stats with tools first, then answering in the requested style (grounded in actual numbers, never invented). It still declines genuinely off-topic asks (e.g. "write my essay") and stays scoped to the server, containers, array, and disks. Tone/personality guidance added to `SYSTEM_PROMPT` in `src/services/nl_processor.py`; action/tool behaviour unchanged.

## [0.14.2] - 2026-06-05

### Fixed
- **AI replies no longer show literal `*` characters** — the LLM returns standard CommonMark (`**bold**`, `### headings`, `*italic*`), but the natural-language chat handler sent it with no parse mode (so the asterisks rendered literally) and `/diagnose` sent it as Telegram legacy Markdown (which can't parse `**`, silently stripping the formatting). Model output is now converted to Telegram **HTML** before sending, so bold/italic/headings/code render correctly. HTML is the most forgiving parse target — text is escaped and only balanced tags are emitted, so an unmatched delimiter degrades to harmless text instead of breaking the whole message.

### Added
- `src/utils/telegram_format.py` — `markdown_to_telegram_html()` converter (snake_case- and math-safe emphasis rules, fenced/inline code, links, list markers) plus `strip_html_tags()` for the plain-text fallback. Applied to NL chat, `/diagnose` (+ details), and the Diagnose alert button.

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

## [0.6.0] - 2026-01-27

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

## [0.5.0] - 2026-01-26

### Added
- Unraid server monitoring via GraphQL API
- `/server` and `/server detailed` commands for system metrics
- `/array` and `/disks` commands for array/disk status
- Server temperature, memory, and UPS alerts
- Array health monitoring (disk temps, SMART status, parity)
- `/mute-server` and `/mute-array` commands
- Array mute manager for disk/parity alerts

## [0.4.0] - 2026-01-25

### Added
- `/mute` and `/unmute` commands for container alert control
- `/mutes` command to view all active mutes
- `/ignore` command to create ignore patterns from recent errors
- `/ignores` command to list all ignore patterns
- Recent errors buffer for ignore pattern selection
- Persistent mute storage in JSON files

## [0.3.0] - 2026-01-24

### Added
- Resource monitoring with CPU/memory threshold alerts
- `/resources` command for container resource stats
- Per-container threshold configuration
- Sustained threshold checking (alerts after duration exceeded)

### Changed
- Alerts now include resource context (memory/CPU usage)

## [0.2.0] - 2026-01-23

### Added
- AI-powered diagnostics with `/diagnose` command
- Log watching with configurable error patterns
- Log error alerts with rate limiting
- Container control commands (`/restart`, `/stop`, `/start`, `/pull`)
- Protected containers list to prevent accidental control
- Confirmation prompts for destructive actions

## [0.1.0] - 2026-01-22

### Added
- Initial release
- Docker container monitoring via socket
- Crash detection with exit code interpretation
- `/status` and `/logs` commands
- Telegram bot with user authentication
- Basic alert system for container events
