"""Ignore regexes: judged on the parsed pattern, and never lost by a save.

0.22's analyzer told the model to write patterns like (\\d{1,3}\\.){3}\\d{1,3}.
The text heuristic that replaced it refused them, the loader dropped them, and
the next save erased them from disk. It also let ((z|zz))+$ through, which
takes seconds on 38 characters.
"""

import json
import time

import pytest

from src.alerts.ignore_manager import IgnoreManager, IgnorePattern, validate_regex_pattern
from src.alerts.regex_worker import REGEX_MATCH_TIMEOUT_SECONDS, RegexWorker

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
])
def test_exponential_shapes_are_refused_on_load(pattern):
    ok, why = validate_regex_pattern(pattern)
    assert not ok and "performance" in why


@pytest.mark.parametrize("pattern", [
    r"(\d{1,3}\.){3}\d{1,3}",
    r"(?:\d{2}:){2}\d{2}",
    r"connection (refused|reset) by peer",
    r"timeout after \d+ms",
    r"(ab)+c",
    r"^\[\w+\] .*error",
    r"error:\s*\d+",
    r"^z*+z*+Q$",
    r"Connection refused to .* on port \d+",
    r".*foo.*bar",
    r"\S+@\S+",
    r"^.*error.*$",
    r"(?i)^.*(error|fail).*$",
    r"(?:GET|POST) /api/\S+ 5\d\d",
    r"[0-9a-f]{32}.*error",
    r"https?://\S+",
    r"\w+\s+\w+x",
    r"-?\d+ms",
    r"\d{1,20}:\d{1,20}",
    r"upstream timed out .* client: (\d{1,3}\.){3}\d{1,3}",
    r"\d+\s+\d+",
    # Round 6 review (Sol): ordinary filters the static run check refused.
    r"^duration=\d+(?:ms|s)? timeout$",
    r"^duration=\d+(?:\.\d+)?ms$",
])
def test_ordinary_patterns_are_allowed(pattern):
    assert validate_regex_pattern(pattern) == (True, "")


def test_probe_that_overruns_refuses_the_pattern():
    r"""\d+0\d+q passes the parse check; on the "0"*3000 probe it is cubic.

    A search holds the GIL, so the old in-process probe could not time out:
    it blocked startup for 9s and then accepted the pattern.
    """
    started = time.monotonic()
    ok, why = validate_regex_pattern(r"\d+0\d+q")
    assert not ok and "backtracking" in why
    assert time.monotonic() - started < 15  # the limit plus one worker restart


# Every polynomial shape six review rounds found past a static check, with a
# character that makes it backtrack. None can be told apart from a slow but
# fair pattern by reading it; the worker's time limit stops all of them.
SLOW_PATTERNS = [
    ("z*z*z*z*z*$", "z"),
    ("Ж*Ж*Ж*Ж*Ж*$", "Ж"),
    ("z{0,16}z{0,16}z{0,16}z{0,16}z{0,16}z{0,16}Q", "z"),
    ("[^\\x00-\\U0001d7ff]+\\d+[^\\x00-\\U0001d7ff]+\\d+Q", "\U0001E950"),
    ("z+(?>z+Q)", "z"),
    ("(?:.*error|.*fail).*timeout", "error"),
]


@pytest.mark.parametrize(("pattern", "char"), SLOW_PATTERNS)
def test_slow_hand_edited_regex_is_stopped_and_disabled(tmp_path, pattern, char):
    path = tmp_path / "ignored_errors.json"
    entry = {"pattern": pattern, "match_type": "regex", "explanation": None, "added": None}
    path.write_text(json.dumps({"sonarr": [entry]}, ensure_ascii=False))
    manager = IgnoreManager({}, str(path))
    line = "ERROR " + char * (3994 // len(char)) + "!"

    started = time.monotonic()
    assert not manager.is_ignored("sonarr", line)
    assert time.monotonic() - started < REGEX_MATCH_TIMEOUT_SECONDS + 2
    started = time.monotonic()
    assert not manager.is_ignored("sonarr", line)  # disabled: not searched again
    assert time.monotonic() - started < 0.1

    manager.add_ignore_pattern("radarr", "disk quota", "substring")
    assert json.loads(path.read_text())["sonarr"] == [entry]  # still on disk to fix


def test_worker_answers_again_after_an_overrun():
    worker = RegexWorker(timeout=0.25)
    try:
        assert worker.search(r"(?:.*error|.*fail).*timeout", ("error" * 800,)) is None
        assert worker.search(r"timeout after \d+ms", ("timeout after 30ms",)) is True
        assert worker.search(r"timeout after \d+ms", ("all good",)) is False
    finally:
        worker.close()


def test_several_slow_patterns_cost_one_limit_per_line(tmp_path):
    """Round 7 review: six overrunning regexes on one line stalled the loop 11.5s."""
    path = tmp_path / "ignored_errors.json"
    entries = [
        {"pattern": f"^ERROR z+z+z+{end}", "match_type": "regex", "explanation": None, "added": None}
        for end in "QRSTUV"
    ]
    path.write_text(json.dumps({"sonarr": entries}))
    manager = IgnoreManager({}, str(path))
    line = "ERROR " + "z" * 3993 + "!"

    for _ in range(6):  # each line stops at the first regex that overruns
        started = time.monotonic()
        assert not manager.is_ignored("sonarr", line)
        assert time.monotonic() - started < REGEX_MATCH_TIMEOUT_SECONDS + 1
    started = time.monotonic()
    assert not manager.is_ignored("sonarr", line)  # all six disabled now
    assert time.monotonic() - started < 0.1


def test_validation_runs_every_probe_even_after_a_match():
    """a|0+0+Q matches the first probe at once; the "0"*3000 probe then hangs."""
    ok, why = validate_regex_pattern(r"a|0+0+Q")
    assert not ok and "backtracking" in why


def test_worker_that_never_starts_is_reaped_and_skipped(monkeypatch):
    import src.alerts.regex_worker as rw

    monkeypatch.setattr(rw, "_CHILD", "import time; time.sleep(60)")
    monkeypatch.setattr(rw, "_START_TIMEOUT_SECONDS", 0.3)
    worker = RegexWorker()
    try:
        with pytest.raises(rw.RegexWorkerError):
            worker.search("x", ("x",))
        assert worker._proc is None  # both failed starts killed and waited for
        started = time.monotonic()
        with pytest.raises(rw.RegexWorkerError):  # backing off: no third start
            worker.search("x", ("x",))
        assert time.monotonic() - started < 0.05
    finally:
        worker.close()


def test_worker_starts_without_the_bot_imports():
    """Spawn re-imported the entry module: about 2s and 180 MiB per start."""
    worker = RegexWorker()
    try:
        started = time.monotonic()
        assert worker.search("ok", ("all ok",)) is True
        assert time.monotonic() - started < 1.0
    finally:
        worker.close()


@pytest.mark.parametrize(("pattern", "expected"), [
    (".*(GET|POST) /api/.*", "(GET|POST) /api/.*"),
    (".*?foo", "foo"),
    (".*.*foo", "foo"),
    (".*", ".*"),            # nothing left to search for: unchanged
    (".*+foo", ".*+foo"),    # possessive: not the same pattern without it
    ("(?i).*foo", "(?i).*foo"),
    (".*|x", ".*|x"),        # top-level alternation
    ("^.*error.*$", "^.*error.*$"),
])
def test_search_form_drops_only_a_leading_dot_star(pattern, expected):
    from src.alerts.ignore_manager import search_form

    assert search_form(pattern) == expected


@pytest.mark.parametrize("pattern", [
    ".*(GET|POST) /api/.*",
    ".*(GET|POST|PUT) .*",
    ".*(?:error|fail).*",
    ".*timeout.*",
])
def test_leading_dot_star_filters_load_fast(pattern):
    """Round 7 review (Opus): these took 270-370ms over the probes and were refused."""
    started = time.monotonic()
    assert validate_regex_pattern(pattern) == (True, "")
    assert time.monotonic() - started < 1.0
    assert IgnorePattern(pattern, "regex").matches("POST /api/x error timeout") is True


def test_child_killed_between_requests_is_replaced_not_raised():
    """Round 8 review: closing the buffered pipe to a dead child re-raised
    BrokenPipeError out of the restart path and aborted the log batch."""
    import os
    import signal

    worker = RegexWorker()
    try:
        assert worker.search("ok", ("ok",)) is True
        os.kill(worker._proc.pid, signal.SIGKILL)
        worker._proc.wait()
        assert worker.search("ok", ("all ok",)) is True  # restarted, answered
    finally:
        worker.close()


def test_write_to_a_stalled_child_keeps_the_deadline():
    """A child that stopped reading used to block a large request past the limit."""
    import os
    import signal

    worker = RegexWorker(timeout=0.25)
    try:
        assert worker.search("ok", ("ok",)) is True
        os.kill(worker._proc.pid, signal.SIGSTOP)
        started = time.monotonic()
        assert worker.search("x", ("\U0001F600" * 4000,) * 2) is None
        assert time.monotonic() - started < 1.0
    finally:
        worker.close()
