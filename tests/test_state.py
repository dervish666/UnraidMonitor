from datetime import datetime


def test_state_manager_update_and_get():
    from src.state import ContainerStateManager
    from src.models import ContainerInfo

    manager = ContainerStateManager()
    info = ContainerInfo(
        name="radarr",
        status="running",
        health="healthy",
        image="linuxserver/radarr:latest",
        started_at=datetime.now(),
    )
    manager.update(info)

    result = manager.get("radarr")
    assert result is not None
    assert result.name == "radarr"


def test_state_manager_get_all():
    from src.state import ContainerStateManager
    from src.models import ContainerInfo

    manager = ContainerStateManager()
    manager.update(ContainerInfo("a", "running", None, "img", None))
    manager.update(ContainerInfo("b", "exited", None, "img", None))

    all_containers = manager.get_all()
    assert len(all_containers) == 2


def test_state_manager_find_by_partial_name():
    from src.state import ContainerStateManager
    from src.models import ContainerInfo

    manager = ContainerStateManager()
    manager.update(ContainerInfo("radarr", "running", None, "img", None))
    manager.update(ContainerInfo("sonarr", "running", None, "img", None))
    manager.update(ContainerInfo("radar-test", "running", None, "img", None))

    matches = manager.find_by_name("radar")
    assert len(matches) == 2
    names = [m.name for m in matches]
    assert "radarr" in names
    assert "radar-test" in names


def test_state_manager_find_by_name_exact_match_priority():
    """Test that exact match returns single result even when substring matches exist."""
    from src.state import ContainerStateManager
    from src.models import ContainerInfo

    manager = ContainerStateManager()
    manager.update(ContainerInfo("plex", "running", None, "img", None))
    manager.update(ContainerInfo("Plex-Rewind", "running", None, "img", None))
    manager.update(ContainerInfo("plex-meta-manager", "running", None, "img", None))

    # Exact match should return only the exact container
    matches = manager.find_by_name("plex")
    assert len(matches) == 1
    assert matches[0].name == "plex"

    # Partial match still works when no exact match
    matches = manager.find_by_name("Rewind")
    assert len(matches) == 1
    assert matches[0].name == "Plex-Rewind"


def test_state_manager_summary():
    from src.state import ContainerStateManager
    from src.models import ContainerInfo

    manager = ContainerStateManager()
    manager.update(ContainerInfo("a", "running", "healthy", "img", None))
    manager.update(ContainerInfo("b", "running", "unhealthy", "img", None))
    manager.update(ContainerInfo("c", "exited", None, "img", None))
    manager.update(ContainerInfo("d", "running", None, "img", None))

    summary = manager.get_summary()
    assert summary["running"] == 3
    assert summary["stopped"] == 1
    assert summary["unhealthy"] == 1
