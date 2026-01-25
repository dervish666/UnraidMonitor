import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime


def test_parse_container_from_docker_api():
    from src.monitors.docker_events import parse_container

    # Mock Docker container object
    mock_container = MagicMock()
    mock_container.name = "radarr"
    mock_container.status = "running"
    mock_container.image.tags = ["linuxserver/radarr:latest"]
    mock_container.attrs = {
        "State": {
            "Health": {"Status": "healthy"},
            "StartedAt": "2025-01-25T10:00:00.000000000Z",
        }
    }

    info = parse_container(mock_container)
    assert info.name == "radarr"
    assert info.status == "running"
    assert info.health == "healthy"
    assert info.image == "linuxserver/radarr:latest"


def test_parse_container_without_health_check():
    from src.monitors.docker_events import parse_container

    mock_container = MagicMock()
    mock_container.name = "plex"
    mock_container.status = "running"
    mock_container.image.tags = ["linuxserver/plex:latest"]
    mock_container.attrs = {
        "State": {
            "StartedAt": "2025-01-25T10:00:00.000000000Z",
        }
    }

    info = parse_container(mock_container)
    assert info.health is None


def test_parse_container_no_image_tags():
    from src.monitors.docker_events import parse_container

    mock_container = MagicMock()
    mock_container.name = "test"
    mock_container.status = "running"
    mock_container.image.tags = []
    mock_container.image.id = "sha256:abc123"
    mock_container.attrs = {"State": {}}

    info = parse_container(mock_container)
    assert info.image == "sha256:abc123"
