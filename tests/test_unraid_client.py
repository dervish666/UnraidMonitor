import pytest
from unittest.mock import ANY, AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_unraid_client_connect():
    """Test UnraidClient connects successfully."""
    from src.unraid.client import UnraidClientWrapper

    with patch("src.unraid.client.UnraidClient") as MockClient, \
         patch("src.unraid.client.aiohttp.ClientSession") as MockSession, \
         patch("src.unraid.client.aiohttp.TCPConnector"):
        mock_instance = AsyncMock()
        MockClient.return_value = mock_instance
        mock_session = MagicMock()
        MockSession.return_value = mock_session

        wrapper = UnraidClientWrapper(
            host="192.168.1.100",
            api_key="test-key",
            port=443,
        )

        await wrapper.connect()

        MockClient.assert_called_once_with(
            "192.168.1.100", "test-key", https_port=443, verify_ssl=True, session=mock_session
        )
        mock_instance.__aenter__.assert_called_once()


@pytest.mark.asyncio
async def test_unraid_client_disconnect():
    """Test UnraidClient disconnects properly."""
    from src.unraid.client import UnraidClientWrapper

    with patch("src.unraid.client.UnraidClient") as MockClient, \
         patch("src.unraid.client.aiohttp.ClientSession") as MockSession, \
         patch("src.unraid.client.aiohttp.TCPConnector"):
        mock_instance = AsyncMock()
        MockClient.return_value = mock_instance
        mock_session = AsyncMock()
        MockSession.return_value = mock_session

        wrapper = UnraidClientWrapper(
            host="192.168.1.100",
            api_key="test-key",
        )

        await wrapper.connect()
        await wrapper.disconnect()

        mock_instance.__aexit__.assert_called_once()
        mock_session.close.assert_called_once()


@pytest.mark.asyncio
async def test_unraid_client_get_system_metrics():
    """Test getting system metrics."""
    from src.unraid.client import UnraidClientWrapper

    with patch("src.unraid.client.UnraidClient") as MockClient, \
         patch("src.unraid.client.aiohttp.ClientSession"), \
         patch("src.unraid.client.aiohttp.TCPConnector"):
        mock_instance = AsyncMock()
        mock_instance.get_system_metrics = AsyncMock(return_value={
            "cpu_percent": 25.5,
            "cpu_temperature": 45.0,
            "memory_percent": 60.0,
            "memory_used": 1024 * 1024 * 1024 * 32,
            "uptime": "5 days, 3 hours",
        })
        MockClient.return_value = mock_instance

        wrapper = UnraidClientWrapper(
            host="192.168.1.100",
            api_key="test-key",
        )
        await wrapper.connect()

        metrics = await wrapper.get_system_metrics()

        assert metrics["cpu_percent"] == 25.5
        assert metrics["cpu_temperature"] == 45.0
        assert metrics["memory_percent"] == 60.0


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

    with patch("src.unraid.client.UnraidClient") as MockClient, \
         patch("src.unraid.client.aiohttp.ClientSession") as MockSession, \
         patch("src.unraid.client.aiohttp.TCPConnector"):
        mock_instance = AsyncMock()
        MockClient.return_value = mock_instance
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

    with patch("src.unraid.client.UnraidClient") as MockClient, \
         patch("src.unraid.client.aiohttp.ClientSession"), \
         patch("src.unraid.client.aiohttp.TCPConnector"):
        mock_instance = AsyncMock()
        mock_instance.get_array_status = AsyncMock(return_value={
            "state": "Started",
            "capacity": {"total": 100, "used": 50},
            "disks": [],
        })
        MockClient.return_value = mock_instance

        wrapper = UnraidClientWrapper(
            host="192.168.1.100",
            api_key="test-key",
        )
        await wrapper.connect()

        status = await wrapper.get_array_status()

        assert status["state"] == "Started"
        mock_instance.get_array_status.assert_called_once()


@pytest.mark.asyncio
async def test_unraid_client_get_vms():
    """Test getting VM list."""
    from src.unraid.client import UnraidClientWrapper

    with patch("src.unraid.client.UnraidClient") as MockClient, \
         patch("src.unraid.client.aiohttp.ClientSession"), \
         patch("src.unraid.client.aiohttp.TCPConnector"):
        mock_instance = AsyncMock()
        mock_instance.get_vms = AsyncMock(return_value=[
            {"name": "Windows10", "id": "vm1", "state": "running"},
            {"name": "Ubuntu", "id": "vm2", "state": "stopped"},
        ])
        MockClient.return_value = mock_instance

        wrapper = UnraidClientWrapper(
            host="192.168.1.100",
            api_key="test-key",
        )
        await wrapper.connect()

        vms = await wrapper.get_vms()

        assert len(vms) == 2
        assert vms[0]["name"] == "Windows10"
        mock_instance.get_vms.assert_called_once()


@pytest.mark.asyncio
async def test_unraid_client_get_ups_status():
    """Test getting UPS status."""
    from src.unraid.client import UnraidClientWrapper

    with patch("src.unraid.client.UnraidClient") as MockClient, \
         patch("src.unraid.client.aiohttp.ClientSession"), \
         patch("src.unraid.client.aiohttp.TCPConnector"):
        mock_instance = AsyncMock()
        mock_instance.get_ups_status = AsyncMock(return_value=[
            {"name": "APC UPS", "status": "online", "charge": 100},
        ])
        MockClient.return_value = mock_instance

        wrapper = UnraidClientWrapper(
            host="192.168.1.100",
            api_key="test-key",
        )
        await wrapper.connect()

        ups = await wrapper.get_ups_status()

        assert len(ups) == 1
        assert ups[0]["status"] == "online"
        mock_instance.get_ups_status.assert_called_once()


@pytest.mark.asyncio
async def test_unraid_client_verify_ssl_false():
    """Test client can be created with verify_ssl=False."""
    from src.unraid.client import UnraidClientWrapper

    with patch("src.unraid.client.UnraidClient") as MockClient, \
         patch("src.unraid.client.aiohttp.ClientSession") as MockSession, \
         patch("src.unraid.client.aiohttp.TCPConnector"):
        mock_instance = AsyncMock()
        MockClient.return_value = mock_instance
        mock_session = MagicMock()
        MockSession.return_value = mock_session

        wrapper = UnraidClientWrapper(
            host="192.168.1.100",
            api_key="test-key",
            port=443,
            verify_ssl=False,
        )

        await wrapper.connect()

        MockClient.assert_called_once_with(
            "192.168.1.100", "test-key", https_port=443, verify_ssl=False, session=mock_session
        )
