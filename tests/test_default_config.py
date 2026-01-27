"""Tests for default config generation."""

import pytest
from pathlib import Path


class TestDefaultConfigGeneration:
    def test_generate_default_config(self, tmp_path):
        from src.config import generate_default_config

        config_path = tmp_path / "config.yaml"

        generate_default_config(str(config_path))

        assert config_path.exists()
        content = config_path.read_text()

        # Check key sections exist
        assert "monitoring:" in content
        assert "log_watching:" in content
        assert "memory_management:" in content
        assert "unraid:" in content

        # Check it's valid YAML with comments
        assert "#" in content  # Has comments

    def test_generate_default_does_not_overwrite(self, tmp_path):
        from src.config import generate_default_config

        config_path = tmp_path / "config.yaml"
        config_path.write_text("existing: content")

        generate_default_config(str(config_path))

        # Should not overwrite
        assert config_path.read_text() == "existing: content"
