import pytest
from datetime import datetime


def test_diagnostic_context_creation():
    """Test DiagnosticContext dataclass creation."""
    from src.services.diagnostic import DiagnosticContext

    context = DiagnosticContext(
        container_name="overseerr",
        logs="Error: connection refused",
        exit_code=1,
        image="linuxserver/overseerr:latest",
        uptime_seconds=3600,
        restart_count=2,
        brief_summary="Container crashed due to database connection failure.",
    )

    assert context.container_name == "overseerr"
    assert context.exit_code == 1
    assert context.restart_count == 2
    assert "database" in context.brief_summary
