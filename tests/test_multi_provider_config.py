"""Tests for multi-provider LLM config support.

Covers new env vars (OPENAI_API_KEY, OLLAMA_HOST) and new AIConfig fields
(default_provider, default_model, anthropic_prompt_caching, ollama_host).
"""

import os
from unittest.mock import patch


# ---------------------------------------------------------------------------
# Settings env-var tests
# ---------------------------------------------------------------------------


def test_openai_api_key_is_none_when_not_set():
    """OPENAI_API_KEY defaults to None when absent from env."""
    with patch.dict(os.environ, {
        "TELEGRAM_BOT_TOKEN": "test-token",
        "TELEGRAM_ALLOWED_USERS": "123",
    }, clear=True):
        from src.config import Settings

        settings = Settings(_env_file=None)
        assert settings.openai_api_key is None


def test_openai_api_key_loads_from_env():
    """OPENAI_API_KEY is read from the environment when present."""
    with patch.dict(os.environ, {
        "TELEGRAM_BOT_TOKEN": "test-token",
        "TELEGRAM_ALLOWED_USERS": "123",
        "OPENAI_API_KEY": "sk-test-key-123",
    }, clear=True):
        from src.config import Settings

        settings = Settings(_env_file=None)
        assert settings.openai_api_key == "sk-test-key-123"


def test_ollama_host_is_none_when_not_set():
    """OLLAMA_HOST defaults to None when absent from env."""
    with patch.dict(os.environ, {
        "TELEGRAM_BOT_TOKEN": "test-token",
        "TELEGRAM_ALLOWED_USERS": "123",
    }, clear=True):
        from src.config import Settings

        settings = Settings(_env_file=None)
        assert settings.ollama_host is None


def test_ollama_host_loads_from_env():
    """OLLAMA_HOST is read from the environment when present."""
    with patch.dict(os.environ, {
        "TELEGRAM_BOT_TOKEN": "test-token",
        "TELEGRAM_ALLOWED_USERS": "123",
        "OLLAMA_HOST": "http://192.168.1.50:11434",
    }, clear=True):
        from src.config import Settings

        settings = Settings(_env_file=None)
        assert settings.ollama_host == "http://192.168.1.50:11434"


# ---------------------------------------------------------------------------
# AppConfig property tests
# ---------------------------------------------------------------------------


def test_appconfig_openai_api_key_property():
    """AppConfig.openai_api_key delegates to Settings."""
    with patch.dict(os.environ, {
        "TELEGRAM_BOT_TOKEN": "test-token",
        "TELEGRAM_ALLOWED_USERS": "123",
        "OPENAI_API_KEY": "sk-from-env",
    }, clear=True):
        from src.config import Settings, AppConfig

        settings = Settings(_env_file=None)
        config = AppConfig(settings)
        assert config.openai_api_key == "sk-from-env"


def test_appconfig_openai_api_key_none():
    """AppConfig.openai_api_key returns None when env var absent."""
    with patch.dict(os.environ, {
        "TELEGRAM_BOT_TOKEN": "test-token",
        "TELEGRAM_ALLOWED_USERS": "123",
    }, clear=True):
        from src.config import Settings, AppConfig

        settings = Settings(_env_file=None)
        config = AppConfig(settings)
        assert config.openai_api_key is None


def test_appconfig_ollama_host_property():
    """AppConfig.ollama_host delegates to Settings."""
    with patch.dict(os.environ, {
        "TELEGRAM_BOT_TOKEN": "test-token",
        "TELEGRAM_ALLOWED_USERS": "123",
        "OLLAMA_HOST": "http://myhost:11434",
    }, clear=True):
        from src.config import Settings, AppConfig

        settings = Settings(_env_file=None)
        config = AppConfig(settings)
        assert config.ollama_host == "http://myhost:11434"


def test_appconfig_ollama_host_none():
    """AppConfig.ollama_host returns None when env var absent."""
    with patch.dict(os.environ, {
        "TELEGRAM_BOT_TOKEN": "test-token",
        "TELEGRAM_ALLOWED_USERS": "123",
    }, clear=True):
        from src.config import Settings, AppConfig

        settings = Settings(_env_file=None)
        config = AppConfig(settings)
        assert config.ollama_host is None


# ---------------------------------------------------------------------------
# AIConfig defaults
# ---------------------------------------------------------------------------


def test_aiconfig_default_provider_defaults_to_anthropic():
    """AIConfig.default_provider defaults to 'anthropic'."""
    from src.config import AIConfig

    ai = AIConfig.from_dict({})
    assert ai.default_provider == "anthropic"


def test_aiconfig_default_model_defaults_to_haiku():
    """AIConfig.default_model defaults to 'haiku' family name."""
    from src.config import AIConfig

    ai = AIConfig.from_dict({})
    assert ai.default_model == "haiku"


def test_aiconfig_anthropic_prompt_caching_defaults_to_true():
    """AIConfig.anthropic_prompt_caching defaults to True."""
    from src.config import AIConfig

    ai = AIConfig.from_dict({})
    assert ai.anthropic_prompt_caching is True


def test_aiconfig_ollama_host_defaults():
    """AIConfig.ollama_host defaults to localhost."""
    from src.config import AIConfig

    ai = AIConfig.from_dict({})
    assert ai.ollama_host == "http://localhost:11434"


# ---------------------------------------------------------------------------
# AIConfig parsing from YAML dict
# ---------------------------------------------------------------------------


def test_aiconfig_parses_default_provider_from_yaml():
    """AIConfig reads default_provider from YAML dict."""
    from src.config import AIConfig

    ai = AIConfig.from_dict({"default_provider": "openai"})
    assert ai.default_provider == "openai"


def test_aiconfig_parses_default_model_from_yaml():
    """AIConfig reads default_model from YAML dict."""
    from src.config import AIConfig

    ai = AIConfig.from_dict({"default_model": "gpt-4o-mini"})
    assert ai.default_model == "gpt-4o-mini"


def test_aiconfig_parses_providers_anthropic_prompt_caching():
    """AIConfig reads providers.anthropic.prompt_caching from YAML dict."""
    from src.config import AIConfig

    ai = AIConfig.from_dict({
        "providers": {
            "anthropic": {"prompt_caching": False},
        },
    })
    assert ai.anthropic_prompt_caching is False


def test_aiconfig_parses_providers_ollama_host():
    """AIConfig reads providers.ollama.host from YAML dict."""
    from src.config import AIConfig

    ai = AIConfig.from_dict({
        "providers": {
            "ollama": {"host": "http://10.0.0.5:11434"},
        },
    })
    assert ai.ollama_host == "http://10.0.0.5:11434"


def test_aiconfig_parses_all_new_fields_together():
    """AIConfig parses all new multi-provider fields from a complete YAML dict."""
    from src.config import AIConfig

    data = {
        "default_provider": "ollama",
        "default_model": "llama3:8b",
        "providers": {
            "anthropic": {"prompt_caching": False},
            "ollama": {"host": "http://gpu-server:11434"},
        },
        "models": {
            "pattern_analyzer": "claude-haiku-4-5-20251001",
            "nl_processor": "claude-sonnet-4-5-20250929",
            "diagnostic": "claude-haiku-4-5-20251001",
        },
        "max_tokens": {
            "pattern_analyzer": 500,
        },
    }
    ai = AIConfig.from_dict(data)

    # New fields
    assert ai.default_provider == "ollama"
    assert ai.default_model == "llama3:8b"
    assert ai.anthropic_prompt_caching is False
    assert ai.ollama_host == "http://gpu-server:11434"

    # Existing fields still work
    assert ai.pattern_analyzer_model == "claude-haiku-4-5-20251001"
    assert ai.nl_processor_model == "claude-sonnet-4-5-20250929"
    assert ai.diagnostic_model == "claude-haiku-4-5-20251001"
    assert ai.pattern_analyzer_max_tokens == 500


def test_aiconfig_existing_fields_unchanged():
    """Verify all existing AIConfig fields retain their defaults."""
    from src.config import AIConfig

    ai = AIConfig.from_dict({})

    # Existing model defaults (family names)
    assert ai.pattern_analyzer_model == "haiku"
    assert ai.nl_processor_model == "sonnet"
    assert ai.diagnostic_model == "haiku"

    # Existing token defaults
    assert ai.pattern_analyzer_max_tokens == 500
    assert ai.nl_processor_max_tokens == 1024
    assert ai.diagnostic_brief_max_tokens == 300
    assert ai.diagnostic_detail_max_tokens == 800

    # Existing NL defaults
    assert ai.nl_max_tool_iterations == 10
    assert ai.nl_max_conversation_exchanges == 5

    # Existing other defaults
    assert ai.pattern_analyzer_context_lines == 30
    assert ai.diagnostic_context_expiry_seconds == 600


# ---------------------------------------------------------------------------
# Full round-trip: YAML file -> AppConfig -> AIConfig
# ---------------------------------------------------------------------------


def test_full_yaml_roundtrip_with_providers(tmp_path):
    """AIConfig loads multi-provider fields from an actual YAML file via AppConfig."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text("""
ai:
  default_provider: openai
  default_model: gpt-4o-mini
  providers:
    anthropic:
      prompt_caching: false
    ollama:
      host: "http://nas:11434"
  models:
    pattern_analyzer: claude-haiku-4-5-20251001
    nl_processor: claude-sonnet-4-5-20250929
    diagnostic: claude-haiku-4-5-20251001
""")

    with patch.dict(os.environ, {
        "TELEGRAM_BOT_TOKEN": "test",
        "TELEGRAM_ALLOWED_USERS": "123",
    }):
        from src.config import Settings, AppConfig

        settings = Settings(config_path=str(config_file))
        config = AppConfig(settings)

        assert config.ai.default_provider == "openai"
        assert config.ai.default_model == "gpt-4o-mini"
        assert config.ai.anthropic_prompt_caching is False
        assert config.ai.ollama_host == "http://nas:11434"
        # Existing fields still correct
        assert config.ai.pattern_analyzer_model == "claude-haiku-4-5-20251001"


def test_full_yaml_roundtrip_defaults_when_providers_absent(tmp_path):
    """Multi-provider fields get defaults when providers section is absent in YAML."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text("""
ai:
  models:
    pattern_analyzer: claude-haiku-4-5-20251001
""")

    with patch.dict(os.environ, {
        "TELEGRAM_BOT_TOKEN": "test",
        "TELEGRAM_ALLOWED_USERS": "123",
    }):
        from src.config import Settings, AppConfig

        settings = Settings(config_path=str(config_file))
        config = AppConfig(settings)

        assert config.ai.default_provider == "anthropic"
        assert config.ai.default_model == "haiku"
        assert config.ai.anthropic_prompt_caching is True
        assert config.ai.ollama_host == "http://localhost:11434"
