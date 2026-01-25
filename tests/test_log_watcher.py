import pytest
from unittest.mock import MagicMock, AsyncMock, patch


def test_log_watcher_matches_error_patterns():
    from src.monitors.log_watcher import matches_error_pattern

    error_patterns = ["error", "exception", "fatal"]
    ignore_patterns = ["DEBUG"]

    assert matches_error_pattern("Something error happened", error_patterns, ignore_patterns) is True
    assert matches_error_pattern("FATAL: Cannot connect", error_patterns, ignore_patterns) is True
    assert matches_error_pattern("All good here", error_patterns, ignore_patterns) is False


def test_log_watcher_respects_ignore_patterns():
    from src.monitors.log_watcher import matches_error_pattern

    error_patterns = ["error"]
    ignore_patterns = ["DeprecationWarning", "DEBUG"]

    assert matches_error_pattern("DEBUG: error in test", error_patterns, ignore_patterns) is False
    assert matches_error_pattern("DeprecationWarning: error", error_patterns, ignore_patterns) is False
    assert matches_error_pattern("Real error occurred", error_patterns, ignore_patterns) is True


def test_log_watcher_case_insensitive():
    from src.monitors.log_watcher import matches_error_pattern

    error_patterns = ["error", "fatal"]
    ignore_patterns = []

    assert matches_error_pattern("ERROR: something", error_patterns, ignore_patterns) is True
    assert matches_error_pattern("Error: something", error_patterns, ignore_patterns) is True
    assert matches_error_pattern("FATAL crash", error_patterns, ignore_patterns) is True


def test_log_watcher_init():
    from src.monitors.log_watcher import LogWatcher

    containers = ["container1", "container2"]
    error_patterns = ["error", "fatal"]
    ignore_patterns = ["DEBUG"]
    on_error = AsyncMock()

    watcher = LogWatcher(
        containers=containers,
        error_patterns=error_patterns,
        ignore_patterns=ignore_patterns,
        on_error=on_error,
    )

    assert watcher.containers == containers
    assert watcher.error_patterns == error_patterns
    assert watcher.ignore_patterns == ignore_patterns
    assert watcher.on_error == on_error
    assert watcher._client is None
    assert watcher._running is False
    assert watcher._tasks == []


def test_log_watcher_connect():
    from src.monitors.log_watcher import LogWatcher

    watcher = LogWatcher(
        containers=["test"],
        error_patterns=["error"],
        ignore_patterns=[],
    )

    with patch("docker.DockerClient") as mock_docker:
        watcher.connect()
        mock_docker.assert_called_once_with(base_url="unix:///var/run/docker.sock")
        assert watcher._client is not None


@pytest.mark.asyncio
async def test_log_watcher_start_requires_connection():
    from src.monitors.log_watcher import LogWatcher

    watcher = LogWatcher(
        containers=["test"],
        error_patterns=["error"],
        ignore_patterns=[],
    )

    with pytest.raises(RuntimeError, match="Not connected to Docker"):
        await watcher.start()


@pytest.mark.asyncio
async def test_log_watcher_calls_on_error_for_matching_lines():
    from src.monitors.log_watcher import LogWatcher

    on_error = AsyncMock()
    watcher = LogWatcher(
        containers=["test-container"],
        error_patterns=["error", "fatal"],
        ignore_patterns=["DEBUG"],
        on_error=on_error,
    )

    # Mock the Docker client and container
    mock_client = MagicMock()
    mock_container = MagicMock()

    # Simulate log lines
    log_lines = [
        b"Normal log line\n",
        b"ERROR: Something went wrong\n",
        b"DEBUG: error in test\n",  # Should be ignored
        b"FATAL: Critical failure\n",
    ]

    mock_container.logs.return_value = log_lines
    mock_client.containers.get.return_value = mock_container
    watcher._client = mock_client
    watcher._running = True

    # Process logs
    await watcher._stream_logs("test-container")

    # Should be called twice (ERROR and FATAL, but not DEBUG)
    assert on_error.call_count == 2

    # Check the calls
    calls = on_error.call_args_list
    assert calls[0][0][0] == "test-container"
    assert "ERROR: Something went wrong" in calls[0][0][1]
    assert calls[1][0][0] == "test-container"
    assert "FATAL: Critical failure" in calls[1][0][1]


def test_log_watcher_stop():
    from src.monitors.log_watcher import LogWatcher

    watcher = LogWatcher(
        containers=["test"],
        error_patterns=["error"],
        ignore_patterns=[],
    )

    # Create mock tasks
    mock_task1 = MagicMock()
    mock_task2 = MagicMock()
    watcher._tasks = [mock_task1, mock_task2]
    watcher._running = True

    watcher.stop()

    assert watcher._running is False
    mock_task1.cancel.assert_called_once()
    mock_task2.cancel.assert_called_once()
