def test_version_matches_pyproject():
    import pathlib
    import tomllib

    from src import __version__

    pyproject = pathlib.Path(__file__).parent.parent / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text())
    assert __version__ == data["project"]["version"]
