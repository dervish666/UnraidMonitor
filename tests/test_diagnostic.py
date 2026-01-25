import pytest
from datetime import datetime
from unittest.mock import MagicMock


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


def test_diagnostic_service_gathers_context():
    """Test gathering container context from Docker."""
    from src.services.diagnostic import DiagnosticService

    # Mock Docker container
    mock_container = MagicMock()
    mock_container.logs.return_value = b"Error: connection refused\nRetrying..."
    mock_container.attrs = {
        "State": {
            "ExitCode": 1,
            "StartedAt": "2025-01-25T10:00:00Z",
        },
        "RestartCount": 2,
    }
    mock_container.image.tags = ["linuxserver/overseerr:latest"]

    mock_client = MagicMock()
    mock_client.containers.get.return_value = mock_container

    service = DiagnosticService(docker_client=mock_client, anthropic_client=None)

    context = service.gather_context("overseerr", lines=50)

    assert context.container_name == "overseerr"
    assert context.exit_code == 1
    assert context.restart_count == 2
    assert "Error: connection refused" in context.logs
    assert context.image == "linuxserver/overseerr:latest"


def test_diagnostic_service_handles_missing_container():
    """Test handling container not found."""
    import docker
    from src.services.diagnostic import DiagnosticService

    mock_client = MagicMock()
    mock_client.containers.get.side_effect = docker.errors.NotFound("not found")

    service = DiagnosticService(docker_client=mock_client, anthropic_client=None)

    context = service.gather_context("nonexistent", lines=50)

    assert context is None
