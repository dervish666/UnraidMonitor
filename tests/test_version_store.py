from src.utils.version_store import read_announced_version, write_announced_version


def test_read_missing_returns_none(tmp_path):
    assert read_announced_version(str(tmp_path / "nope.json")) is None


def test_write_then_read_roundtrip(tmp_path):
    p = str(tmp_path / "announced_version.json")
    write_announced_version(p, "0.12.0")
    assert read_announced_version(p) == "0.12.0"


def test_read_corrupt_returns_none(tmp_path):
    p = tmp_path / "announced_version.json"
    p.write_text("{not json")
    assert read_announced_version(str(p)) is None
