"""The wizard's Unraid probe: safest transport first, and a reply has to be real."""

import ssl
from unittest.mock import MagicMock, patch

import aiohttp

from src.bot.setup_wizard import SetupWizard, format_classification_summary
from src.config import AppConfig, ConfigWriter, Settings
from src.services.container_classifier import ContainerClassification

OK = {"data": {"info": {"os": {"hostname": "tower"}}}}


class _Resp:
    def __init__(self, status: int, body: object) -> None:
        self.status = status
        self._body = body

    async def json(self, content_type=None):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Raise:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def __aenter__(self):
        raise self._exc

    async def __aexit__(self, *a):
        return False


def _session(route):
    """route(url, verified: bool) -> _Resp | _Raise. Records every call."""
    calls: list[tuple[str, bool]] = []

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def post(self, url, ssl=None, **kw):
            verified = ssl is None
            calls.append((url, verified))
            return route(url, verified)

    return Session, calls


def _cert_error() -> Exception:
    return ssl.SSLCertVerificationError(1, "self-signed certificate")


async def _probe(route):
    Session, calls = _session(route)
    wizard = SetupWizard("unused.yaml", MagicMock(), None, "key")
    with patch("src.bot.setup_wizard.aiohttp.ClientSession", side_effect=lambda: Session()):
        probe = await wizard.test_unraid_connection("192.168.0.10", "key")
    return probe, calls


async def test_verified_https_is_tried_first_and_kept():
    probe, calls = await _probe(lambda url, verified: _Resp(200, OK))
    assert (probe.success, probe.use_ssl, probe.verify_ssl) == (True, True, True)
    assert calls == [("https://192.168.0.10:443/graphql", True)]


async def test_self_signed_falls_back_to_unverified_https_and_says_so():
    def route(url, verified):
        if url.startswith("https") and verified:
            return _Raise(_cert_error())
        return _Resp(200, OK)

    probe, calls = await _probe(route)
    assert (probe.success, probe.port, probe.use_ssl, probe.verify_ssl) == (True, 443, True, False)
    assert "verify_ssl: false" in probe.detail
    assert all(url.startswith("https") for url, _ in calls)  # never dropped to HTTP


async def test_graphql_errors_are_a_failure_not_a_connection():
    """An invalid key comes back as HTTP 200 with an errors list."""
    probe, calls = await _probe(
        lambda url, verified: _Resp(200, {"errors": [{"message": "Invalid API key"}]})
    )
    assert probe.success is False
    assert "Invalid API key" in probe.detail
    # The key was rejected over HTTPS; it is not resent over plain HTTP.
    assert not any(url.startswith("http://") for url, _ in calls)


async def test_rejected_key_status_does_not_retry_over_http():
    probe, calls = await _probe(lambda url, verified: _Resp(401, {}))
    assert probe.success is False and "rejected" in probe.detail
    assert calls == [("https://192.168.0.10:443/graphql", True)]


async def test_plain_http_only_when_https_is_not_served():
    def route(url, verified):
        if url.startswith("https"):
            return _Raise(aiohttp.ClientConnectionError("connection refused"))
        return _Resp(200, OK)

    probe, _ = await _probe(route)
    assert (probe.success, probe.port, probe.use_ssl) == (True, 80, False)
    assert "unencrypted" in probe.detail


def test_wizard_saves_the_verification_that_worked(tmp_path):
    path = tmp_path / "config.yaml"
    ConfigWriter(str(path)).write(
        unraid_host="192.168.0.10", unraid_port=443, unraid_use_ssl=True,
        unraid_verify_ssl=False, watched_containers=[], protected_containers=[],
        ignored_containers=[], priority_containers=[], killable_containers=[],
    )
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None, telegram_bot_token="1:x", telegram_allowed_users="1",
        config_path=str(path),
    )
    assert AppConfig(settings).unraid.verify_ssl is False


def test_container_names_are_escaped_in_the_summary():
    """binhex_plex in legacy Markdown opened an italic run and broke the message."""
    text = format_classification_summary([
        ContainerClassification(name="binhex_plex", image="x", categories={"watched"}),
    ])
    assert "binhex\\_plex" in text
