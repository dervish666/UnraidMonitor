"""Named constants for configuration defaults and thresholds."""

# ---------------------------------------------------------------------------
# Log watching defaults
# ---------------------------------------------------------------------------
DEFAULT_COOLDOWN_SECONDS = 900

# ---------------------------------------------------------------------------
# Bot display defaults
# ---------------------------------------------------------------------------
CONFIRMATION_TIMEOUT_SECONDS = 60
LOG_MAX_LINES = 100
LOG_MAX_CHARS = 4000
NL_LOG_MAX_CHARS = 3000
DIAGNOSE_MAX_LINES = 500
ERROR_DISPLAY_MAX_CHARS = 200

# ---------------------------------------------------------------------------
# Resource monitoring defaults
# ---------------------------------------------------------------------------
RESOURCE_POLL_INTERVAL_SECONDS = 60
RESOURCE_SUSTAINED_THRESHOLD_SECONDS = 120
DEFAULT_CPU_PERCENT = 80
DEFAULT_MEMORY_PERCENT = 85

# ---------------------------------------------------------------------------
# Memory management defaults
# ---------------------------------------------------------------------------
MEMORY_WARNING_THRESHOLD = 90
MEMORY_CRITICAL_THRESHOLD = 95
MEMORY_SAFE_THRESHOLD = 80
MEMORY_KILL_DELAY_SECONDS = 60
MEMORY_STABILIZATION_WAIT = 180
MEMORY_TOP_CONSUMERS = 5  # Containers listed in the "Top memory users" alert section

# ---------------------------------------------------------------------------
# AI / LLM token limits
# ---------------------------------------------------------------------------
PATTERN_ANALYZER_MAX_TOKENS = 500
NL_PROCESSOR_MAX_TOKENS = 1024
DIAGNOSTIC_BRIEF_MAX_TOKENS = 300
DIAGNOSTIC_DETAIL_MAX_TOKENS = 800
PATTERN_ANALYZER_CONTEXT_LINES = 30
DIAGNOSTIC_CONTEXT_EXPIRY_SECONDS = 600
NL_MAX_TOOL_ITERATIONS = 10
NL_MAX_CONVERSATION_EXCHANGES = 5
# SDK defaults are ~10 minutes; a hung cloud call shouldn't pin a chat that long.
# Ollama gets longer — local models on CPU can legitimately take minutes.
LLM_REQUEST_TIMEOUT_SECONDS = 120
OLLAMA_REQUEST_TIMEOUT_SECONDS = 300
MODEL_DISCOVERY_TIMEOUT_SECONDS = 15

# ---------------------------------------------------------------------------
# Liveness heartbeat (Docker HEALTHCHECK reads this file's mtime)
# ---------------------------------------------------------------------------
HEARTBEAT_PATH = "/tmp/unraidmonitor-heartbeat"
HEARTBEAT_INTERVAL_SECONDS = 60
# Dockerfile HEALTHCHECK fails when the file is older than this (3 missed beats).
HEARTBEAT_MAX_AGE_SECONDS = 180

# ---------------------------------------------------------------------------
# Unraid monitoring defaults
# ---------------------------------------------------------------------------
UNRAID_POLL_SYSTEM_SECONDS = 30
UNRAID_POLL_ARRAY_SECONDS = 300
UNRAID_POLL_NOTIFICATIONS_SECONDS = 300

# Unraid's own notification feed, ordered least to most urgent. The relay sends
# anything at or above the configured floor.
NOTIFICATION_IMPORTANCE_LEVELS = ("INFO", "WARNING", "ALERT")
UNRAID_NOTIFICATION_MIN_IMPORTANCE = "WARNING"
# How many notification ids to remember for dedup. Unraid ids embed a unix
# timestamp so they never repeat; this only bounds the state file.
NOTIFICATION_DEDUP_HISTORY = 500
# Cap per poll so a backlog can't produce a hundred Telegram messages at once.
NOTIFICATION_MAX_PER_POLL = 10
UNRAID_CPU_TEMP_THRESHOLD = 80
UNRAID_CPU_USAGE_THRESHOLD = 95
UNRAID_MEMORY_USAGE_THRESHOLD = 90
UNRAID_DISK_TEMP_THRESHOLD = 50
UNRAID_ARRAY_USAGE_THRESHOLD = 85

# ---------------------------------------------------------------------------
# NUT (Network UPS Tools) defaults
# ---------------------------------------------------------------------------
NUT_DEFAULT_PORT = 3493
NUT_TIMEOUT_SECONDS = 5.0
NUT_POLL_SECONDS = 60
# Warn when the battery falls below this while running on battery.
NUT_BATTERY_CHARGE_THRESHOLD = 50
# Warn when the UPS load exceeds this percentage of its rated capacity.
NUT_LOAD_THRESHOLD = 80
# Consecutive poll failures before a previously-reachable NUT server is
# reported as lost. One dropped poll is normal; three is a real outage.
NUT_UNAVAILABLE_AFTER_FAILURES = 3
# Status flags that get their own alert. CAL is deliberately absent: a runtime
# calibration puts the UPS on battery on purpose, the same reasoning that keeps
# a parity sync from reading as a disk failure.
NUT_ALERT_FLAGS = ("OB", "LB", "RB", "OVER", "BYPASS", "OFF", "FSD", "ALARM")

# ---------------------------------------------------------------------------
# Threshold picker step options (used by alert callback buttons)
# ---------------------------------------------------------------------------
CPU_THRESHOLD_STEPS = [90, 120, 150, 200, 300, 400]
MEMORY_THRESHOLD_STEPS = [85, 90, 95, 99]

# ---------------------------------------------------------------------------
# Alert proxy
# ---------------------------------------------------------------------------
ALERT_QUEUE_MAX = 50
ALERT_SEND_DELAY = 0.1

# ---------------------------------------------------------------------------
# Mute maintenance
# ---------------------------------------------------------------------------
MUTE_MAINTENANCE_INTERVAL_SECONDS = 300

# ---------------------------------------------------------------------------
# Container name validation
# ---------------------------------------------------------------------------
MAX_CONTAINER_NAME_LENGTH = 256

# ---------------------------------------------------------------------------
# Default AI model names
# ---------------------------------------------------------------------------
DEFAULT_HAIKU_MODEL = "haiku"
DEFAULT_SONNET_MODEL = "sonnet"
DEFAULT_OLLAMA_MODEL = "qwen2.5:7b"
DEFAULT_OLLAMA_HOST = "http://localhost:11434"

# Image-update detection defaults
IMAGE_UPDATE_POLL_INTERVAL_HOURS = 24
IMAGE_UPDATE_MAX_SHOWN = 10  # cap Pull buttons per digest message
ANNOUNCED_UPDATES_PATH = "data/announced_updates.json"  # dedup map surviving restarts
ANNOUNCED_NOTIFICATIONS_PATH = "data/announced_notifications.json"  # relayed Unraid notification ids

# Auto-heal defaults
AUTOHEAL_MAX_RESTARTS = 3
AUTOHEAL_WINDOW_MINUTES = 60

# Startup "What's new" - curated user-facing one-liners per version.
# Shown once when BOT_VERSION first differs from data/announced_version.json.
ANNOUNCED_VERSION_PATH = "data/announced_version.json"
WHATS_NEW: dict[str, list[str]] = {
    "0.21.1": [
        "Memory readings were wrong. The bot reported your server as using almost all its RAM when Unraid's own dashboard said about half. It was counting the disk cache as used memory, which Linux frees the moment anything needs it",
        "/server now shows the real figure and lists the reclaimable cache separately, so \"55% used but almost nothing free\" finally makes sense",
        "Memory alerts were never affected: they always used the percentage, which was correct. Only the gigabyte figure was wrong",
    ],
    "0.21.0": [
        "UPS monitoring, over the network. The bot now reads your UPS from a NUT server, so the UPS does not have to be plugged into the machine running the bot. It alerts when the mains drops, when the battery gets low, and when the UPS is overloaded or on bypass",
        "New /ups command shows battery, runtime left, load and input voltage",
        "On by default. If it cannot find a NUT server it stays quiet rather than nagging you, and you can turn it off in /manage \u2192 \u2699\ufe0f Features",
        "A UPS it cannot read is reported as \"unavailable\", never as healthy. Silence from a monitor is not the same as good news",
    ],
    "0.20.0": [
        "No more false parity alarms - a parity sync or disk rebuild is now reported as progress (\"45% complete\"), not as a disk problem, and you get a message when it finishes. A genuinely failed disk still alerts during a sync",
        "Unraid's own notifications can now reach Telegram - SMART warnings, disk errors, share-full and parity results in the same place as everything else. Off by default; turn it on in /manage → Features",
        "Set how chatty it is - the notification button cycles WARNING+ (default), ALERT only, or everything including INFO, and applies instantly",
    ],
    "0.19.0": [
        "Four broken buttons fixed - array threshold options no longer fail silently after saving, Stop buttons on memory alerts now work even with memory management off, and \"Re-mute 1h\" means 1 hour rather than 60",
        "Commands now autocomplete - type / in Telegram to see every command this bot actually has enabled",
        "/manage panels have Back and Refresh buttons, so Status, Resources, Server and Disks are no longer dead ends",
        "/pull keeps your GPU - nvidia device access, custom runtimes and extra groups now survive a container update",
        "A button that fails now says so instead of spinning forever",
    ],
    "0.18.0": [
        "Memory warnings now show your top 5 memory users, and Stop buttons are sorted so the biggest win is always the top button",
        "New memory restart list - pick containers (like Plex) that just need a bounce to give memory back, and pressure alerts offer a one-tap 🔄 Restart. Set it up in /manage → Features",
    ],
    "0.17.0": [
        "Memory pressure alerts now show how much RAM each container is using right on the Stop buttons, so you can free the most memory first",
        "When a container is stopped to free memory, the bot tells you how much it was using and how much system memory is now free",
    ],
    "0.16.0": [
        "Health check in the Unraid dashboard - the container now reports healthy/unhealthy in docker ps and the Unraid UI, no setup needed",
        "No more lost boot alerts - alerts queued before your first /start are retried if Telegram is flaky during delivery, instead of vanishing",
    ],
    "0.15.0": [
        "Auto-heal that doesn't give up - containers that stay unhealthy after a restart are now retried up to your limit, failed restarts are reported honestly, and the give-up escalation actually fires",
        "Image-update alerts remember what they've told you - restarting the bot no longer re-announces updates you've already seen",
    ],
    "0.14.3": [
        "Chattier assistant - ask for the server status as a story, a captain's log, or a haiku and it'll play along with real numbers, while still staying focused on your server",
    ],
    "0.14.2": [
        "Cleaner AI replies - chat and /diagnose answers now render bold, italics and code properly instead of showing raw ** asterisks",
    ],
    "0.14.0": [
        "Turn features on from Telegram - /manage → ⚙️ Features explains and enables image-update alerts and lets you pick auto-heal containers, no config file editing",
    ],
    "0.13.0": [
        "Smarter diagnostics - /diagnose now reads full container state (health, volumes, ports, restart policy) and knows which alert triggered it, for more accurate fixes",
    ],
    "0.12.0": [
        "Image-update detection - notified when a newer image is available (opt-in: image_updates.enabled)",
        "Auto-heal - auto-restart unhealthy containers (opt-in: auto_heal.containers)",
        "Tests now run in CI on every change",
    ],
}
