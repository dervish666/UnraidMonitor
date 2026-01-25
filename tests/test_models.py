from datetime import datetime


def test_container_info_creation():
    from src.models import ContainerInfo

    info = ContainerInfo(
        name="radarr",
        status="running",
        health="healthy",
        image="linuxserver/radarr:latest",
        started_at=datetime(2025, 1, 25, 10, 0, 0),
    )
    assert info.name == "radarr"
    assert info.status == "running"
    assert info.health == "healthy"


def test_container_info_health_optional():
    from src.models import ContainerInfo

    info = ContainerInfo(
        name="plex",
        status="running",
        health=None,
        image="linuxserver/plex:latest",
        started_at=None,
    )
    assert info.health is None
    assert info.started_at is None
