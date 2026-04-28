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

# ---------------------------------------------------------------------------
# Unraid monitoring defaults
# ---------------------------------------------------------------------------
UNRAID_POLL_SYSTEM_SECONDS = 30
UNRAID_POLL_ARRAY_SECONDS = 300
UNRAID_CPU_TEMP_THRESHOLD = 80
UNRAID_CPU_USAGE_THRESHOLD = 95
UNRAID_MEMORY_USAGE_THRESHOLD = 90
UNRAID_DISK_TEMP_THRESHOLD = 50
UNRAID_ARRAY_USAGE_THRESHOLD = 85

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
DEFAULT_HAIKU_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_SONNET_MODEL = "claude-sonnet-4-5-20250929"
DEFAULT_OLLAMA_MODEL = "qwen2.5:7b"
DEFAULT_OLLAMA_HOST = "http://localhost:11434"
