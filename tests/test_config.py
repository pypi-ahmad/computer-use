"""Tests for configuration module."""

from __future__ import annotations

from backend.config import Config, config, get_all_key_statuses, resolve_api_key


class TestConfig:
    """Validates Config singleton defaults and from_env factory."""

    def test_singleton_exists(self):
        assert config is not None

    def test_default_screen_dimensions(self):
        assert config.screen_width == 1440
        assert config.screen_height == 900

    def test_default_model(self):
        assert config.gemini_model == "gemini-3-flash-preview"

    def test_agent_service_url(self):
        c = Config(agent_service_host="127.0.0.1", agent_service_port=9222)
        assert c.agent_service_url == "http://127.0.0.1:9222"

    def test_from_env_defaults(self):
        c = Config.from_env()
        assert c.container_name == "cua-environment"
        assert c.max_steps == 50

    def test_resolve_openai_ui_key(self):
        key, source = resolve_api_key("openai", "sk-test-openai")
        assert key == "sk-test-openai"
        assert source == "ui"

    def test_key_statuses_include_openai(self):
        providers = {entry["provider"] for entry in get_all_key_statuses()}
        assert "openai" in providers
