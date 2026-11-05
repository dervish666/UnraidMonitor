"""Ignore regexes: judged on the parsed pattern, and never lost by a save.

0.22's analyzer told the model to write patterns like (\\d{1,3}\\.){3}\\d{1,3}.
The text heuristic that replaced it refused them, the loader dropped them, and
the next save erased them from disk. It also let ((z|zz))+$ through, which
takes seconds on 38 characters.
"""

import json

import pytest

from src.alerts.ignore_manager import IgnoreManager, validate_regex_pattern

# What a 0.22 install has on disk: the analyzer's regexes plus a substring.
PRE_BRANCH_FILE = {
    "nginx": [
        {
            "pattern": r"upstream timed out .* client: (\d{1,3}\.){3}\d{1,3}",
            "match_type": "regex",
            "explanation": "Upstream timeouts from any client IP",
            "added": "2026-03-01T10:00:00",
        },
        {
            "pattern": r"\[(?:\d{2}:){2}\d{2}\] heartbeat missed",
            "match_type": "regex",
            "explanation": "Heartbeat noise",
            "added": "2026-03-02T10:00:00",
        },
        {
            "pattern": "deprecated option",
            "match_type": "substring",
            "explanation": None,
            "added": "2026-03-03T10:00:00",
        },
    ],
    "plex": [
        {"pattern": r"(a+)+$", "match_type": "regex", "explanation": "hand-edited", "added": None},
    ],
}


def test_pre_branch_ignores_load_match_and_survive_a_save(tmp_path):
    path = tmp_path / "ignored_errors.json"
    path.write_text(json.dumps(PRE_BRANCH_FILE))
    manager = IgnoreManager({}, str(path))

    assert manager.is_ignored("nginx", "upstream timed out (110) client: 192.168.0.5, server")
    assert manager.is_ignored("nginx", "[12:34:56] heartbeat missed")

    # Any save: adding an unrelated pattern rewrites the file.
    manager.add_ignore_pattern("radarr", "disk quota", "substring")

    saved = json.loads(path.read_text())
    assert saved["nginx"] == PRE_BRANCH_FILE["nginx"]
    # The unsafe one is not used, but stays on disk for the user to fix.
    assert saved["plex"] == PRE_BRANCH_FILE["plex"]
    assert not manager.is_ignored("plex", "aaaa")
    assert IgnoreManager({}, str(path)).is_ignored("nginx", "[01:02:03] heartbeat missed")


@pytest.mark.parametrize("pattern", [
    r"((z|zz))+$",
    r"(a+)+$",
    r"(a|aa)*$",
    r"(.*a){20}",
    r"(\w+\s?)+$",
    r"(?:x+y?)*z",
    r"(a{1,3}){16}",
    # Polynomial: a run of overlapping repeats, not nested ones (round 3 review).
    r"z*z*z*z*z*$",
    r"\w*\w*\w*\w*Q",
    r"(z*)(z*)x",
    r"\d+\s*\d+x",
    r"\w+\w+Q",
])
def test_backtracking_shapes_are_refused(pattern):
    ok, why = validate_regex_pattern(pattern)
    assert not ok and "performance" in why


@pytest.mark.parametrize("pattern", [
    r"(\d{1,3}\.){3}\d{1,3}",
    r"(?:\d{2}:){2}\d{2}",
    r"connection (refused|reset) by peer",
    r"timeout after \d+ms",
    r"(ab)+c",
    r"^\[\w+\] .*error",
    r".*foo.*bar",
    r"error:\s*\d+",
    r"\d+\s+\d+",
])
def test_ordinary_patterns_are_allowed(pattern):
    assert validate_regex_pattern(pattern) == (True, "")


def test_overlapping_repeat_run_hand_edited_into_file_is_never_used(tmp_path):
    """z*z*z*z*z*$ passed the nesting check, loaded, and stalled on 150 z's + '!'."""
    path = tmp_path / "ignored_errors.json"
    entry = {"pattern": "z*z*z*z*z*$", "match_type": "regex", "explanation": None, "added": None}
    path.write_text(json.dumps({"sonarr": [entry]}))
    manager = IgnoreManager({}, str(path))

    assert not manager.is_ignored("sonarr", "z" * 150 + "!")  # returns at once
    manager.add_ignore_pattern("radarr", "disk quota", "substring")
    assert json.loads(path.read_text())["sonarr"] == [entry]
