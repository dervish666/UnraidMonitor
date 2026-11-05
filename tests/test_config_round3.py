"""Config regressions found in round 2 of the council review.

Decimals that older releases accepted must still load, every writer must
cope with a section written as ``key:`` with nothing under it, and /setup
reusing an existing connection must save the transport its probe proved.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import yaml

from src.bot.setup_wizard import SetupWizard, UnraidProbe, create_start_handler
from src.config import AppConfig, Settings
from src.services.llm.registry import ProviderRegistry


def _settings(path: Path) -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None, telegram_bot_token="1:x", telegram_allowed_users="1",
        config_path=str(path),
    )


def _load(path: Path, text: str) -> AppConfig:
    path.write_text(text)
    return AppConfig(_settings(path))


DECIMAL_CONFIG = """
ai:
  max_tokens: {pattern_analyzer: 500.4, nl_processor: 1024.0}
  nl_processor: {max_tool_iterations: 4.6}
log_watching:
  cooldown_seconds: 900.5
resource_monitoring:
  poll_interval_seconds: "30.2"
  defaults: {cpu_percent: 92.5, memory_percent: 85.5}
  containers:
    plex: {cpu_percent: 150.5, memory_percent: 70.4}
memory_management:
  warning_threshold: 90.5
  kill_delay_seconds: 60.0
image_updates:
  enabled: true
  poll_interval_hours: 0.5
auto_heal:
  max_restarts: 3.0
  window_minutes: 30.4
unraid:
  enabled: true
  host: 192.168.0.10
  port: 443.0
  polling: {system: 30.5, array: 300.0}
  thresholds: {cpu_temp: 80.5, cpu_usage: 95.5, memory_usage: 90.2, disk_temp: 50.5, array_usage: 85.5}
nut:
  poll_seconds: 30.5
  thresholds: {battery_charge: 50.5, load: 80.5}
"""


def test_decimals_older_releases_accepted_still_load(tmp_path):
    """0.22 read these with max()/int(); 92.5 must not stop the bot at boot."""
    config = _load(tmp_path / "config.yaml", DECIMAL_CONFIG)

    assert config.ai.pattern_analyzer_max_tokens == 500
    assert config.ai.nl_max_tool_iterations == 5
    assert config.log_watching["cooldown_seconds"] == 900
    rm = config.resource_monitoring
    assert (rm.poll_interval_seconds, rm.default_cpu_percent) == (30, 92)
    assert rm.get_thresholds("plex") == (150, 70)
    assert config.memory_management.warning_threshold == 90
    # 0.5 hours rounds down and then meets the 1-hour floor, as 0.22 did.
    assert config.image_updates.poll_interval_hours == 1
    assert config.auto_heal.window_minutes == 30
    assert config.unraid.port == 443
    assert config.unraid.cpu_temp_threshold == 80
    assert config.unraid.poll_system_seconds == 30
    assert (config.nut.battery_charge_threshold, config.nut.load_threshold) == (50, 80)


def test_non_numbers_are_still_refused(tmp_path):
    import pytest

    # ValueError, not ConfigError: another test module reloads src.config.
    for bad in ("ninety", ".nan", ".inf", "[1]"):
        with pytest.raises(ValueError, match="unraid.thresholds.cpu_temp"):
            _load(tmp_path / "config.yaml", f"unraid:\n  thresholds:\n    cpu_temp: {bad}\n")


EMPTY_SECTIONS = """
ai:
resource_monitoring:
memory_management:
image_updates:
auto_heal:
unraid:
  enabled: true
  host: 192.168.0.10
  thresholds:
  notifications:
nut:
  thresholds:
"""


def test_every_writer_copes_with_an_empty_section(tmp_path):
    """``key:`` reads as None, and ``data.setdefault(key, {})`` returned it."""
    path = tmp_path / "config.yaml"
    config = _load(path, EMPTY_SECTIONS)

    config.resource_monitoring.set_threshold("plex", "cpu", 150)
    config.memory_management.set_restart_containers(["plex"])
    config.image_updates.set_enabled(True)
    config.auto_heal.set_containers(["plex"])
    config.unraid.set_threshold("cpu_temp", 85)
    config.unraid.set_notifications_enabled(True)
    config.nut.set_enabled(False)

    data = yaml.safe_load(path.read_text())
    assert data["resource_monitoring"]["containers"]["plex"]["cpu_percent"] == 150
    assert data["memory_management"]["restart_containers"] == ["plex"]
    assert data["image_updates"]["enabled"] is True
    assert data["auto_heal"]["containers"] == ["plex"]
    assert data["unraid"]["thresholds"]["cpu_temp"] == 85
    assert data["unraid"]["notifications"]["enabled"] is True
    assert data["nut"]["enabled"] is False
    assert "load" in data["nut"]["thresholds"]

    # The wizard's merge writes into the same sections.
    from src.config import ConfigWriter

    path.write_text("log_watching:\nmemory_management:\nunraid:\n")
    ConfigWriter(str(path)).merge(
        unraid_host="192.168.0.10", unraid_port=443, unraid_use_ssl=True,
        watched_containers=["plex"], protected_containers=[], ignored_containers=[],
        priority_containers=[], killable_containers=[],
    )
    data = yaml.safe_load(path.read_text())
    assert data["log_watching"]["containers"] == ["plex"]
    assert data["unraid"]["host"] == "192.168.0.10"


def test_model_persist_copes_with_an_empty_ai_section(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("ai:\n")
    registry = ProviderRegistry(config_path=str(path), data_dir=str(tmp_path))
    registry._persist_to_config(default_model="sonnet", feature="chat", feature_model="haiku")

    data = yaml.safe_load(path.read_text())
    assert data["ai"]["default_model"] == "sonnet"
    assert data["ai"]["models"] == {"chat": "haiku"}


async def test_setup_reuse_saves_the_transport_the_probe_proved(tmp_path):
    """Existing config says verify_ssl: true, but only unverified HTTPS answers."""
    path = tmp_path / "config.yaml"
    path.write_text(
        "unraid:\n  enabled: true\n  host: 192.168.0.10\n  port: 443\n"
        "  use_ssl: true\n  verify_ssl: true\n"
    )
    wizard = SetupWizard(str(path), MagicMock(), None, "key")
    wizard.test_unraid_connection = AsyncMock(  # type: ignore[method-assign]
        return_value=UnraidProbe(True, 443, True, False, "saved as verify_ssl: false")
    )
    wizard.classify_containers = AsyncMock(return_value=[])  # type: ignore[method-assign]

    message = MagicMock()
    message.from_user.id = 1
    message.answer = AsyncMock()
    await create_start_handler(wizard)(message)

    replies = " ".join(str(c.args[0]) for c in message.answer.await_args_list)
    assert "verify_ssl: false" in replies
    wizard.save_config(1, merge=True)
    assert AppConfig(_settings(path)).unraid.verify_ssl is False
