"""data/*.json: whole writes, corrupt files kept aside, failed saves retried and told."""

import json
import os
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.alerts.ignore_manager import IgnoreManager
from src.alerts.manager import ChatIdStore
from src.alerts.mute_manager import MuteManager
from src.alerts.server_mute_manager import ServerMuteManager
from src.bot.health_command import build_startup_lines
from src.bot.mute_command import mute_command
from src.models import ContainerInfo
from src.state import ContainerStateManager
from src.utils.json_store import atomic_write_json, quarantined_files
from src.utils.version_store import read_announced_version


def _corrupt_copies(directory, name):
    return [p for p in os.listdir(directory) if p.startswith(f"{name}.corrupt-")]


def test_failed_write_leaves_the_old_file_and_no_temp(tmp_path):
    path = tmp_path / "mutes.json"
    atomic_write_json(path, {"plex": "2030-01-01T00:00:00"})
    with pytest.raises(TypeError):
        atomic_write_json(path, {"plex": object()})
    assert json.loads(path.read_text()) == {"plex": "2030-01-01T00:00:00"}
    assert os.listdir(tmp_path) == ["mutes.json"]


def test_corrupt_mutes_are_set_aside_not_overwritten(tmp_path):
    path = tmp_path / "mutes.json"
    path.write_text('{"plex": "2030-01-01T00:00:00", ')  # truncated by a crash
    manager = MuteManager(json_path=str(path))
    assert manager.get_active_mutes() == []
    [aside] = _corrupt_copies(tmp_path, "mutes.json")
    assert (tmp_path / aside).read_text().startswith('{"plex"')

    manager.add_mute("radarr", timedelta(hours=1))
    # The new save writes a fresh file; the evidence is still there.
    assert (tmp_path / aside).exists()
    assert "radarr" in json.loads(path.read_text())
    assert any(aside in entry for entry in quarantined_files())


def test_wrong_root_type_is_set_aside(tmp_path):
    path = tmp_path / "chat_ids.json"
    path.write_text('{"not": "a list"}')
    store = ChatIdStore(json_path=str(path))
    assert not store.get_all_chat_ids()
    assert _corrupt_copies(tmp_path, "chat_ids.json")


def test_startup_card_names_the_file_set_aside():
    lines = build_startup_lines(
        [], version="0.0.0", ai_lines=[], ai_problems=[],
        set_aside=["mutes.json → mutes.json.corrupt-20261105T101010"],
    )
    text = "\n".join(lines)
    assert "Unreadable data file set aside" in text and "mutes.json.corrupt-" in text
    assert "1 thing needs a look" in lines[0]


def test_valid_entries_survive_a_bad_neighbour(tmp_path, caplog):
    mutes = tmp_path / "mutes.json"
    mutes.write_text(json.dumps({"plex": "2099-01-01T00:00:00", "radarr": "tomorrow", "sonarr": 5}))
    assert [k for k, _ in MuteManager(json_path=str(mutes)).get_active_mutes()] == ["plex"]
    assert "2 malformed entries" in caplog.text

    ignores = tmp_path / "ignores.json"
    ignores.write_text(json.dumps({
        "plex": ["connection reset", {"pattern": "slow query", "match_type": "substring"}, 42],
        "radarr": "not a list",
        "sonarr": [{"pattern": "", "match_type": "substring"}, {"pattern": "x", "match_type": "glob"}],
    }))
    manager = IgnoreManager({}, json_path=str(ignores))
    assert [p for _, p, _ in manager.get_runtime_ignores("plex")] == ["connection reset", "slow query"]
    assert manager.get_containers_with_runtime_ignores() == ["plex"]

    chats = tmp_path / "chat_ids.json"
    chats.write_text(json.dumps([123, "456", "abc", None, True]))
    assert sorted(ChatIdStore(json_path=str(chats)).get_all_chat_ids()) == [123, 456]
    assert json.loads(chats.read_text()) == [123, 456]


def test_corrupt_version_file_is_kept_not_deleted(tmp_path):
    path = tmp_path / "announced_version.json"
    path.write_text("{oops")
    assert read_announced_version(str(path)) is None
    assert _corrupt_copies(tmp_path, "announced_version.json")


def test_empty_file_reads_as_absent(tmp_path):
    path = tmp_path / "mutes.json"
    path.write_text("")
    assert MuteManager(json_path=str(path)).get_active_mutes() == []
    assert not _corrupt_copies(tmp_path, "mutes.json")


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
async def test_failed_save_is_told_kept_dirty_and_retried(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    path = data / "mutes.json"
    manager = MuteManager(json_path=str(path))
    state = ContainerStateManager()
    state.update(ContainerInfo("plex", "running", None, "plex:latest", None))

    message = MagicMock()
    message.text = "/mute plex 2h"
    message.reply_to_message = None
    message.answer = AsyncMock()

    os.chmod(data, 0o500)
    try:
        await mute_command(state, manager)(message)
        assert manager.save_error is not None
        assert "lasts only until the bot restarts" in message.answer.call_args.args[0]
        manager.flush()  # still read-only: must stay dirty
        assert not path.exists()
    finally:
        os.chmod(data, 0o700)

    manager.flush()
    assert "plex" in json.loads(path.read_text())
    assert manager.save_error is None


def test_server_mute_survives_a_failed_save_then_flush(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    manager = ServerMuteManager(json_path=str(data / "server_mutes.json"))
    if os.geteuid() == 0:
        pytest.skip("root ignores directory permissions")
    os.chmod(data, 0o500)
    try:
        manager.mute_server(timedelta(hours=1))
        assert manager.save_note()
    finally:
        os.chmod(data, 0o700)
    manager.flush()
    assert set(json.loads((data / "server_mutes.json").read_text())) == {"server", "array", "ups"}
    assert manager.save_note() == ""
