import pytest
from datetime import datetime
from unittest.mock import MagicMock, AsyncMock

from src.services.llm.provider import LLMResponse


def make_mock_provider(text=""):
    """Create a mock LLM provider returning the given text."""
    provider = MagicMock()
    provider.supports_tools = False
    provider.model_name = "test-model"
    provider.provider_name = "test"
    provider.chat = AsyncMock(return_value=LLMResponse(
        text=text,
        stop_reason="end",
        tool_calls=None,
    ))
    return provider


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


@pytest.mark.asyncio
async def test_diagnostic_service_gathers_context():
    """Test gathering container context from Docker."""
    from src.services.diagnostic import DiagnosticService

    # Mock Docker container with full attrs
    mock_container = MagicMock()
    mock_container.logs.return_value = b"Error: connection refused\nRetrying..."
    mock_container.attrs = {
        "State": {
            "ExitCode": 1,
            "Status": "exited",
            "Running": False,
            "OOMKilled": False,
            "Error": "",
            "StartedAt": "2025-01-25T10:00:00Z",
        },
        "RestartCount": 2,
        "Config": {
            "Env": ["PUID=99", "PGID=100", "TZ=Europe/London", "API_KEY=secret123"],
        },
        "HostConfig": {
            "PortBindings": {"5055/tcp": [{"HostPort": "5055"}]},
            "RestartPolicy": {"Name": "unless-stopped", "MaximumRetryCount": 0},
        },
        "Mounts": [
            {"Source": "/mnt/user/appdata/overseerr", "Destination": "/config", "Mode": "rw"},
        ],
    }
    mock_container.image.tags = ["linuxserver/overseerr:latest"]

    mock_client = MagicMock()
    mock_client.containers.get.return_value = mock_container

    service = DiagnosticService(docker_client=mock_client, provider=None)

    context = await service.gather_context("overseerr", lines=50)

    assert context.container_name == "overseerr"
    assert context.exit_code == 1
    assert context.restart_count == 2
    assert "Error: connection refused" in context.logs
    assert context.image == "linuxserver/overseerr:latest"
    assert context.status == "exited"
    assert context.running is False
    assert context.oom_killed is False
    assert len(context.volumes) == 1
    assert "/config" in context.volumes[0]
    # API_KEY should be filtered out
    assert not any("API_KEY" in e for e in context.env_vars)
    assert any("PUID=99" in e for e in context.env_vars)
    assert len(context.ports) == 1
    assert "5055" in context.ports[0]
    assert "unless-stopped" in context.restart_policy


@pytest.mark.asyncio
async def test_diagnostic_context_running_container():
    """Test that running containers are correctly identified."""
    from src.services.diagnostic import DiagnosticService

    mock_container = MagicMock()
    mock_container.logs.return_value = b"[eac3] Error decoding audio"
    mock_container.attrs = {
        "State": {
            "ExitCode": 0,
            "Status": "running",
            "Running": True,
            "OOMKilled": False,
            "Error": "",
            "StartedAt": "2025-01-25T10:00:00Z",
        },
        "RestartCount": 0,
        "Config": {"Env": ["TZ=Europe/London"]},
        "HostConfig": {
            "PortBindings": {},
            "RestartPolicy": {"Name": "no", "MaximumRetryCount": 0},
        },
        "Mounts": [],
    }
    mock_container.image.tags = ["plexinc/pms-docker:latest"]

    mock_client = MagicMock()
    mock_client.containers.get.return_value = mock_container

    service = DiagnosticService(docker_client=mock_client, provider=None)

    context = await service.gather_context(
        "plex", lines=50, alert_context="Error alert (container still running with errors)",
    )

    assert context.running is True
    assert context.status == "running"
    assert context.alert_context == "Error alert (container still running with errors)"


@pytest.mark.asyncio
async def test_diagnostic_service_handles_missing_container():
    """Test handling container not found."""
    import docker
    from src.services.diagnostic import DiagnosticService

    mock_client = MagicMock()
    mock_client.containers.get.side_effect = docker.errors.NotFound("not found")

    service = DiagnosticService(docker_client=mock_client, provider=None)

    context = await service.gather_context("nonexistent", lines=50)

    assert context is None


@pytest.mark.asyncio
async def test_diagnostic_service_analyzes_with_claude():
    """Test calling AI provider for analysis."""
    from src.services.diagnostic import DiagnosticService, DiagnosticContext

    mock_client = MagicMock()
    mock_provider = make_mock_provider(
        "The container crashed due to OOM. Increase memory limits."
    )

    service = DiagnosticService(docker_client=mock_client, provider=mock_provider)

    context = DiagnosticContext(
        container_name="overseerr",
        logs="Error: JavaScript heap out of memory",
        exit_code=137,
        image="linuxserver/overseerr:latest",
        uptime_seconds=3600,
        restart_count=2,
    )

    result = await service.analyze(context)

    assert "OOM" in result or "memory" in result.lower()
    mock_provider.chat.assert_called_once()


@pytest.mark.asyncio
async def test_diagnostic_service_stores_and_retrieves_context():
    """Test storing context for follow-up."""
    from src.services.diagnostic import DiagnosticService, DiagnosticContext

    mock_client = MagicMock()
    mock_provider = make_mock_provider(
        "Detailed analysis: The root cause is..."
    )

    service = DiagnosticService(docker_client=mock_client, provider=mock_provider)

    context = DiagnosticContext(
        container_name="overseerr",
        logs="Error log",
        exit_code=1,
        image="linuxserver/overseerr:latest",
        uptime_seconds=3600,
        restart_count=0,
        brief_summary="Container crashed.",
    )

    # Store context for user
    service.store_context(user_id=123, context=context)

    # Check pending
    assert service.has_pending(123) is True
    assert service.has_pending(456) is False

    # Get details
    details = await service.get_details(123)

    assert details is not None
    assert "root cause" in details.lower() or "Detailed" in details

    # Context should be cleared after retrieval
    assert service.has_pending(123) is False


def test_prompt_running_container_no_exit_framing():
    """Verify that a running container's prompt says RUNNING, not exit code."""
    from src.services.diagnostic import DiagnosticService, DiagnosticContext

    service = DiagnosticService(docker_client=MagicMock(), provider=None)

    context = DiagnosticContext(
        container_name="plex",
        logs="[eac3] Error decoding audio",
        exit_code=0,
        image="plexinc/pms-docker:latest",
        uptime_seconds=86400,
        restart_count=0,
        status="running",
        running=True,
        volumes=["/mnt/user/appdata/plex -> /config (rw)", "/tmp/transcode -> /transcode (rw)"],
        env_vars=["PUID=99", "TZ=Europe/London", "TRANSCODE_DIR=/transcode"],
        alert_context="Error alert (container still running with errors)",
    )

    prompt = service._build_analysis_prompt(context)

    assert "**RUNNING**" in prompt
    assert "EXITED" not in prompt
    assert "exit code 0" not in prompt.lower()
    assert "/transcode" in prompt
    assert "TRANSCODE_DIR" in prompt
    assert "Error alert" in prompt


def test_prompt_exited_container_shows_exit_code():
    """Verify that an exited container's prompt includes exit code."""
    from src.services.diagnostic import DiagnosticService, DiagnosticContext

    service = DiagnosticService(docker_client=MagicMock(), provider=None)

    context = DiagnosticContext(
        container_name="overseerr",
        logs="Segmentation fault",
        exit_code=139,
        image="linuxserver/overseerr:latest",
        uptime_seconds=120,
        restart_count=3,
        status="exited",
        running=False,
        oom_killed=True,
    )

    prompt = service._build_analysis_prompt(context)

    assert "**EXITED**" in prompt
    assert "exit code 139" in prompt
    assert "OOM Killed" in prompt


def test_filter_env_vars():
    """Test that secret-containing env vars are filtered out."""
    from src.services.diagnostic import _filter_env_vars

    env = [
        "PUID=99",
        "PGID=100",
        "TZ=Europe/London",
        "API_KEY=supersecret",
        "DB_PASSWORD=hunter2",
        "TRANSCODE_DIR=/transcode",
        "PLEX_CLAIM=claim-xyz",
        "MY_SECRET_THING=abc",
        "SOME_AUTH_TOKEN=abc",
    ]

    filtered = _filter_env_vars(env)

    names = [e.split("=")[0] for e in filtered]
    assert "PUID" in names
    assert "TZ" in names
    assert "TRANSCODE_DIR" in names
    assert "API_KEY" not in names
    assert "DB_PASSWORD" not in names
    assert "MY_SECRET_THING" not in names
    assert "SOME_AUTH_TOKEN" not in names


async def test_gather_context_returns_none_on_docker_api_error():
    """A daemon hiccup on containers.get degrades to None instead of crashing the handler."""
    import docker
    from src.services.diagnostic import DiagnosticService

    mock_client = MagicMock()
    mock_client.containers.get.side_effect = docker.errors.APIError("daemon restarting")

    service = DiagnosticService(docker_client=mock_client, provider=None)
    context = await service.gather_context("overseerr", lines=50)

    assert context is None


async def test_gather_context_degrades_when_logs_unavailable():
    """A failing log endpoint still yields a usable DiagnosticContext."""
    from src.services.diagnostic import DiagnosticService

    mock_container = MagicMock()
    mock_container.logs.side_effect = Exception("read timeout")
    mock_container.attrs = {
        "State": {"ExitCode": 1, "Status": "exited", "Running": False,
                  "OOMKilled": False, "Error": "", "StartedAt": "2025-01-25T10:00:00Z"},
        "RestartCount": 0,
        "Config": {"Env": []},
        "HostConfig": {"PortBindings": {}, "RestartPolicy": {"Name": "no", "MaximumRetryCount": 0}},
        "Mounts": [],
    }
    mock_container.image.tags = ["linuxserver/overseerr:latest"]

    mock_client = MagicMock()
    mock_client.containers.get.return_value = mock_container

    service = DiagnosticService(docker_client=mock_client, provider=None)
    context = await service.gather_context("overseerr", lines=50)

    assert context is not None
    assert context.logs == "(logs unavailable)"
    assert context.container_name == "overseerr"
