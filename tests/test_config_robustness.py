"""config.yaml and .env read the way a person writes them."""

import logging

import pytest

from src.config import (
    AppConfig,
    ConfigError,
    MemoryConfig,
    Settings,
    UnraidConfig,
    describe_settings_error,
    unknown_env_file_keys,
)


def _settings(tmp_path, yaml_text: str, **env) -> Settings:
    path = tmp_path / "config.yaml"
    path.write_text(yaml_text)
    return Settings(  # type: ignore[call-arg]
        _env_file=None, telegram_bot_token="1:x", telegram_allowed_users="1",
        config_path=str(path), **env,
    )


def test_empty_sections_take_defaults(tmp_path):
    """`unraid:` with nothing under it is None in YAML; it crashed .get()."""
    config = AppConfig(_settings(tmp_path, "unraid:\nmemory_management:\nai:\nnut:\nlog_watching:\n"))
    assert config.unraid.poll_system_seconds > 0
    assert config.memory_management.enabled is False
    assert config.log_watching["error_patterns"]


def test_string_false_is_false(tmp_path):
    config = AppConfig(_settings(tmp_path, 'image_updates:\n  enabled: "false"\nnut:\n  enabled: "no"\n'))
    assert config.image_updates.enabled is False
    assert config.nut.enabled is False


def test_numeric_strings_are_numbers(tmp_path):
    config = AppConfig(_settings(tmp_path, 'unraid:\n  thresholds:\n    cpu_temp: "85"\n'))
    assert config.unraid.cpu_temp_threshold == 85


def test_bad_value_names_the_path_and_the_type(tmp_path):
    with pytest.raises(ConfigError, match=r"memory_management\.warning_threshold: expected a number, got 'high'"):
        AppConfig(_settings(tmp_path, "memory_management:\n  warning_threshold: high\n"))
    with pytest.raises(ConfigError, match=r"auto_heal\.enabled: expected true or false"):
        AppConfig(_settings(tmp_path, "auto_heal:\n  enabled: maybe\n"))
    with pytest.raises(ConfigError, match=r"unraid: expected a mapping, got list"):
        AppConfig(_settings(tmp_path, "unraid:\n  - host\n"))


def test_unknown_keys_are_named(tmp_path, caplog):
    with caplog.at_level(logging.WARNING):
        AppConfig(_settings(tmp_path, "unraid:\n  thresholds:\n    cpu_tmep: 80\nmemory_managment:\n  enabled: true\n"))
    assert "unraid.thresholds.cpu_tmep" in caplog.text
    assert "memory_managment" in caplog.text


def test_partial_log_watching_keeps_the_other_defaults(tmp_path):
    """Setting only containers dropped error_patterns, and startup KeyError'd."""
    config = AppConfig(_settings(tmp_path, "log_watching:\n  containers: [plex]\n"))
    assert config.log_watching["containers"] == ["plex"]
    assert "error" in config.log_watching["error_patterns"]
    assert config.log_watching["container_ignores"] == {}


def test_placeholder_unraid_host_is_never_polled(tmp_path):
    config = AppConfig(_settings(tmp_path, "unraid:\n  enabled: true\n  host: your-unraid-ip\n"))
    assert config.unraid.enabled is False and config.unraid.host == ""
    # NUT falls back to the Unraid host; the placeholder must not reach it.
    assert config.nut.resolve_host(config.unraid.host) == ""


def test_from_dict_still_accepts_none():
    assert UnraidConfig.from_dict(None).host == ""  # type: ignore[arg-type]
    assert MemoryConfig.from_dict({"enabled": "true"}).enabled is True


@pytest.mark.parametrize("raw,expected", [("debug", "DEBUG"), (" warn ", "WARNING"), ("nonsense", "INFO")])
def test_log_level_is_normalised(tmp_path, raw, expected):
    assert _settings(tmp_path, "", log_level=raw).log_level == expected


def test_unknown_env_file_keys_do_not_stop_the_bot(tmp_path):
    env = tmp_path / ".env"
    env.write_text("TELEGRAM_BOT_TOKEN=1:x\nTELEGRAM_ALLOWED_USERS=1\nANTHROPIC_APIKEY=sk\nPUID=99\n")
    settings = Settings(_env_file=str(env))  # type: ignore[call-arg]
    assert settings.telegram_bot_token == "1:x"
    assert unknown_env_file_keys(str(env)) == ["ANTHROPIC_APIKEY", "PUID"]


def test_settings_error_does_not_echo_secrets():
    from pydantic import ValidationError

    with pytest.raises(ValidationError) as info:
        Settings(_env_file=None, telegram_bot_token="1:SECRETTOKEN",  # type: ignore[call-arg]
                 telegram_allowed_users="abc")
    message = describe_settings_error(info.value)
    assert "TELEGRAM_ALLOWED_USERS" in message
    assert "SECRETTOKEN" not in message and "abc" not in message
