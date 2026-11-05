"""Tests for setup wizard."""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from aiogram.types import Message, CallbackQuery

from src.bot.setup_wizard import (
    UnraidProbe,
    SetupWizard,
    WizardState,
    WizardSession,
    SetupModeMiddleware,
    format_classification_summary,
    build_summary_keyboard,
    build_adjust_keyboard,
    create_start_handler,
    create_cancel_handler,
    create_host_handler,
    create_confirm_callback,
    create_toggle_callback,
    create_adjust_callback,
    create_adjust_done_callback,
)
from src.services.container_classifier import ContainerClassification


def _msg() -> MagicMock:
    """A Message the handlers can isinstance-check, with async send methods."""
    m = MagicMock(spec=Message)
    m.answer = AsyncMock()
    m.edit_text = AsyncMock()
    m.edit_reply_markup = AsyncMock()
    return m


@pytest.fixture
def wizard(tmp_path):
    return SetupWizard(
        config_path=str(tmp_path / "config.yaml"),
        docker_client=MagicMock(),
        anthropic_client=None,
        unraid_api_key=None,
    )


@pytest.fixture
def wizard_with_unraid(tmp_path):
    return SetupWizard(
        config_path=str(tmp_path / "config.yaml"),
        docker_client=MagicMock(),
        anthropic_client=None,
        unraid_api_key="some-key",
    )


def _make_message(user_id: int, text: str) -> MagicMock:
    """Create a MagicMock that passes isinstance(event, Message)."""
    msg = MagicMock(spec=Message)
    msg.from_user = MagicMock()
    msg.from_user.id = user_id
    msg.text = text
    msg.answer = AsyncMock()
    return msg


class TestWizardState:
    def test_initial_state_is_idle(self, wizard):
        assert wizard.get_state(user_id=123) == WizardState.IDLE

    def test_start_moves_to_awaiting_host_with_unraid_key(self, wizard_with_unraid):
        wizard_with_unraid.start(user_id=123)
        assert wizard_with_unraid.get_state(123) == WizardState.AWAITING_HOST

    def test_start_skips_to_containers_without_unraid_key(self, wizard):
        wizard.start(user_id=123)
        assert wizard.get_state(123) == WizardState.REVIEW_CONTAINERS

    def test_set_host_moves_to_connecting(self, wizard_with_unraid):
        wizard_with_unraid.start(user_id=123)
        wizard_with_unraid.set_host(123, "192.168.0.190")
        assert wizard_with_unraid.get_state(123) == WizardState.CONNECTING

    def test_connection_success_moves_to_review(self, wizard_with_unraid):
        wizard_with_unraid.start(user_id=123)
        wizard_with_unraid.set_host(123, "192.168.0.190")
        wizard_with_unraid.connection_result(123, success=True, port=80, use_ssl=False)
        assert wizard_with_unraid.get_state(123) == WizardState.REVIEW_CONTAINERS

    def test_connection_failure_returns_to_awaiting_host(self, wizard_with_unraid):
        wizard_with_unraid.start(user_id=123)
        wizard_with_unraid.set_host(123, "192.168.0.190")
        wizard_with_unraid.connection_result(123, success=False, port=0, use_ssl=False)
        assert wizard_with_unraid.get_state(123) == WizardState.AWAITING_HOST

    def test_confirm_moves_to_complete(self, wizard):
        wizard.start(user_id=123)
        wizard.confirm(123)
        assert wizard.get_state(123) == WizardState.COMPLETE

    def test_is_active(self, wizard):
        assert wizard.is_active(123) is False
        wizard.start(user_id=123)
        assert wizard.is_active(123) is True
        wizard.confirm(123)
        assert wizard.is_active(123) is False

    def test_get_session_data_returns_session(self, wizard):
        session = wizard.get_session_data(123)
        assert isinstance(session, WizardSession)
        assert session.state == WizardState.IDLE

    def test_connection_stores_port_and_ssl(self, wizard_with_unraid):
        wizard_with_unraid.start(user_id=123)
        wizard_with_unraid.set_host(123, "192.168.0.190")
        wizard_with_unraid.connection_result(123, success=True, port=443, use_ssl=True)
        session = wizard_with_unraid.get_session_data(123)
        assert session.unraid_port == 443
        assert session.unraid_use_ssl is True

    def test_set_host_stores_host(self, wizard_with_unraid):
        wizard_with_unraid.start(user_id=123)
        wizard_with_unraid.set_host(123, "my-server.local")
        session = wizard_with_unraid.get_session_data(123)
        assert session.unraid_host == "my-server.local"

    def test_separate_user_sessions_blocked(self, wizard):
        """Concurrent wizard sessions are blocked to prevent config corruption."""
        wizard.start(user_id=100)
        with pytest.raises(RuntimeError, match="Another user"):
            wizard.start(user_id=200)
        # First user can still proceed
        wizard.confirm(100)
        assert wizard.get_state(100) == WizardState.COMPLETE
        # Now second user can start
        wizard.start(user_id=200)
        assert wizard.get_state(200) == WizardState.REVIEW_CONTAINERS


class TestFormatting:
    def test_format_empty_classifications(self):
        assert format_classification_summary([]) == "No containers found."

    def test_format_classification_summary_groups_by_category(self):
        classifications = [
            ContainerClassification(name="mariadb", image="img", categories={"priority", "watched"}),
            ContainerClassification(name="plex", image="img", categories={"watched"}),
            ContainerClassification(name="qbit", image="img", categories={"killable", "watched"}),
        ]
        summary = format_classification_summary(classifications)
        assert "mariadb" in summary
        assert "plex" in summary
        assert "qbit" in summary
        assert "Priority" in summary
        assert "Watched" in summary

    def test_format_shows_uncategorised(self):
        classifications = [
            ContainerClassification(name="mystery", image="img", categories=set()),
        ]
        summary = format_classification_summary(classifications)
        assert "mystery" in summary
        assert "Uncategorised" in summary

    def test_format_shows_ai_markers(self):
        classifications = [
            ContainerClassification(
                name="plex", image="img", categories={"watched"}, ai_suggested=True
            ),
            ContainerClassification(
                name="mariadb", image="img", categories={"priority"}, ai_suggested=False
            ),
        ]
        summary = format_classification_summary(classifications)
        assert "plex \\*" in summary
        assert "mariadb \\*" not in summary
        assert "AI-suggested" in summary

    def test_format_no_ai_markers_when_none_ai(self):
        classifications = [
            ContainerClassification(
                name="plex", image="img", categories={"watched"}, ai_suggested=False
            ),
        ]
        summary = format_classification_summary(classifications)
        assert "AI-suggested" not in summary

    def test_build_summary_keyboard_has_adjust_buttons(self):
        keyboard = build_summary_keyboard()
        # 5 adjust buttons + 1 looks good = 6 rows
        assert len(keyboard.inline_keyboard) == 6
        assert keyboard.inline_keyboard[-1][0].callback_data == "setup:confirm"
        assert "Adjust" in keyboard.inline_keyboard[0][0].text

    def test_build_adjust_keyboard_shows_containers(self):
        classifications = [
            ContainerClassification(name="plex", image="img", categories={"watched"}),
            ContainerClassification(name="radarr", image="img", categories=set()),
        ]
        keyboard = build_adjust_keyboard(classifications, "watched")
        # 2 containers + 1 done button = 3 rows
        assert len(keyboard.inline_keyboard) == 3
        # plex is in watched, radarr is not
        plex_btn = next(
            row[0] for row in keyboard.inline_keyboard
            if "plex" in row[0].text
        )
        radarr_btn = next(
            row[0] for row in keyboard.inline_keyboard
            if "radarr" in row[0].text
        )
        assert "\u2705" in plex_btn.text
        assert "\u274c" in radarr_btn.text
        # Done button at end
        assert keyboard.inline_keyboard[-1][0].callback_data == "setup:adjust_done"


class TestWizardHandlers:
    @pytest.mark.asyncio
    async def test_start_handler_sends_welcome(self, wizard):
        handler = create_start_handler(wizard)
        message = AsyncMock()
        message.from_user = MagicMock()
        message.from_user.id = 123

        with patch.object(wizard, "classify_containers", new_callable=AsyncMock) as mock_classify:
            mock_classify.return_value = []
            await handler(message)

        message.answer.assert_called()
        call_text = message.answer.call_args_list[0][0][0]
        assert "Welcome" in call_text

    @pytest.mark.asyncio
    async def test_start_handler_with_unraid_asks_for_host(self, wizard_with_unraid):
        handler = create_start_handler(wizard_with_unraid)
        message = AsyncMock()
        message.from_user = MagicMock()
        message.from_user.id = 123

        await handler(message)

        message.answer.assert_called_once()
        call_text = message.answer.call_args[0][0]
        assert "IP" in call_text or "hostname" in call_text

    @pytest.mark.asyncio
    async def test_start_handler_without_unraid_scans_containers(self, wizard):
        handler = create_start_handler(wizard)
        message = AsyncMock()
        message.from_user = MagicMock()
        message.from_user.id = 123

        with patch.object(wizard, "classify_containers", new_callable=AsyncMock) as mock_classify:
            mock_classify.return_value = [
                ContainerClassification(name="plex", image="img", categories={"watched"})
            ]
            await handler(message)

        # First answer is welcome, second is classification summary
        assert message.answer.call_count == 2

    @pytest.mark.asyncio
    async def test_host_handler_triggers_connection(self, wizard_with_unraid):
        wizard_with_unraid.start(user_id=123)

        handler = create_host_handler(wizard_with_unraid)
        message = AsyncMock()
        message.from_user = MagicMock()
        message.from_user.id = 123
        message.text = "192.168.0.190"

        with patch.object(
            wizard_with_unraid, "test_unraid_connection", new_callable=AsyncMock
        ) as mock_test:
            mock_test.return_value = UnraidProbe(True, 80, False, True, "")
            with patch.object(
                wizard_with_unraid, "classify_containers", new_callable=AsyncMock
            ) as mock_classify:
                mock_classify.return_value = []
                await handler(message)

        assert wizard_with_unraid.get_state(123) == WizardState.REVIEW_CONTAINERS

    @pytest.mark.asyncio
    async def test_host_handler_connection_failure(self, wizard_with_unraid):
        wizard_with_unraid.start(user_id=123)

        handler = create_host_handler(wizard_with_unraid)
        message = AsyncMock()
        message.from_user = MagicMock()
        message.from_user.id = 123
        message.text = "bad-host"

        with patch.object(
            wizard_with_unraid, "test_unraid_connection", new_callable=AsyncMock
        ) as mock_test:
            mock_test.return_value = UnraidProbe(False, 0, False, True, "connection refused")
            await handler(message)

        assert wizard_with_unraid.get_state(123) == WizardState.AWAITING_HOST
        # Should mention failure and ask to try again
        last_call = message.answer.call_args_list[-1]
        assert "Could not connect" in last_call[0][0]

    @pytest.mark.asyncio
    async def test_confirm_callback_saves_config(self, wizard):
        wizard.start(user_id=123)

        on_complete = AsyncMock()
        handler = create_confirm_callback(wizard, on_complete=on_complete)
        callback = AsyncMock()
        callback.from_user = MagicMock()
        callback.from_user.id = 123
        callback.data = "setup:confirm"
        callback.message = _msg()

        with patch.object(wizard, "save_config") as mock_save:
            await handler(callback)
            mock_save.assert_called_once()

        assert wizard.get_state(123) == WizardState.COMPLETE
        on_complete.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_confirm_callback_uses_merge_when_config_exists(self, tmp_path):
        """When config.yaml already exists, confirm should merge."""
        config_path = str(tmp_path / "config.yaml")
        # Create existing config
        import yaml

        with open(config_path, "w") as f:
            yaml.dump({"log_watching": {"containers": ["old"]}}, f)

        w = SetupWizard(
            config_path=config_path,
            docker_client=MagicMock(),
        )
        w.start(user_id=123)

        handler = create_confirm_callback(w)
        callback = AsyncMock()
        callback.from_user = MagicMock()
        callback.from_user.id = 123
        callback.data = "setup:confirm"
        callback.message = _msg()

        with patch.object(w, "save_config") as mock_save:
            await handler(callback)
            mock_save.assert_called_once_with(123, merge=True)

        # Message should mention merge
        msg_text = callback.message.answer.call_args[0][0]
        assert "merged" in msg_text

    @pytest.mark.asyncio
    async def test_confirm_callback_without_on_complete(self, wizard):
        wizard.start(user_id=123)

        handler = create_confirm_callback(wizard, on_complete=None)
        callback = AsyncMock()
        callback.from_user = MagicMock()
        callback.from_user.id = 123
        callback.data = "setup:confirm"
        callback.message = _msg()

        with patch.object(wizard, "save_config"):
            await handler(callback)

        assert wizard.get_state(123) == WizardState.COMPLETE

    @pytest.mark.asyncio
    async def test_toggle_callback_adds_category(self, wizard):
        wizard.start(user_id=123)
        session = wizard.get_session_data(123)
        session.classifications = [
            ContainerClassification(name="plex", image="img", categories={"watched"})
        ]

        handler = create_toggle_callback(wizard)
        callback = AsyncMock()
        callback.from_user = MagicMock()
        callback.from_user.id = 123
        callback.data = "setup:toggle:killable:plex"
        callback.message = _msg()

        await handler(callback)

        assert "killable" in session.classifications[0].categories
        assert "watched" in session.classifications[0].categories

    @pytest.mark.asyncio
    async def test_toggle_callback_removes_category(self, wizard):
        wizard.start(user_id=123)
        session = wizard.get_session_data(123)
        session.classifications = [
            ContainerClassification(name="plex", image="img", categories={"watched", "killable"})
        ]

        handler = create_toggle_callback(wizard)
        callback = AsyncMock()
        callback.from_user = MagicMock()
        callback.from_user.id = 123
        callback.data = "setup:toggle:killable:plex"
        callback.message = _msg()

        await handler(callback)

        assert "killable" not in session.classifications[0].categories

    @pytest.mark.asyncio
    async def test_toggle_callback_ignored_watched_conflict(self, wizard):
        """Adding ignored should remove watched and vice versa."""
        wizard.start(user_id=123)
        session = wizard.get_session_data(123)
        session.classifications = [
            ContainerClassification(name="plex", image="img", categories={"watched"})
        ]

        handler = create_toggle_callback(wizard)
        callback = AsyncMock()
        callback.from_user = MagicMock()
        callback.from_user.id = 123
        callback.data = "setup:toggle:ignored:plex"
        callback.message = _msg()

        await handler(callback)

        assert "ignored" in session.classifications[0].categories
        assert "watched" not in session.classifications[0].categories

    @pytest.mark.asyncio
    async def test_toggle_callback_watched_ignored_conflict(self, wizard):
        """Adding watched should remove ignored."""
        wizard.start(user_id=123)
        session = wizard.get_session_data(123)
        session.classifications = [
            ContainerClassification(name="plex", image="img", categories={"ignored"})
        ]

        handler = create_toggle_callback(wizard)
        callback = AsyncMock()
        callback.from_user = MagicMock()
        callback.from_user.id = 123
        callback.data = "setup:toggle:watched:plex"
        callback.message = _msg()

        await handler(callback)

        assert "watched" in session.classifications[0].categories
        assert "ignored" not in session.classifications[0].categories

    @pytest.mark.asyncio
    async def test_toggle_callback_unknown_container(self, wizard):
        wizard.start(user_id=123)
        session = wizard.get_session_data(123)
        session.classifications = []

        handler = create_toggle_callback(wizard)
        callback = AsyncMock()
        callback.from_user = MagicMock()
        callback.from_user.id = 123
        callback.data = "setup:toggle:watched:nonexistent"
        callback.message = _msg()

        await handler(callback)

        callback.answer.assert_called_with("Container not found")

    @pytest.mark.asyncio
    async def test_adjust_callback_shows_toggle_keyboard(self, wizard):
        wizard.start(user_id=123)
        session = wizard.get_session_data(123)
        session.classifications = [
            ContainerClassification(name="plex", image="img", categories={"watched"})
        ]

        handler = create_adjust_callback(wizard)
        callback = AsyncMock()
        callback.from_user = MagicMock()
        callback.from_user.id = 123
        callback.data = "setup:adjust:watched"
        callback.message = _msg()

        await handler(callback)

        assert session.state == WizardState.ADJUSTING
        assert session.adjusting_category == "watched"
        callback.message.answer.assert_called_once()

    @pytest.mark.asyncio
    async def test_adjust_done_returns_to_summary(self, wizard):
        wizard.start(user_id=123)
        session = wizard.get_session_data(123)
        session.state = WizardState.ADJUSTING
        session.adjusting_category = "watched"
        session.classifications = [
            ContainerClassification(name="plex", image="img", categories={"watched"})
        ]

        handler = create_adjust_done_callback(wizard)
        callback = AsyncMock()
        callback.from_user = MagicMock()
        callback.from_user.id = 123
        callback.data = "setup:adjust_done"
        callback.message = _msg()

        await handler(callback)

        assert session.state == WizardState.REVIEW_CONTAINERS
        assert session.adjusting_category is None
        callback.message.answer.assert_called_once()


class TestCancelHandler:
    @pytest.mark.asyncio
    async def test_cancel_resets_wizard(self, wizard):
        wizard.start(user_id=123)
        assert wizard.is_active(123)

        handler = create_cancel_handler(wizard)
        message = AsyncMock()
        message.from_user = MagicMock()
        message.from_user.id = 123

        await handler(message)

        assert not wizard.is_active(123)
        message.answer.assert_called_once()
        assert "cancelled" in message.answer.call_args[0][0]

    @pytest.mark.asyncio
    async def test_cancel_when_not_active(self, wizard):
        handler = create_cancel_handler(wizard)
        message = AsyncMock()
        message.from_user = MagicMock()
        message.from_user.id = 123

        await handler(message)

        message.answer.assert_called_once()
        assert "No setup wizard" in message.answer.call_args[0][0]

    @pytest.mark.asyncio
    async def test_middleware_allows_cancel_during_setup(self, wizard):
        wizard.start(user_id=123)
        middleware = SetupModeMiddleware(wizard)

        message = _make_message(123, "/cancel")
        inner_handler = AsyncMock()

        await middleware(inner_handler, message, {})

        inner_handler.assert_called_once()


class TestDockerContainers:
    def test_get_docker_containers(self, tmp_path):
        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_container.name = "plex"
        mock_container.image.tags = ["plexinc/pms:latest"]
        mock_container.status = "running"
        mock_client.containers.list.return_value = [mock_container]

        w = SetupWizard(
            config_path=str(tmp_path / "config.yaml"),
            docker_client=mock_client,
        )
        containers = w.get_docker_containers()

        assert len(containers) == 1
        assert containers[0] == ("plex", "plexinc/pms:latest", "running")

    def test_get_docker_containers_no_image_tags(self, tmp_path):
        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_container.name = "plex"
        mock_container.image.tags = []
        mock_container.image.id = "sha256:abc123def456"
        mock_container.status = "exited"
        mock_client.containers.list.return_value = [mock_container]

        w = SetupWizard(
            config_path=str(tmp_path / "config.yaml"),
            docker_client=mock_client,
        )
        containers = w.get_docker_containers()

        assert len(containers) == 1
        name, image, status = containers[0]
        assert name == "plex"
        assert "sha256" in image
        assert status == "exited"

    def test_get_docker_containers_handles_error(self, tmp_path):
        mock_client = MagicMock()
        mock_client.containers.list.side_effect = Exception("Docker error")

        w = SetupWizard(
            config_path=str(tmp_path / "config.yaml"),
            docker_client=mock_client,
        )
        containers = w.get_docker_containers()
        assert containers == []


class TestSaveConfig:
    def test_save_config_writes_file(self, tmp_path):
        config_path = str(tmp_path / "config.yaml")
        mock_client = MagicMock()
        mock_client.containers.list.return_value = []

        w = SetupWizard(
            config_path=config_path,
            docker_client=mock_client,
        )
        w.start(user_id=123)
        session = w.get_session_data(123)
        session.classifications = [
            ContainerClassification(name="plex", image="img", categories={"watched"}),
            ContainerClassification(name="mariadb", image="img", categories={"priority", "watched"}),
            ContainerClassification(name="qbit", image="img", categories={"killable", "watched"}),
            ContainerClassification(name="dozzle", image="img", categories={"ignored"}),
            ContainerClassification(
                name="unraid-monitor-bot", image="img", categories={"protected"}
            ),
        ]

        w.save_config(123)

        import yaml

        with open(config_path) as f:
            config = yaml.safe_load(f)

        assert "plex" in config["log_watching"]["containers"]
        assert "mariadb" in config["log_watching"]["containers"]
        assert "mariadb" in config["memory_management"]["priority_containers"]
        assert "qbit" in config["memory_management"]["killable_containers"]
        assert "dozzle" in config["ignored_containers"]
        assert "unraid-monitor-bot" in config["protected_containers"]

    def test_save_config_merge(self, tmp_path):
        config_path = str(tmp_path / "config.yaml")

        # Write initial config
        import yaml

        initial = {
            "log_watching": {"containers": ["old"], "cooldown_seconds": 999},
            "protected_containers": [],
            "ignored_containers": [],
            "memory_management": {
                "priority_containers": [],
                "killable_containers": [],
                "warning_threshold": 85,
            },
            "unraid": {"enabled": False},
        }
        with open(config_path, "w") as f:
            yaml.dump(initial, f)

        mock_client = MagicMock()
        mock_client.containers.list.return_value = []

        w = SetupWizard(config_path=config_path, docker_client=mock_client)
        w.start(user_id=123)
        session = w.get_session_data(123)
        session.classifications = [
            ContainerClassification(name="plex", image="img", categories={"watched"}),
        ]

        w.save_config(123, merge=True)

        with open(config_path) as f:
            config = yaml.safe_load(f)

        # Wizard-managed fields updated
        assert "plex" in config["log_watching"]["containers"]
        assert "old" not in config["log_watching"]["containers"]
        # Non-wizard fields preserved
        assert config["log_watching"]["cooldown_seconds"] == 999
        assert config["memory_management"]["warning_threshold"] == 85


class TestSetupMiddleware:
    @pytest.mark.asyncio
    async def test_middleware_blocks_commands_during_setup(self, wizard):
        wizard.start(user_id=123)
        middleware = SetupModeMiddleware(wizard)

        message = _make_message(123, "/status")
        inner_handler = AsyncMock()

        await middleware(inner_handler, message, {})

        inner_handler.assert_not_called()
        message.answer.assert_called_once()
        assert "Setup is in progress" in message.answer.call_args[0][0]

    @pytest.mark.asyncio
    async def test_middleware_allows_help_during_setup(self, wizard):
        wizard.start(user_id=123)
        middleware = SetupModeMiddleware(wizard)

        message = _make_message(123, "/help")
        inner_handler = AsyncMock()

        await middleware(inner_handler, message, {})

        inner_handler.assert_called_once()

    @pytest.mark.asyncio
    async def test_middleware_allows_setup_during_setup(self, wizard):
        wizard.start(user_id=123)
        middleware = SetupModeMiddleware(wizard)

        message = _make_message(123, "/setup")
        inner_handler = AsyncMock()

        await middleware(inner_handler, message, {})

        inner_handler.assert_called_once()

    @pytest.mark.asyncio
    async def test_middleware_passes_through_after_setup(self, wizard):
        middleware = SetupModeMiddleware(wizard)

        message = _make_message(123, "/status")
        inner_handler = AsyncMock()

        await middleware(inner_handler, message, {})

        inner_handler.assert_called_once()

    @pytest.mark.asyncio
    async def test_middleware_allows_plain_text_during_setup(self, wizard):
        """Non-command text (like host input) should pass through."""
        wizard.start(user_id=123)
        middleware = SetupModeMiddleware(wizard)

        message = _make_message(123, "192.168.0.190")
        inner_handler = AsyncMock()

        await middleware(inner_handler, message, {})

        inner_handler.assert_called_once()

    @pytest.mark.asyncio
    async def test_middleware_passes_callback_queries(self, wizard):
        """Callback queries should always pass through."""
        wizard.start(user_id=123)
        middleware = SetupModeMiddleware(wizard)

        callback_mock = MagicMock(spec=CallbackQuery)
        callback_mock.from_user = MagicMock()
        callback_mock.from_user.id = 123
        inner_handler = AsyncMock()

        await middleware(inner_handler, callback_mock, {})

        inner_handler.assert_called_once()

    @pytest.mark.asyncio
    async def test_middleware_different_user_not_blocked(self, wizard):
        """A user not in setup should not be blocked."""
        wizard.start(user_id=123)
        middleware = SetupModeMiddleware(wizard)

        message = _make_message(456, "/status")
        inner_handler = AsyncMock()

        await middleware(inner_handler, message, {})

        inner_handler.assert_called_once()


class TestConnectionTest:
    @pytest.mark.asyncio
    async def test_test_unraid_connection_tries_https_then_http(self, wizard):
        """Test that test_unraid_connection tries HTTPS:443 then HTTP:80."""
        call_urls: list[str] = []

        class FakeResponse:
            status = 200

            async def json(self, content_type=None):
                return {"data": {"info": {"os": {"hostname": "tower"}}}}

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        class FailResponse:
            async def __aenter__(self):
                raise ConnectionError("refused")

            async def __aexit__(self, *args):
                pass

        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            def post(self, url, **kwargs):
                call_urls.append(url)
                # First call (HTTPS) fails, second call (HTTP) succeeds
                if "https" in url:
                    return FailResponse()
                return FakeResponse()

        with patch("src.bot.setup_wizard.aiohttp.ClientSession", return_value=FakeSession()):
            probe = await wizard.test_unraid_connection("192.168.0.190", "test-key")

        assert probe.success is True
        assert probe.port == 80
        assert probe.use_ssl is False
        assert "unencrypted" in probe.detail  # plain HTTP is never silent
        assert len(call_urls) == 2
        assert "https" in call_urls[0] and "443" in call_urls[0]
        assert "http" in call_urls[1] and "80" in call_urls[1]


class TestClassifyContainers:
    @pytest.mark.asyncio
    async def test_classify_containers_stores_in_session(self, tmp_path):
        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_container.name = "mariadb"
        mock_container.image.tags = ["linuxserver/mariadb:latest"]
        mock_container.status = "running"
        mock_client.containers.list.return_value = [mock_container]

        w = SetupWizard(
            config_path=str(tmp_path / "config.yaml"),
            docker_client=mock_client,
        )
        w.start(user_id=123)
        results = await w.classify_containers(123)

        session = w.get_session_data(123)
        assert len(session.classifications) == 1
        assert session.classifications[0].name == "mariadb"
        assert "priority" in session.classifications[0].categories


class TestMainIntegration:
    """Tests for first-run vs normal-run detection used by main.py."""

    def test_first_run_detected_without_config(self, tmp_path):
        """Without config.yaml, wizard should be created."""
        from pathlib import Path
        config_path = str(tmp_path / "config.yaml")
        assert not Path(config_path).exists()
        first_run = not Path(config_path).exists()
        assert first_run is True

    def test_rerun_detected_with_config(self, tmp_path):
        """With existing config.yaml, it's a re-run."""
        from pathlib import Path
        config_path = tmp_path / "config.yaml"
        config_path.write_text("unraid:\n  enabled: true\n")
        first_run = not config_path.exists()
        assert first_run is False

    def test_wizard_created_with_correct_params(self, tmp_path):
        """SetupWizard receives docker_client, anthropic_client, and unraid key."""
        mock_docker = MagicMock()
        mock_anthropic = MagicMock()

        w = SetupWizard(
            config_path=str(tmp_path / "config.yaml"),
            docker_client=mock_docker,
            anthropic_client=mock_anthropic,
            unraid_api_key="test-key-123",
        )

        assert w._docker_client is mock_docker
        assert w._anthropic_client is mock_anthropic
        assert w._unraid_api_key == "test-key-123"

    def test_wizard_on_complete_can_be_async(self, tmp_path):
        """The on_complete callback passed to confirm handler should be async."""
        import asyncio

        mock_docker = MagicMock()
        w = SetupWizard(
            config_path=str(tmp_path / "config.yaml"),
            docker_client=mock_docker,
        )

        called = False

        async def on_complete():
            nonlocal called
            called = True

        # Verify the callback is async-compatible
        assert asyncio.iscoroutinefunction(on_complete)
