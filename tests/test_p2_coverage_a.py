"""Tests for P2 coverage gaps: container_control pull/recreate, AlertManagerProxy,
and AlertManager exit code / truncation behavior."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from datetime import datetime, timedelta

import docker


# ---------------------------------------------------------------------------
# P2-1: ContainerController._extract_run_config and pull_and_recreate
# ---------------------------------------------------------------------------


class TestExtractRunConfig:
    """Tests for ContainerController._extract_run_config."""

    def _make_controller(self):
        from src.services.container_control import ContainerController

        return ContainerController(
            docker_client=MagicMock(),
            protected_containers=[],
        )

    def test_extracts_environment_from_config(self):
        """_extract_run_config maps Config.Env to environment."""
        controller = self._make_controller()
        attrs = {
            "Config": {"Env": ["FOO=bar", "BAZ=qux"]},
            "HostConfig": {},
        }
        result = controller._extract_run_config(attrs)
        assert result["environment"] == ["FOO=bar", "BAZ=qux"]

    def test_extracts_labels_from_config(self):
        controller = self._make_controller()
        attrs = {
            "Config": {"Labels": {"com.example.key": "val"}},
            "HostConfig": {},
        }
        result = controller._extract_run_config(attrs)
        assert result["labels"] == {"com.example.key": "val"}

    def test_extracts_command_entrypoint_workdir_user(self):
        controller = self._make_controller()
        attrs = {
            "Config": {
                "Cmd": ["/bin/sh", "-c", "echo hello"],
                "Entrypoint": ["/init"],
                "WorkingDir": "/app",
                "User": "1000:1000",
            },
            "HostConfig": {},
        }
        result = controller._extract_run_config(attrs)
        assert result["command"] == ["/bin/sh", "-c", "echo hello"]
        assert result["entrypoint"] == ["/init"]
        assert result["working_dir"] == "/app"
        assert result["user"] == "1000:1000"

    def test_extracts_volumes_ports_restart_policy_from_host_config(self):
        controller = self._make_controller()
        attrs = {
            "Config": {},
            "HostConfig": {
                "Binds": ["/host/data:/container/data:rw"],
                "PortBindings": {"8080/tcp": [{"HostPort": "80"}]},
                "RestartPolicy": {"Name": "unless-stopped"},
                "NetworkMode": "bridge",
            },
        }
        result = controller._extract_run_config(attrs)
        assert result["volumes"] == ["/host/data:/container/data:rw"]
        assert result["ports"] == {"8080/tcp": [{"HostPort": "80"}]}
        assert result["restart_policy"] == {"Name": "unless-stopped"}
        assert result["network_mode"] == "bridge"

    def test_extracts_privileged_cap_add_cap_drop(self):
        controller = self._make_controller()
        attrs = {
            "Config": {},
            "HostConfig": {
                "Privileged": True,
                "CapAdd": ["NET_ADMIN"],
                "CapDrop": ["MKNOD"],
            },
        }
        result = controller._extract_run_config(attrs)
        assert result["privileged"] is True
        assert result["cap_add"] == ["NET_ADMIN"]
        assert result["cap_drop"] == ["MKNOD"]

    def test_extracts_devices_formatted(self):
        """Devices should be formatted as PathOnHost:PathInContainer:CgroupPermissions."""
        controller = self._make_controller()
        attrs = {
            "Config": {},
            "HostConfig": {
                "Devices": [
                    {
                        "PathOnHost": "/dev/dri",
                        "PathInContainer": "/dev/dri",
                        "CgroupPermissions": "rwm",
                    }
                ]
            },
        }
        result = controller._extract_run_config(attrs)
        assert result["devices"] == ["/dev/dri:/dev/dri:rwm"]

    def test_extracts_resource_limits(self):
        controller = self._make_controller()
        attrs = {
            "Config": {},
            "HostConfig": {
                "NanoCpus": 2_000_000_000,
                "CpuShares": 512,
                "Memory": 536870912,
                "MemoryReservation": 268435456,
            },
        }
        result = controller._extract_run_config(attrs)
        assert result["nano_cpus"] == 2_000_000_000
        assert result["cpu_shares"] == 512
        assert result["mem_limit"] == 536870912
        assert result["mem_reservation"] == 268435456

    def test_skips_default_ipc_mode(self):
        """IpcMode 'private' is the default and should be omitted."""
        controller = self._make_controller()
        attrs = {
            "Config": {},
            "HostConfig": {"IpcMode": "private"},
        }
        result = controller._extract_run_config(attrs)
        assert "ipc_mode" not in result

    def test_includes_non_default_ipc_mode(self):
        controller = self._make_controller()
        attrs = {
            "Config": {},
            "HostConfig": {"IpcMode": "host"},
        }
        result = controller._extract_run_config(attrs)
        assert result["ipc_mode"] == "host"

    def test_skips_default_shm_size(self):
        """ShmSize equal to default 64MB (67108864) should be omitted."""
        controller = self._make_controller()
        attrs = {
            "Config": {},
            "HostConfig": {"ShmSize": 67108864},
        }
        result = controller._extract_run_config(attrs)
        assert "shm_size" not in result

    def test_includes_non_default_shm_size(self):
        controller = self._make_controller()
        attrs = {
            "Config": {},
            "HostConfig": {"ShmSize": 134217728},
        }
        result = controller._extract_run_config(attrs)
        assert result["shm_size"] == 134217728

    def test_skips_zero_cpu_shares(self):
        controller = self._make_controller()
        attrs = {
            "Config": {},
            "HostConfig": {"CpuShares": 0},
        }
        result = controller._extract_run_config(attrs)
        assert "cpu_shares" not in result

    def test_skips_zero_memory(self):
        controller = self._make_controller()
        attrs = {
            "Config": {},
            "HostConfig": {"Memory": 0, "MemoryReservation": 0},
        }
        result = controller._extract_run_config(attrs)
        assert "mem_limit" not in result
        assert "mem_reservation" not in result

    def test_empty_attrs_returns_empty_config(self):
        controller = self._make_controller()
        result = controller._extract_run_config({})
        assert result == {}

    def test_extracts_tty_and_stdin_open(self):
        controller = self._make_controller()
        attrs = {
            "Config": {"Tty": True, "OpenStdin": True},
            "HostConfig": {},
        }
        result = controller._extract_run_config(attrs)
        assert result["tty"] is True
        assert result["stdin_open"] is True

    def test_extracts_dns_extra_hosts_sysctls(self):
        controller = self._make_controller()
        attrs = {
            "Config": {},
            "HostConfig": {
                "Dns": ["8.8.8.8"],
                "DnsSearch": ["example.com"],
                "ExtraHosts": ["host1:1.2.3.4"],
                "Sysctls": {"net.core.somaxconn": "1024"},
            },
        }
        result = controller._extract_run_config(attrs)
        assert result["dns"] == ["8.8.8.8"]
        assert result["dns_search"] == ["example.com"]
        assert result["extra_hosts"] == ["host1:1.2.3.4"]
        assert result["sysctls"] == {"net.core.somaxconn": "1024"}


class TestPullAndRecreate:
    """Tests for ContainerController.pull_and_recreate."""

    @pytest.mark.asyncio
    async def test_success_flow_pull_stop_remove_create_start(self):
        """Happy path: pull image, save config, stop, remove, run with new image."""
        from src.services.container_control import ContainerController

        mock_image = MagicMock()
        mock_image.tags = ["linuxserver/radarr:latest"]
        mock_image.id = "sha256:oldimage"

        mock_container = MagicMock()
        mock_container.image = mock_image
        mock_container.attrs = {
            "Config": {"Env": ["TZ=Europe/London"]},
            "HostConfig": {"Binds": ["/config:/config"]},
        }

        mock_client = MagicMock()
        mock_client.containers.get.return_value = mock_container

        controller = ContainerController(
            docker_client=mock_client,
            protected_containers=[],
        )

        result = await controller.pull_and_recreate("radarr")

        # Verify pull happened
        mock_client.images.pull.assert_called_once_with("linuxserver/radarr:latest")
        # Verify stop and remove happened
        mock_container.stop.assert_called_once()
        mock_container.remove.assert_called_once()
        # Verify new container was created
        mock_client.containers.run.assert_called_once()
        run_kwargs = mock_client.containers.run.call_args
        assert run_kwargs[0][0] == "linuxserver/radarr:latest"
        assert run_kwargs[1]["name"] == "radarr"
        assert run_kwargs[1]["detach"] is True
        assert run_kwargs[1]["environment"] == ["TZ=Europe/London"]
        assert run_kwargs[1]["volumes"] == ["/config:/config"]
        # Verify success message
        assert "updated" in result.lower()
        assert "radarr" in result

    @pytest.mark.asyncio
    async def test_failure_during_pull_does_not_stop_container(self):
        """If image pull fails, the running container must not be touched."""
        from src.services.container_control import ContainerController

        mock_image = MagicMock()
        mock_image.tags = ["linuxserver/radarr:latest"]
        mock_image.id = "sha256:oldimage"

        mock_container = MagicMock()
        mock_container.image = mock_image
        mock_container.attrs = {"Config": {}, "HostConfig": {}}

        mock_client = MagicMock()
        mock_client.containers.get.return_value = mock_container
        mock_client.images.pull.side_effect = Exception("Network error during pull")

        controller = ContainerController(
            docker_client=mock_client,
            protected_containers=[],
        )

        result = await controller.pull_and_recreate("radarr")

        # Pull was attempted
        mock_client.images.pull.assert_called_once()
        # Container must NOT have been stopped or removed
        mock_container.stop.assert_not_called()
        mock_container.remove.assert_not_called()
        # No new container created
        mock_client.containers.run.assert_not_called()
        # Error message returned
        assert "failed" in result.lower()

    @pytest.mark.asyncio
    async def test_not_found_returns_error(self):
        """pull_and_recreate with nonexistent container returns error message."""
        from src.services.container_control import ContainerController

        mock_client = MagicMock()
        mock_client.containers.get.side_effect = docker.errors.NotFound("nope")

        controller = ContainerController(
            docker_client=mock_client,
            protected_containers=[],
        )

        result = await controller.pull_and_recreate("nonexistent")
        assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_recreate_failure_triggers_rollback(self):
        """If recreation fails, should attempt rollback with old image."""
        from src.services.container_control import ContainerController

        mock_image = MagicMock()
        mock_image.tags = ["myapp:latest"]
        mock_image.id = "sha256:oldid"

        mock_container = MagicMock()
        mock_container.image = mock_image
        mock_container.attrs = {"Config": {}, "HostConfig": {}}

        mock_client = MagicMock()
        mock_client.containers.get.return_value = mock_container

        # First call to containers.run (new image) fails,
        # second call (rollback with old image) succeeds
        mock_client.containers.run.side_effect = [
            Exception("Port conflict"),
            MagicMock(),  # rollback succeeds
        ]

        controller = ContainerController(
            docker_client=mock_client,
            protected_containers=[],
        )

        result = await controller.pull_and_recreate("myapp")

        # Should have tried to create twice: once with new, once with old
        assert mock_client.containers.run.call_count == 2
        # Second call should use old image id
        second_call = mock_client.containers.run.call_args_list[1]
        assert second_call[0][0] == "sha256:oldid"
        # Result should mention rollback
        assert "rolled back" in result.lower()

    @pytest.mark.asyncio
    async def test_recreate_and_rollback_both_fail(self):
        """If both recreation and rollback fail, return critical error."""
        from src.services.container_control import ContainerController

        mock_image = MagicMock()
        mock_image.tags = ["myapp:latest"]
        mock_image.id = "sha256:oldid"

        mock_container = MagicMock()
        mock_container.image = mock_image
        mock_container.attrs = {"Config": {}, "HostConfig": {}}

        mock_client = MagicMock()
        mock_client.containers.get.return_value = mock_container

        mock_client.containers.run.side_effect = [
            Exception("Port conflict"),
            Exception("Rollback also failed"),
        ]

        controller = ContainerController(
            docker_client=mock_client,
            protected_containers=[],
        )

        result = await controller.pull_and_recreate("myapp")

        assert "critical" in result.lower()
        assert "manual intervention" in result.lower()

    @pytest.mark.asyncio
    async def test_uses_image_id_when_no_tags(self):
        """When image has no tags, use image.id as the image name."""
        from src.services.container_control import ContainerController

        mock_image = MagicMock()
        mock_image.tags = []
        mock_image.id = "sha256:abc123"

        mock_container = MagicMock()
        mock_container.image = mock_image
        mock_container.attrs = {"Config": {}, "HostConfig": {}}

        mock_client = MagicMock()
        mock_client.containers.get.return_value = mock_container

        controller = ContainerController(
            docker_client=mock_client,
            protected_containers=[],
        )

        result = await controller.pull_and_recreate("myapp")

        mock_client.images.pull.assert_called_once_with("sha256:abc123")


# ---------------------------------------------------------------------------
# P2-3: AlertManagerProxy
# ---------------------------------------------------------------------------


class TestAlertManagerProxy:
    """Tests for AlertManagerProxy from main.py."""

    def _make_proxy(self, chat_id=None):
        from src.alert_proxy import AlertManagerProxy
        from src.alerts.manager import ChatIdStore

        mock_bot = MagicMock()
        store = ChatIdStore()
        if chat_id is not None:
            store.set_chat_id(chat_id)

        proxy = AlertManagerProxy(mock_bot, store, error_display_max_chars=200)
        return proxy, mock_bot, store

    @pytest.mark.asyncio
    async def test_alert_sent_when_chat_id_available(self):
        """When chat_id is set, alert is sent immediately via AlertManager."""
        proxy, mock_bot, store = self._make_proxy(chat_id=12345)

        with patch("src.alert_proxy.AlertManager") as MockAlertManager:
            mock_mgr_instance = MagicMock()
            mock_mgr_instance.send_crash_alert = AsyncMock()
            MockAlertManager.return_value = mock_mgr_instance

            await proxy.send_crash_alert(
                container_name="radarr",
                exit_code=137,
                image="linuxserver/radarr:latest",
            )

            MockAlertManager.assert_called_with(
                mock_bot, 12345, error_display_max_chars=200
            )
            mock_mgr_instance.send_crash_alert.assert_called_once_with(
                container_name="radarr",
                exit_code=137,
                image="linuxserver/radarr:latest",
                uptime_seconds=None,
                restart_loop_count=None,
            )

    @pytest.mark.asyncio
    async def test_alert_queued_when_no_chat_id(self):
        """When no chat_id, alerts are queued instead of sent."""
        proxy, mock_bot, store = self._make_proxy(chat_id=None)

        with patch("src.alert_proxy.AlertManager") as MockAlertManager:
            await proxy.send_crash_alert(
                container_name="radarr",
                exit_code=1,
                image="test:latest",
            )

            # AlertManager should NOT have been instantiated
            MockAlertManager.assert_not_called()
            # Alert should be in the queue
            assert len(proxy._queued_alerts) == 1
            method, kwargs = proxy._queued_alerts[0]
            assert method == "send_crash_alert"
            assert kwargs["container_name"] == "radarr"

    @pytest.mark.asyncio
    async def test_queued_alerts_flushed_when_chat_id_becomes_available(self):
        """Queued alerts are delivered when the first real alert finds a chat_id."""
        proxy, mock_bot, store = self._make_proxy(chat_id=None)

        # Queue some alerts while no chat_id
        with patch("src.alert_proxy.AlertManager"):
            await proxy.send_crash_alert(
                container_name="radarr", exit_code=1, image="img1"
            )
            await proxy.send_log_error_alert(
                container_name="sonarr", error_line="connection failed"
            )

        assert len(proxy._queued_alerts) == 2

        # Now set the chat_id
        store.set_chat_id(99999)

        # Send another alert -- should flush the queue first
        with patch("src.alert_proxy.AlertManager") as MockAlertManager:
            mock_mgr = MagicMock()
            mock_mgr.send_crash_alert = AsyncMock()
            mock_mgr.send_log_error_alert = AsyncMock()
            mock_mgr.send_resource_alert = AsyncMock()
            MockAlertManager.return_value = mock_mgr

            await proxy.send_resource_alert(
                container_name="plex",
                metric="cpu",
                current_value=95.0,
                threshold=80,
                duration_seconds=300,
                memory_bytes=1_000_000,
                memory_limit=2_000_000,
                memory_percent=50.0,
                cpu_percent=95.0,
            )

            # Queue should be empty now
            assert len(proxy._queued_alerts) == 0

            # The queued alerts should have been delivered
            mock_mgr.send_crash_alert.assert_called_once_with(
                container_name="radarr", exit_code=1, image="img1",
                uptime_seconds=None, restart_loop_count=None,
            )
            mock_mgr.send_log_error_alert.assert_called_once_with(
                container_name="sonarr", error_line="connection failed",
                suppressed_count=0,
            )
            # Plus the new alert
            mock_mgr.send_resource_alert.assert_called_once()

    @pytest.mark.asyncio
    async def test_queue_limit_drops_excess_alerts(self):
        """Queue is capped at MAX_QUEUED (50). Excess alerts are dropped."""
        proxy, mock_bot, store = self._make_proxy(chat_id=None)

        with patch("src.alert_proxy.AlertManager"):
            # Fill the queue to MAX_QUEUED
            for i in range(proxy.MAX_QUEUED):
                await proxy.send_crash_alert(
                    container_name=f"container_{i}",
                    exit_code=1,
                    image="img",
                )

            assert len(proxy._queued_alerts) == proxy.MAX_QUEUED

            # One more should be dropped
            await proxy.send_crash_alert(
                container_name="dropped",
                exit_code=1,
                image="img",
            )

            # Still at MAX_QUEUED, not MAX_QUEUED + 1
            assert len(proxy._queued_alerts) == proxy.MAX_QUEUED

            # The dropped alert should not be in the queue
            names = [kwargs["container_name"] for _, kwargs in proxy._queued_alerts]
            assert "dropped" not in names

    @pytest.mark.asyncio
    async def test_max_queued_is_50(self):
        """Verify the MAX_QUEUED constant is 50."""
        from src.alert_proxy import AlertManagerProxy

        assert AlertManagerProxy.MAX_QUEUED == 50

    @pytest.mark.asyncio
    async def test_different_alert_types_are_queued(self):
        """All alert types (crash, log_error, resource) can be queued."""
        proxy, mock_bot, store = self._make_proxy(chat_id=None)

        with patch("src.alert_proxy.AlertManager"):
            await proxy.send_crash_alert(
                container_name="a", exit_code=1, image="img"
            )
            await proxy.send_log_error_alert(
                container_name="b", error_line="err"
            )
            await proxy.send_resource_alert(
                container_name="c",
                metric="cpu",
                current_value=90.0,
                threshold=80,
                duration_seconds=60,
                memory_bytes=1000,
                memory_limit=2000,
                memory_percent=50.0,
                cpu_percent=90.0,
            )

        assert len(proxy._queued_alerts) == 3
        methods = [m for m, _ in proxy._queued_alerts]
        assert methods == [
            "send_crash_alert",
            "send_log_error_alert",
            "send_resource_alert",
        ]


# ---------------------------------------------------------------------------
# P2-9: AlertManager exit code interpretation and error line truncation
# ---------------------------------------------------------------------------


class TestAlertManagerExitCodes:
    """Tests for exit code interpretation in send_crash_alert."""

    @pytest.mark.asyncio
    async def test_exit_code_137_oom_killed(self):
        """Exit code 137 should show (OOM killed) in the alert text."""
        from src.alerts.manager import AlertManager

        bot = MagicMock()
        bot.send_message = AsyncMock()
        mgr = AlertManager(bot=bot, chat_id=123)

        await mgr.send_crash_alert(
            container_name="app",
            exit_code=137,
            image="app:latest",
        )

        text = bot.send_message.call_args[1]["text"]
        assert "137" in text
        assert "(OOM killed)" in text

    @pytest.mark.asyncio
    async def test_exit_code_143_sigterm(self):
        """Exit code 143 should show (SIGTERM) in the alert text."""
        from src.alerts.manager import AlertManager

        bot = MagicMock()
        bot.send_message = AsyncMock()
        mgr = AlertManager(bot=bot, chat_id=123)

        await mgr.send_crash_alert(
            container_name="app",
            exit_code=143,
            image="app:latest",
        )

        text = bot.send_message.call_args[1]["text"]
        assert "143" in text
        assert "(SIGTERM)" in text

    @pytest.mark.asyncio
    async def test_exit_code_139_segfault(self):
        """Exit code 139 should show (segfault) in the alert text."""
        from src.alerts.manager import AlertManager

        bot = MagicMock()
        bot.send_message = AsyncMock()
        mgr = AlertManager(bot=bot, chat_id=123)

        await mgr.send_crash_alert(
            container_name="app",
            exit_code=139,
            image="app:latest",
        )

        text = bot.send_message.call_args[1]["text"]
        assert "139" in text
        assert "(segfault)" in text

    @pytest.mark.asyncio
    async def test_exit_code_1_no_special_reason(self):
        """Exit code 1 (generic failure) should have no parenthesized reason."""
        from src.alerts.manager import AlertManager

        bot = MagicMock()
        bot.send_message = AsyncMock()
        mgr = AlertManager(bot=bot, chat_id=123)

        await mgr.send_crash_alert(
            container_name="app",
            exit_code=1,
            image="app:latest",
        )

        text = bot.send_message.call_args[1]["text"]
        assert "1" in text
        assert "(OOM" not in text
        assert "(SIGTERM)" not in text
        assert "(segfault)" not in text

    @pytest.mark.asyncio
    async def test_exit_code_0_no_special_reason(self):
        """Exit code 0 should have no special reason annotation."""
        from src.alerts.manager import AlertManager

        bot = MagicMock()
        bot.send_message = AsyncMock()
        mgr = AlertManager(bot=bot, chat_id=123)

        await mgr.send_crash_alert(
            container_name="app",
            exit_code=0,
            image="app:latest",
        )

        text = bot.send_message.call_args[1]["text"]
        assert "(OOM" not in text
        assert "(SIGTERM)" not in text
        assert "(segfault)" not in text


class TestAlertManagerErrorTruncation:
    """Tests for error line truncation in send_log_error_alert."""

    @pytest.mark.asyncio
    async def test_short_error_not_truncated(self):
        """Error lines shorter than error_display_max_chars are shown in full."""
        from src.alerts.manager import AlertManager

        bot = MagicMock()
        bot.send_message = AsyncMock()
        mgr = AlertManager(bot=bot, chat_id=123, error_display_max_chars=200)

        short_error = "Connection refused"
        await mgr.send_log_error_alert(
            container_name="app",
            error_line=short_error,
        )

        text = bot.send_message.call_args[1]["text"]
        assert short_error in text
        assert "..." not in text

    @pytest.mark.asyncio
    async def test_long_error_truncated_at_max_chars(self):
        """Error lines longer than error_display_max_chars are truncated with '...'."""
        from src.alerts.manager import AlertManager

        bot = MagicMock()
        bot.send_message = AsyncMock()
        max_chars = 50
        mgr = AlertManager(bot=bot, chat_id=123, error_display_max_chars=max_chars)

        long_error = "A" * 200
        await mgr.send_log_error_alert(
            container_name="app",
            error_line=long_error,
        )

        text = bot.send_message.call_args[1]["text"]
        # The displayed error should be truncated to max_chars + "..."
        expected_truncated = "A" * max_chars + "..."
        assert expected_truncated in text
        # The full 200-char string should NOT appear
        assert long_error not in text

    @pytest.mark.asyncio
    async def test_error_exactly_at_limit_not_truncated(self):
        """Error line exactly at limit should NOT be truncated."""
        from src.alerts.manager import AlertManager

        bot = MagicMock()
        bot.send_message = AsyncMock()
        max_chars = 100
        mgr = AlertManager(bot=bot, chat_id=123, error_display_max_chars=max_chars)

        exact_error = "B" * max_chars
        await mgr.send_log_error_alert(
            container_name="app",
            error_line=exact_error,
        )

        text = bot.send_message.call_args[1]["text"]
        assert exact_error in text
        # Should not have trailing "..." since it is exactly at the limit
        assert exact_error + "..." not in text

    @pytest.mark.asyncio
    async def test_truncation_with_default_max_chars(self):
        """Default error_display_max_chars is 200."""
        from src.alerts.manager import AlertManager

        bot = MagicMock()
        bot.send_message = AsyncMock()
        mgr = AlertManager(bot=bot, chat_id=123)

        assert mgr.error_display_max_chars == 200

        long_error = "X" * 300
        await mgr.send_log_error_alert(
            container_name="app",
            error_line=long_error,
        )

        text = bot.send_message.call_args[1]["text"]
        # Should contain truncated version (200 chars + "...")
        assert ("X" * 200 + "...") in text
        # Full 300-char string should not appear
        assert long_error not in text
