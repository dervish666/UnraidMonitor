import pytest
from unittest.mock import MagicMock, AsyncMock

from aiogram.types import Message


@pytest.mark.asyncio
async def test_ignore_command_shows_recent_errors():
    """Test /ignore shows recent errors with toggle buttons."""
    from src.bot.ignore_command import ignore_command, IgnoreSelectionState
    from src.alerts.recent_errors import RecentErrorsBuffer
    from src.alerts.ignore_manager import IgnoreManager

    buffer = RecentErrorsBuffer()
    buffer.add("plex", "Error 1")
    buffer.add("plex", "Error 2")

    manager = IgnoreManager({}, json_path="/tmp/test.json")
    selection_state = IgnoreSelectionState()

    handler = ignore_command(buffer, manager, selection_state)

    # Create mock message replying to an alert
    reply_message = MagicMock()
    reply_message.text = "\u26a0\ufe0f ERRORS IN: plex\n\nFound 2 errors"

    message = MagicMock()
    message.text = "/ignore"
    message.reply_to_message = reply_message
    message.answer = AsyncMock()
    message.from_user = MagicMock()
    message.from_user.id = 123

    await handler(message)

    message.answer.assert_called_once()
    call_args = message.answer.call_args
    response = call_args[0][0]

    assert "Recent errors in plex" in response
    assert "Error 1" in response
    assert "Error 2" in response

    # Verify keyboard is sent with toggle buttons
    keyboard = call_args.kwargs.get("reply_markup")
    assert keyboard is not None
    # First row should have toggle buttons
    assert keyboard.inline_keyboard[0][0].callback_data == "ign_toggle:0"
    assert keyboard.inline_keyboard[0][1].callback_data == "ign_toggle:1"
    # Should have Select All, Done, Cancel buttons
    all_callbacks = [
        btn.callback_data
        for row in keyboard.inline_keyboard
        for btn in row
    ]
    assert "ign_all" in all_callbacks
    assert "ign_done" in all_callbacks
    assert "ign_cancel" in all_callbacks


@pytest.mark.asyncio
async def test_ignore_command_no_reply():
    """Test /ignore without replying to message."""
    from src.bot.ignore_command import ignore_command, IgnoreSelectionState
    from src.alerts.recent_errors import RecentErrorsBuffer
    from src.alerts.ignore_manager import IgnoreManager

    buffer = RecentErrorsBuffer()
    manager = IgnoreManager({}, json_path="/tmp/test.json")
    selection_state = IgnoreSelectionState()

    handler = ignore_command(buffer, manager, selection_state)

    message = MagicMock()
    message.text = "/ignore"
    message.reply_to_message = None
    message.answer = AsyncMock()
    message.from_user = MagicMock()
    message.from_user.id = 123

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]
    assert "Reply to an error alert" in response


@pytest.mark.asyncio
async def test_ignore_command_not_error_alert():
    """Test /ignore when replying to non-error message."""
    from src.bot.ignore_command import ignore_command, IgnoreSelectionState
    from src.alerts.recent_errors import RecentErrorsBuffer
    from src.alerts.ignore_manager import IgnoreManager

    buffer = RecentErrorsBuffer()
    manager = IgnoreManager({}, json_path="/tmp/test.json")
    selection_state = IgnoreSelectionState()

    handler = ignore_command(buffer, manager, selection_state)

    reply_message = MagicMock()
    reply_message.text = "Hello there"

    message = MagicMock()
    message.text = "/ignore"
    message.reply_to_message = reply_message
    message.answer = AsyncMock()
    message.from_user = MagicMock()
    message.from_user.id = 123

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]
    assert "error alert" in response.lower()


@pytest.mark.asyncio
async def test_ignores_command_lists_all():
    """Test /ignores lists all ignores."""
    from src.bot.ignore_command import ignores_command
    from src.alerts.ignore_manager import IgnoreManager
    import json

    # Create manager with config and runtime ignores
    config_ignores = {"plex": ["config pattern"]}

    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({"plex": ["runtime pattern"], "radarr": ["another"]}, f)
        json_path = f.name

    manager = IgnoreManager(config_ignores, json_path=json_path)

    handler = ignores_command(manager)

    message = MagicMock()
    message.text = "/ignores"
    message.answer = AsyncMock()

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]

    assert "plex" in response
    assert "config pattern" in response
    assert "(config)" in response
    assert "runtime pattern" in response
    assert "radarr" in response


@pytest.mark.asyncio
async def test_ignores_command_empty():
    """Test /ignores with no ignores."""
    from src.bot.ignore_command import ignores_command
    from src.alerts.ignore_manager import IgnoreManager

    manager = IgnoreManager({}, json_path="/tmp/nonexistent.json")

    handler = ignores_command(manager)

    message = MagicMock()
    message.text = "/ignores"
    message.answer = AsyncMock()

    await handler(message)

    message.answer.assert_called_once()
    response = message.answer.call_args[0][0]
    assert "no ignored" in response.lower() or "No ignored" in response


def test_ignore_commands_in_help():
    """Test that /ignore and /ignores are in help section content."""
    from src.bot.commands import _HELP_SECTIONS

    alerts_content = _HELP_SECTIONS["alerts"][2]
    assert "/ignore" in alerts_content
    assert "/ignores" in alerts_content


class TestIgnoreSelectionState:
    """Tests for IgnoreSelectionState toggle and select-all methods."""

    def test_toggle_selection(self):
        from src.bot.ignore_command import IgnoreSelectionState

        state = IgnoreSelectionState()
        state.set_pending(123, "plex", ["Error 1", "Error 2", "Error 3"])

        # Initially empty
        assert state.get_selected(123) == set()

        # Toggle on
        state.toggle_selection(123, 0)
        assert state.get_selected(123) == {0}

        # Toggle another on
        state.toggle_selection(123, 2)
        assert state.get_selected(123) == {0, 2}

        # Toggle first off
        state.toggle_selection(123, 0)
        assert state.get_selected(123) == {2}

    def test_select_all(self):
        from src.bot.ignore_command import IgnoreSelectionState

        state = IgnoreSelectionState()
        state.set_pending(123, "plex", ["Error 1", "Error 2", "Error 3"])

        # Select all
        state.select_all(123)
        assert state.get_selected(123) == {0, 1, 2}

        # Deselect all (toggle)
        state.select_all(123)
        assert state.get_selected(123) == set()

    def test_select_all_partial(self):
        from src.bot.ignore_command import IgnoreSelectionState

        state = IgnoreSelectionState()
        state.set_pending(123, "plex", ["Error 1", "Error 2"])

        # Partially select
        state.toggle_selection(123, 0)
        assert state.get_selected(123) == {0}

        # Select all (not all selected, so should select all)
        state.select_all(123)
        assert state.get_selected(123) == {0, 1}

    def test_get_selected_no_pending(self):
        from src.bot.ignore_command import IgnoreSelectionState

        state = IgnoreSelectionState()
        assert state.get_selected(999) == set()

    def test_toggle_no_pending(self):
        """Toggle on non-existent user is a no-op."""
        from src.bot.ignore_command import IgnoreSelectionState

        state = IgnoreSelectionState()
        state.toggle_selection(999, 0)  # Should not raise


class TestIgnoreToggleCallback:
    """Tests for ignore_toggle_callback."""

    @pytest.mark.asyncio
    async def test_toggle_updates_keyboard(self):
        from src.bot.ignore_command import ignore_toggle_callback, IgnoreSelectionState

        state = IgnoreSelectionState()
        state.set_pending(123, "plex", ["Error 1", "Error 2"])

        handler = ignore_toggle_callback(state)

        callback = AsyncMock()
        callback.data = "ign_toggle:0"
        callback.from_user = MagicMock()
        callback.from_user.id = 123
        callback.message = MagicMock(spec=Message)
        callback.message.edit_reply_markup = AsyncMock()

        await handler(callback)

        callback.answer.assert_called_once()
        callback.message.edit_reply_markup.assert_called_once()
        keyboard = callback.message.edit_reply_markup.call_args.kwargs["reply_markup"]
        # First button should be checked now
        assert "\u2611" in keyboard.inline_keyboard[0][0].text  # ☑

    @pytest.mark.asyncio
    async def test_toggle_expired(self):
        from src.bot.ignore_command import ignore_toggle_callback, IgnoreSelectionState

        state = IgnoreSelectionState()
        # No pending selection

        handler = ignore_toggle_callback(state)

        callback = AsyncMock()
        callback.data = "ign_toggle:0"
        callback.from_user = MagicMock()
        callback.from_user.id = 123

        await handler(callback)

        callback.answer.assert_called_with("Selection expired. Use /ignore again.")


class TestIgnoreAllCallback:
    """Tests for ignore_all_callback."""

    @pytest.mark.asyncio
    async def test_select_all(self):
        from src.bot.ignore_command import ignore_all_callback, IgnoreSelectionState

        state = IgnoreSelectionState()
        state.set_pending(123, "plex", ["Error 1", "Error 2"])

        handler = ignore_all_callback(state)

        callback = AsyncMock()
        callback.data = "ign_all"
        callback.from_user = MagicMock()
        callback.from_user.id = 123
        callback.message = MagicMock(spec=Message)
        callback.message.edit_reply_markup = AsyncMock()

        await handler(callback)

        assert state.get_selected(123) == {0, 1}
        callback.message.edit_reply_markup.assert_called_once()


class TestIgnoreDoneCallback:
    """Tests for ignore_done_callback."""

    @pytest.mark.asyncio
    async def test_done_saves_selected(self, tmp_path):
        from src.bot.ignore_command import ignore_done_callback, IgnoreSelectionState
        from src.alerts.ignore_manager import IgnoreManager

        manager = IgnoreManager({}, json_path=str(tmp_path / "ignores.json"))
        state = IgnoreSelectionState()
        state.set_pending(123, "plex", ["Error message 1", "Error message 2"])
        state.toggle_selection(123, 0)  # Select first

        handler = ignore_done_callback(manager, state)

        callback = AsyncMock()
        callback.data = "ign_done"
        callback.from_user = MagicMock()
        callback.from_user.id = 123
        callback.message = AsyncMock()
        callback.message.edit_text = AsyncMock()

        await handler(callback)

        callback.answer.assert_called_once()
        # Verify ignore was saved
        assert manager.is_ignored("plex", "Error message 1 happened")
        # Verify second was NOT saved
        assert not manager.is_ignored("plex", "Error message 2 happened")

    @pytest.mark.asyncio
    async def test_done_no_selection(self, tmp_path):
        from src.bot.ignore_command import ignore_done_callback, IgnoreSelectionState
        from src.alerts.ignore_manager import IgnoreManager

        manager = IgnoreManager({}, json_path=str(tmp_path / "ignores.json"))
        state = IgnoreSelectionState()
        state.set_pending(123, "plex", ["Error message 1"])
        # Don't toggle anything

        handler = ignore_done_callback(manager, state)

        callback = AsyncMock()
        callback.data = "ign_done"
        callback.from_user = MagicMock()
        callback.from_user.id = 123

        await handler(callback)

        callback.answer.assert_called_with("No errors selected. Toggle some first.")

    @pytest.mark.asyncio
    async def test_done_with_analyzer(self, tmp_path):
        from src.bot.ignore_command import ignore_done_callback, IgnoreSelectionState
        from src.alerts.ignore_manager import IgnoreManager

        manager = IgnoreManager({}, json_path=str(tmp_path / "ignores.json"))
        state = IgnoreSelectionState()
        state.set_pending(123, "sonarr", ["Connection refused to api.example.com on port 443"])
        state.toggle_selection(123, 0)

        mock_analyzer = MagicMock()
        mock_analyzer.analyze_error = AsyncMock(return_value={
            "pattern": "Connection refused to .* on port \\d+",
            "match_type": "regex",
            "explanation": "Connection refused errors",
        })

        handler = ignore_done_callback(manager, state, mock_analyzer)

        callback = AsyncMock()
        callback.data = "ign_done"
        callback.from_user = MagicMock()
        callback.from_user.id = 123
        callback.message = AsyncMock()
        callback.message.edit_text = AsyncMock()

        await handler(callback)

        mock_analyzer.analyze_error.assert_called_once()
        ignores = manager.get_all_ignores("sonarr")
        assert len(ignores) == 1
        assert ignores[0][0] == "Connection refused to .* on port \\d+"
        assert ignores[0][2] == "Connection refused errors"


class TestIgnoreCancelCallback:
    """Tests for ignore_cancel_callback."""

    @pytest.mark.asyncio
    async def test_cancel_clears_state(self):
        from src.bot.ignore_command import ignore_cancel_callback, IgnoreSelectionState

        state = IgnoreSelectionState()
        state.set_pending(123, "plex", ["Error 1"])

        handler = ignore_cancel_callback(state)

        callback = AsyncMock()
        callback.data = "ign_cancel"
        callback.from_user = MagicMock()
        callback.from_user.id = 123
        callback.message = AsyncMock()
        callback.message.edit_text = AsyncMock()

        await handler(callback)

        assert not state.has_pending(123)
        callback.answer.assert_called_once()


class TestIgnoreSimilarCallback:
    """Tests for ignore_similar_callback with timestamp stripping."""

    @pytest.mark.asyncio
    async def test_fallback_strips_timestamps(self, tmp_path):
        """Test that fallback substring pattern strips timestamps."""
        from src.bot.ignore_command import ignore_similar_callback
        from src.alerts.ignore_manager import IgnoreManager
        from src.alerts.recent_errors import RecentErrorsBuffer

        manager = IgnoreManager({}, json_path=str(tmp_path / "ignores.json"))
        buffer = RecentErrorsBuffer()

        # Add an error with a timestamp (as it would appear in the buffer)
        full_error = "[error] 2026-02-25T11:55:11.548437Z nonode@nohost <0.3827709.0> -------- Replicator failed"
        buffer.add("CouchDB", full_error)

        # No pattern analyzer — fallback path
        handler = ignore_similar_callback(manager, None, buffer)

        # Simulate callback data with timestamp-stripped preview (as sent by manager)
        # The preview would be: "[error] nonode@nohost <0.3827709.0" (truncated to fit 64 bytes)
        callback = AsyncMock()
        callback.data = "ignore_similar:CouchDB:[error] nonode@nohost <0.38"
        callback.from_user = MagicMock()
        callback.from_user.id = 123
        callback.message = AsyncMock()

        await handler(callback)

        # Verify the stored pattern does NOT contain the timestamp
        assert not manager.is_ignored("CouchDB", "some other random error")
        # But it matches the error content without timestamp
        assert manager.is_ignored("CouchDB", "[error] nonode@nohost <0.3827709.0> -------- Replicator failed")
        # And matches future errors with different timestamps
        assert manager.is_ignored("CouchDB", "[error] 2026-03-01T09:00:00Z nonode@nohost <0.3827709.0> -------- Replicator failed")

    @pytest.mark.asyncio
    async def test_buffer_lookup_strips_timestamps(self, tmp_path):
        """Test that buffer lookup strips timestamps from stored errors for comparison."""
        from src.bot.ignore_command import ignore_similar_callback
        from src.alerts.ignore_manager import IgnoreManager
        from src.alerts.recent_errors import RecentErrorsBuffer

        manager = IgnoreManager({}, json_path=str(tmp_path / "ignores.json"))
        buffer = RecentErrorsBuffer()

        # The full error in the buffer has a timestamp
        full_error = "[error] 2026-02-25T11:55:11.548437Z nonode@nohost connection refused"
        buffer.add("CouchDB", full_error)

        handler = ignore_similar_callback(manager, None, buffer)

        # The callback preview has NO timestamp (stripped by send_log_error_alert)
        callback = AsyncMock()
        callback.data = "ignore_similar:CouchDB:[error] nonode@nohost conn"
        callback.from_user = MagicMock()
        callback.from_user.id = 123
        callback.message = AsyncMock()

        await handler(callback)

        # The full error should have been found and the pattern should match
        ignores = manager.get_all_ignores("CouchDB")
        assert len(ignores) == 1
        # Pattern should be timestamp-stripped
        assert "2026-02-25" not in ignores[0][0]
        assert "nonode@nohost" in ignores[0][0]


class TestBuildIgnoreKeyboard:
    """Tests for _build_ignore_keyboard."""

    def test_keyboard_layout(self):
        from src.bot.ignore_command import _build_ignore_keyboard

        errors = ["Error 1", "Error 2", "Error 3", "Error 4", "Error 5"]
        selected = {1, 3}

        keyboard = _build_ignore_keyboard(errors, selected)
        rows = keyboard.inline_keyboard

        # First row: 4 toggle buttons
        assert len(rows[0]) == 4
        assert "\u2610" in rows[0][0].text  # ☐ 1 (not selected)
        assert "\u2611" in rows[0][1].text  # ☑ 2 (selected)
        assert "\u2610" in rows[0][2].text  # ☐ 3
        assert "\u2611" in rows[0][3].text  # ☑ 4 (selected)

        # Second row: 1 toggle button (overflow)
        assert len(rows[1]) == 1
        assert "\u2610" in rows[1][0].text  # ☐ 5

        # Third row: Select All
        assert rows[2][0].callback_data == "ign_all"

        # Fourth row: Done + Cancel
        assert rows[3][0].callback_data == "ign_done"
        assert rows[3][1].callback_data == "ign_cancel"

    def test_all_selected_shows_deselect(self):
        from src.bot.ignore_command import _build_ignore_keyboard

        errors = ["Error 1", "Error 2"]
        selected = {0, 1}

        keyboard = _build_ignore_keyboard(errors, selected)
        rows = keyboard.inline_keyboard

        # Find Select All row
        all_btn = None
        for row in rows:
            for btn in row:
                if btn.callback_data == "ign_all":
                    all_btn = btn
        assert all_btn is not None
        assert "Deselect" in all_btn.text
