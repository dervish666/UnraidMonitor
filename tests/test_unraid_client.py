import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import aiohttp


def _make_ok_response():
    """Create a mock response with status 200 for connectivity test."""
    resp = AsyncMock()
    resp.status = 200
    resp.json = AsyncMock(return_value={"data": {"info": {"os": {"hostname": "Tower"}}}})
    return resp


def _make_post_context(response):
    """Wrap a mock response in an async context manager for session.post()."""
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=response)
    ctx.__aexit__ = AsyncMock(return_value=None)
    return ctx


def _make_session_with_ok_connect():
    """Create a mock session that passes the connectivity test in connect()."""
    mock_session = AsyncMock()
    ok_resp = _make_ok_response()
    mock_session.post = MagicMock(return_value=_make_post_context(ok_resp))
    return mock_session


@pytest.mark.asyncio
async def test_unraid_client_connect():
    """Test UnraidClientWrapper connects successfully."""
    from src.unraid.client import UnraidClientWrapper

    with patch("src.unraid.client.aiohttp.ClientSession") as MockSession, \
         patch("src.unraid.client.aiohttp.TCPConnector"):
        mock_session = _make_session_with_ok_connect()
        MockSession.return_value = mock_session

        wrapper = UnraidClientWrapper(
            host="192.168.1.100",
            api_key="test-key",
            port=443,
        )

        await wrapper.connect()

        assert wrapper.is_connected is True
        MockSession.assert_called_once()


@pytest.mark.asyncio
async def test_unraid_client_disconnect():
    """Test UnraidClientWrapper disconnects properly."""
    from src.unraid.client import UnraidClientWrapper

    with patch("src.unraid.client.aiohttp.ClientSession") as MockSession, \
         patch("src.unraid.client.aiohttp.TCPConnector"):
        mock_session = _make_session_with_ok_connect()
        MockSession.return_value = mock_session

        wrapper = UnraidClientWrapper(
            host="192.168.1.100",
            api_key="test-key",
        )

        await wrapper.connect()
        await wrapper.disconnect()

        assert wrapper.is_connected is False
        mock_session.close.assert_called_once()


@pytest.mark.asyncio
async def test_unraid_client_get_system_metrics():
    """Test getting system metrics."""
    from src.unraid.client import UnraidClientWrapper

    with patch("src.unraid.client.aiohttp.ClientSession") as MockSession, \
         patch("src.unraid.client.aiohttp.TCPConnector"):
        # Mock response for both connectivity test and system metrics query
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "data": {
                "info": {
                    "os": {"uptime": "5 days, 3 hours", "hostname": "Tower"},
                },
                "metrics": {
                    "cpu": {"percentTotal": 25.5},
                    "memory": {
                        "total": 34359738368,
                        "used": 17179869184,
                        "percentTotal": 50.0,
                    },
                },
            }
        })

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=_make_post_context(mock_response))
        MockSession.return_value = mock_session

        wrapper = UnraidClientWrapper(
            host="192.168.1.100",
            api_key="test-key",
        )
        await wrapper.connect()

        metrics = await wrapper.get_system_metrics()

        assert metrics["uptime"] == "5 days, 3 hours"
        assert metrics["cpu_percent"] == 25.5
        assert metrics["cpu_temperature"] is None  # Not available in GraphQL schema
        assert metrics["memory_percent"] == 50.0
        assert metrics["memory_used"] == 17179869184
        assert metrics["memory_total"] == 34359738368


@pytest.mark.asyncio
async def test_unraid_client_not_connected():
    """Test error when calling methods without connecting."""
    from src.unraid.client import UnraidClientWrapper, UnraidConnectionError

    wrapper = UnraidClientWrapper(
        host="192.168.1.100",
        api_key="test-key",
    )

    with pytest.raises(UnraidConnectionError):
        await wrapper.get_system_metrics()


@pytest.mark.asyncio
async def test_unraid_client_is_connected_property():
    """Test is_connected property."""
    from src.unraid.client import UnraidClientWrapper

    with patch("src.unraid.client.aiohttp.ClientSession") as MockSession, \
         patch("src.unraid.client.aiohttp.TCPConnector"):
        mock_session = _make_session_with_ok_connect()
        MockSession.return_value = mock_session

        wrapper = UnraidClientWrapper(
            host="192.168.1.100",
            api_key="test-key",
        )

        assert wrapper.is_connected is False

        await wrapper.connect()
        assert wrapper.is_connected is True

        await wrapper.disconnect()
        assert wrapper.is_connected is False


@pytest.mark.asyncio
async def test_unraid_client_get_array_status():
    """Test getting array status."""
    from src.unraid.client import UnraidClientWrapper

    with patch("src.unraid.client.aiohttp.ClientSession") as MockSession, \
         patch("src.unraid.client.aiohttp.TCPConnector"):
        # Mock response
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "data": {
                "array": {
                    "state": "Started",
                    "capacity": {"kilobytes": {"total": 100, "used": 50}},
                    "disks": [],
                }
            }
        })

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=_make_post_context(mock_response))
        MockSession.return_value = mock_session

        wrapper = UnraidClientWrapper(
            host="192.168.1.100",
            api_key="test-key",
        )
        await wrapper.connect()

        status = await wrapper.get_array_status()

        assert status["state"] == "Started"


@pytest.mark.asyncio
async def test_unraid_client_verify_ssl_false():
    """Test client can be created with verify_ssl=False and use_ssl=True."""
    from src.unraid.client import UnraidClientWrapper

    with patch("src.unraid.client.aiohttp.ClientSession") as MockSession, \
         patch("src.unraid.client.aiohttp.TCPConnector") as MockConnector, \
         patch("src.unraid.client.ssl.create_default_context") as mock_ssl:
        mock_ssl_context = MagicMock()
        mock_ssl.return_value = mock_ssl_context
        mock_session = _make_session_with_ok_connect()
        MockSession.return_value = mock_session

        wrapper = UnraidClientWrapper(
            host="192.168.1.100",
            api_key="test-key",
            port=443,
            verify_ssl=False,
            use_ssl=True,
        )

        await wrapper.connect()

        # Verify SSL context was configured for no verification
        mock_ssl.assert_called_once()
        assert mock_ssl_context.check_hostname is False


@pytest.mark.asyncio
async def test_unraid_client_http_mode():
    """Test client uses HTTP when use_ssl=False."""
    from src.unraid.client import UnraidClientWrapper

    wrapper = UnraidClientWrapper(
        host="192.168.1.100",
        api_key="test-key",
        port=80,
        use_ssl=False,
    )

    assert wrapper._base_url == "http://192.168.1.100/graphql"


@pytest.mark.asyncio
async def test_unraid_client_https_mode():
    """Test client uses HTTPS when use_ssl=True."""
    from src.unraid.client import UnraidClientWrapper

    wrapper = UnraidClientWrapper(
        host="192.168.1.100",
        api_key="test-key",
        port=443,
        use_ssl=True,
    )

    assert wrapper._base_url == "https://192.168.1.100/graphql"


@pytest.mark.asyncio
async def test_unraid_client_custom_port():
    """Test client includes port in URL for non-standard ports."""
    from src.unraid.client import UnraidClientWrapper

    wrapper = UnraidClientWrapper(
        host="192.168.1.100",
        api_key="test-key",
        port=8080,
        use_ssl=False,
    )

    assert wrapper._base_url == "http://192.168.1.100:8080/graphql"


@pytest.mark.asyncio
async def test_unraid_client_graphql_error():
    """Test handling of GraphQL errors."""
    from src.unraid.client import UnraidClientWrapper, UnraidConnectionError

    with patch("src.unraid.client.aiohttp.TCPConnector"):
        wrapper = UnraidClientWrapper(
            host="192.168.1.100",
            api_key="test-key",
        )

        with patch("src.unraid.client.aiohttp.ClientSession") as MockSession:
            mock_session = AsyncMock()

            # First call (connect test) returns OK, second call (query) returns error
            ok_response = _make_ok_response()
            error_response = AsyncMock()
            error_response.status = 200
            error_response.json = AsyncMock(return_value={
                "errors": [{"message": "Query failed"}]
            })

            mock_session.post = MagicMock(side_effect=[
                _make_post_context(ok_response),
                _make_post_context(error_response),
            ])

            MockSession.return_value = mock_session

            await wrapper.connect()

            with pytest.raises(UnraidConnectionError) as exc_info:
                await wrapper.get_system_metrics()

            assert "GraphQL errors" in str(exc_info.value)


@pytest.mark.asyncio
async def test_unraid_client_http_error():
    """Test handling of HTTP errors."""
    from src.unraid.client import UnraidClientWrapper, UnraidConnectionError

    with patch("src.unraid.client.aiohttp.TCPConnector"):
        wrapper = UnraidClientWrapper(
            host="192.168.1.100",
            api_key="test-key",
        )

        with patch("src.unraid.client.aiohttp.ClientSession") as MockSession:
            mock_session = AsyncMock()

            # First call (connect test) returns OK, second call (query) returns error
            ok_response = _make_ok_response()
            error_response = AsyncMock()
            error_response.status = 400
            error_response.text = AsyncMock(return_value="Bad Request")

            mock_session.post = MagicMock(side_effect=[
                _make_post_context(ok_response),
                _make_post_context(error_response),
            ])

            MockSession.return_value = mock_session

            await wrapper.connect()

            with pytest.raises(UnraidConnectionError) as exc_info:
                await wrapper.get_system_metrics()

            assert "400" in str(exc_info.value)


@pytest.mark.asyncio
async def test_unraid_client_connect_fails_on_bad_status():
    """Test connect() does not set connected on non-200 response."""
    from src.unraid.client import UnraidClientWrapper

    with patch("src.unraid.client.aiohttp.ClientSession") as MockSession, \
         patch("src.unraid.client.aiohttp.TCPConnector"):
        fail_response = AsyncMock()
        fail_response.status = 500
        fail_response.text = AsyncMock(return_value="Server Error")

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=_make_post_context(fail_response))
        MockSession.return_value = mock_session

        wrapper = UnraidClientWrapper(
            host="192.168.1.100",
            api_key="test-key",
        )

        await wrapper.connect()

        assert wrapper.is_connected is False


@pytest.mark.asyncio
async def test_unraid_client_connect_fails_on_exception():
    """Test connect() handles exceptions during connectivity test."""
    from src.unraid.client import UnraidClientWrapper

    with patch("src.unraid.client.aiohttp.ClientSession") as MockSession, \
         patch("src.unraid.client.aiohttp.TCPConnector"):
        mock_session = AsyncMock()
        mock_session.post = MagicMock(side_effect=aiohttp.ClientError("Connection refused"))
        MockSession.return_value = mock_session

        wrapper = UnraidClientWrapper(
            host="192.168.1.100",
            api_key="test-key",
        )

        await wrapper.connect()

        assert wrapper.is_connected is False


def test_safe_body_redacts_api_key():
    """API key echoed in an error body must never reach logs or exceptions."""
    from src.unraid.client import UnraidClientWrapper

    wrapper = UnraidClientWrapper(host="192.168.1.100", api_key="super-secret-key")
    body = 'error: bad request, x-api-key was "super-secret-key"'
    safe = wrapper._safe_body(body)
    assert "super-secret-key" not in safe
    assert "[REDACTED]" in safe


def test_safe_body_redacts_before_truncating():
    """A key straddling the 200-char cut-off must not partially leak."""
    from src.unraid.client import UnraidClientWrapper

    key = "k" * 40
    wrapper = UnraidClientWrapper(host="192.168.1.100", api_key=key)
    # Key starts at position 190 — truncate-first would leave a 10-char prefix
    body = "x" * 190 + key + "trailing"
    safe = wrapper._safe_body(body)
    assert "k" not in safe
    assert len(safe) <= 200


def test_safe_body_truncates_long_bodies():
    from src.unraid.client import UnraidClientWrapper

    wrapper = UnraidClientWrapper(host="192.168.1.100", api_key="key")
    assert len(wrapper._safe_body("a" * 5000)) == 200
