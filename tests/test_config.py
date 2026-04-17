"""Tests for configuration module."""

from __future__ import annotations

import os
from unittest.mock import patch

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


class TestEnvClamping:
    """S3 — numeric env values must be clamped to safe ranges."""

    def _with_env(self, **overrides):
        """Build a Config under a fully-scrubbed env so existing vars don't leak in."""
        # Start from a copy of the real env, then drop any var we intend to
        # override or that could otherwise bleed through.
        keys = {
            "SCREEN_WIDTH", "SCREEN_HEIGHT", "AGENT_SERVICE_PORT", "PORT",
            "MAX_STEPS", "STEP_TIMEOUT",
            "CUA_UI_SETTLE_DELAY", "CUA_SCREENSHOT_SETTLE_DELAY",
            "CUA_POST_ACTION_SCREENSHOT_DELAY",
        }
        scrubbed = {k: v for k, v in os.environ.items() if k not in keys}
        scrubbed.update(overrides)
        with patch.dict(os.environ, scrubbed, clear=True):
            return Config.from_env()

    def test_oversized_screen_width_clamped(self):
        c = self._with_env(SCREEN_WIDTH="2147483647")
        assert c.screen_width == 4096

    def test_undersized_screen_width_clamped(self):
        c = self._with_env(SCREEN_WIDTH="10")
        assert c.screen_width == 640

    def test_non_integer_screen_width_falls_back_to_default(self):
        c = self._with_env(SCREEN_WIDTH="not-a-number")
        assert c.screen_width == 1440

    def test_port_out_of_range_clamped(self):
        assert self._with_env(PORT="0").port == 1
        assert self._with_env(PORT="99999").port == 65535

    def test_max_steps_hard_capped(self):
        """MAX_STEPS must respect the 200-step hard cap enforced upstream."""
        assert self._with_env(MAX_STEPS="100000").max_steps == 200
        assert self._with_env(MAX_STEPS="0").max_steps == 1

    def test_step_timeout_clamped(self):
        assert self._with_env(STEP_TIMEOUT="9999").step_timeout == 600.0
        assert self._with_env(STEP_TIMEOUT="0.1").step_timeout == 1.0


class TestCorsPreflightSecurityHeaders:
    """S1 — security headers must wrap CORS preflight responses.

    Confirms the middleware registration order: ``_security_headers`` is
    registered after ``CORSMiddleware`` so it runs on the outside of the
    response stack and appends headers even to the short-circuited
    preflight response.
    """

    def test_preflight_response_has_security_headers(self):
        from fastapi.testclient import TestClient
        from backend.server import app

        client = TestClient(app)
        resp = client.options(
            "/api/health",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        # Preflight is accepted by CORSMiddleware
        assert resp.status_code == 200
        # And our security headers wrap the response
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"
        assert resp.headers.get("X-Frame-Options") == "DENY"
        assert "no-referrer" in resp.headers.get("Referrer-Policy", "")
