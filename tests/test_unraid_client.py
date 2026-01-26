import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import aiohttp


@pytest.mark.asyncio
async def test_unraid_client_connect():
    """Test UnraidClientWrapper connects successfully."""
    from src.unraid.client import UnraidClientWrapper

    with patch("src.unraid.client.aiohttp.ClientSession") as MockSession, \
         patch("src.unraid.client.aiohttp.TCPConnector"):
        mock_session = MagicMock()
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
        mock_session = AsyncMock()
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
        # Mock response for system info query
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "data": {
                "info": {
                    "os": {"uptime": "5 days, 3 hours", "hostname": "Tower"},
                },
            }
        })

        mock_context = AsyncMock()
        mock_context.__aenter__ = AsyncMock(return_value=mock_response)
        mock_context.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_context)
        MockSession.return_value = mock_session

        wrapper = UnraidClientWrapper(
            host="192.168.1.100",
            api_key="test-key",
        )
        await wrapper.connect()

        metrics = await wrapper.get_system_metrics()

        assert metrics["hostname"] == "Tower"
        assert metrics["uptime"] == "5 days, 3 hours"
        assert "cpu_percent" in metrics
        assert "memory_percent" in metrics


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
        mock_session = AsyncMock()
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
        mock_session.post = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_response), __aexit__=AsyncMock()))
        MockSession.return_value = mock_session

        wrapper = UnraidClientWrapper(
            host="192.168.1.100",
            api_key="test-key",
        )
        await wrapper.connect()

        status = await wrapper.get_array_status()

        assert status["state"] == "Started"


@pytest.mark.asyncio
async def test_unraid_client_get_vms():
    """Test getting VM list."""
    from src.unraid.client import UnraidClientWrapper

    with patch("src.unraid.client.aiohttp.ClientSession") as MockSession, \
         patch("src.unraid.client.aiohttp.TCPConnector"):
        # Mock response
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "data": {
                "vms": {
                    "domains": [
                        {"name": "Windows10", "id": "vm1", "state": "running"},
                        {"name": "Ubuntu", "id": "vm2", "state": "stopped"},
                    ]
                }
            }
        })

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_response), __aexit__=AsyncMock()))
        MockSession.return_value = mock_session

        wrapper = UnraidClientWrapper(
            host="192.168.1.100",
            api_key="test-key",
        )
        await wrapper.connect()

        vms = await wrapper.get_vms()

        assert len(vms) == 2
        assert vms[0]["name"] == "Windows10"


@pytest.mark.asyncio
async def test_unraid_client_get_ups_status():
    """Test getting UPS status."""
    from src.unraid.client import UnraidClientWrapper

    with patch("src.unraid.client.aiohttp.ClientSession") as MockSession, \
         patch("src.unraid.client.aiohttp.TCPConnector"):
        # Mock response
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "data": {
                "upsDevices": [
                    {"name": "APC UPS", "status": "online", "battery": {"chargeLevel": 100}},
                ]
            }
        })

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_response), __aexit__=AsyncMock()))
        MockSession.return_value = mock_session

        wrapper = UnraidClientWrapper(
            host="192.168.1.100",
            api_key="test-key",
        )
        await wrapper.connect()

        ups = await wrapper.get_ups_status()

        assert len(ups) == 1
        assert ups[0]["status"] == "online"


@pytest.mark.asyncio
async def test_unraid_client_verify_ssl_false():
    """Test client can be created with verify_ssl=False and use_ssl=True."""
    from src.unraid.client import UnraidClientWrapper

    with patch("src.unraid.client.aiohttp.ClientSession") as MockSession, \
         patch("src.unraid.client.aiohttp.TCPConnector") as MockConnector, \
         patch("src.unraid.client.ssl.create_default_context") as mock_ssl:
        mock_ssl_context = MagicMock()
        mock_ssl.return_value = mock_ssl_context
        mock_session = MagicMock()
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

        # Create a real session but mock the post method
        with patch("src.unraid.client.aiohttp.ClientSession") as MockSession:
            mock_session = AsyncMock()

            # Mock response with GraphQL error
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={
                "errors": [{"message": "Query failed"}]
            })

            # Create async context manager for post
            mock_context = AsyncMock()
            mock_context.__aenter__ = AsyncMock(return_value=mock_response)
            mock_context.__aexit__ = AsyncMock(return_value=None)
            mock_session.post = MagicMock(return_value=mock_context)

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

            # Mock response with HTTP error
            mock_response = AsyncMock()
            mock_response.status = 400
            mock_response.text = AsyncMock(return_value="Bad Request")

            # Create async context manager for post
            mock_context = AsyncMock()
            mock_context.__aenter__ = AsyncMock(return_value=mock_response)
            mock_context.__aexit__ = AsyncMock(return_value=None)
            mock_session.post = MagicMock(return_value=mock_context)

            MockSession.return_value = mock_session

            await wrapper.connect()

            with pytest.raises(UnraidConnectionError) as exc_info:
                await wrapper.get_system_metrics()

            assert "400" in str(exc_info.value)
