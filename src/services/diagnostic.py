"""AI-powered container diagnostics service."""

import logging
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class DiagnosticContext:
    """Context for a diagnostic request."""

    container_name: str
    logs: str
    exit_code: int | None
    image: str
    uptime_seconds: int | None
    restart_count: int
    brief_summary: str | None = None
    created_at: datetime | None = None

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now()
