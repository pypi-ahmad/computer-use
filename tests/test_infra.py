# === merged from tests/test_config.py ===
"""Tests for configuration module."""

from __future__ import annotations

import os
from unittest.mock import patch

from backend.infra.config import Config, config, get_all_key_statuses, resolve_api_key


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

    def test_resolve_google_accepts_gemini_alias(self):
        """``GEMINI_API_KEY`` is honored as an alias for the google provider."""
        env = {k: v for k, v in os.environ.items()
               if k not in ("GOOGLE_API_KEY", "GEMINI_API_KEY")}
        env["GEMINI_API_KEY"] = "AIza-from-gemini-alias"
        with patch.dict(os.environ, env, clear=True):
            key, source = resolve_api_key("google")
        assert key == "AIza-from-gemini-alias"
        assert source == "env"

    def test_resolve_google_prefers_canonical_over_alias(self):
        env = {k: v for k, v in os.environ.items()
               if k not in ("GOOGLE_API_KEY", "GEMINI_API_KEY")}
        env["GOOGLE_API_KEY"] = "AIza-canonical"
        env["GEMINI_API_KEY"] = "AIza-alias"
        with patch.dict(os.environ, env, clear=True):
            key, _ = resolve_api_key("google")
        assert key == "AIza-canonical"


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

# === merged from tests/test_logging_ctx.py ===
"""Regression tests for ``backend/logging_ctx.py``.

Covers the two public surfaces introduced by SC1:

* ``configure_logging(fmt="json")`` emits single-line JSON log records
  with the documented field set (``ts``, ``level``, ``logger``,
  ``msg``, ``session_id``).
* The ``session_id`` context variable flows into log records even when
  the record is emitted from a library module (via the
  ``SessionIdFilter`` installed on the root logger).
"""


import io
import json
import logging
from typing import Iterator

import pytest

from backend.infra.observability import (
    JsonFormatter,
    SessionIdFilter,
    configure_logging,
    session_context,
)


@pytest.fixture
def root_with_stream() -> Iterator[tuple[logging.Logger, io.StringIO]]:
    """A throw-away Logger with a StringIO handler.

    The real root logger is shared across pytest invocations — reusing
    it and then reconfiguring would bleed formatters into sibling
    tests. Build a private Logger with its own handler for each test.
    """
    logger = logging.getLogger("test_logging_ctx._root")
    logger.setLevel(logging.INFO)
    for h in list(logger.handlers):
        logger.removeHandler(h)
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    logger.addHandler(handler)
    logger.propagate = False
    yield logger, buf
    for h in list(logger.handlers):
        logger.removeHandler(h)


class TestJsonFormatter:
    """The formatter must emit valid JSON with the documented fields."""

    def test_fields(self, root_with_stream: tuple[logging.Logger, io.StringIO]) -> None:
        logger, buf = root_with_stream
        for h in logger.handlers:
            h.setFormatter(JsonFormatter())
            h.addFilter(SessionIdFilter())
        with session_context("abcdef01"):
            logger.info("hello")
        line = buf.getvalue().strip()
        assert line, "formatter emitted nothing"
        data = json.loads(line)
        assert set(data) >= {"ts", "level", "logger", "msg", "session_id"}
        assert data["level"] == "INFO"
        assert data["msg"] == "hello"
        assert data["session_id"] == "abcdef01"
        # Timestamp ends with 'Z' for UTC per the formatter contract.
        assert data["ts"].endswith("Z")

    def test_no_session_renders_dash(
        self, root_with_stream: tuple[logging.Logger, io.StringIO],
    ) -> None:
        """When no session is bound, the filter surfaces ``"-"`` so the
        JSON record always has a stable ``session_id`` field."""
        logger, buf = root_with_stream
        for h in logger.handlers:
            h.setFormatter(JsonFormatter())
            h.addFilter(SessionIdFilter())
        logger.info("no-session")
        data = json.loads(buf.getvalue().strip())
        assert data["session_id"] == "-"

    def test_exception_emits_exc_type_and_traceback(
        self, root_with_stream: tuple[logging.Logger, io.StringIO],
    ) -> None:
        """``logger.exception(...)`` must include ``exc_type`` and a
        stringified traceback for downstream collectors to index on."""
        logger, buf = root_with_stream
        for h in logger.handlers:
            h.setFormatter(JsonFormatter())
            h.addFilter(SessionIdFilter())
        try:
            raise ValueError("boom")
        except ValueError:
            logger.exception("caught")
        data = json.loads(buf.getvalue().strip())
        assert data["exc_type"] == "ValueError"
        assert "ValueError: boom" in data["exc"]


class TestConfigureLogging:
    """``configure_logging`` respects env vars + explicit kwargs and
    is idempotent across calls."""

    def test_json_format_via_kwarg(self) -> None:
        """Explicit fmt='json' installs the JSON formatter on the root
        logger's handlers."""
        buf = io.StringIO()
        root = logging.getLogger()
        # Reset handlers to a private StringIO handler so the assertion
        # works regardless of whether pytest captured stderr.
        orig_handlers = list(root.handlers)
        for h in orig_handlers:
            root.removeHandler(h)
        root.addHandler(logging.StreamHandler(buf))
        try:
            configure_logging(level="INFO", fmt="json")
            logger = logging.getLogger("test_logging_ctx.cfg_json")
            with session_context("feedface"):
                logger.info("configured")
            line = buf.getvalue().strip().splitlines()[-1]
            data = json.loads(line)
            assert data["session_id"] == "feedface"
            assert data["msg"] == "configured"
        finally:
            for h in list(root.handlers):
                root.removeHandler(h)
            for h in orig_handlers:
                root.addHandler(h)

    def test_unknown_format_falls_back_to_console(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An unrecognised LOG_FORMAT must not crash startup."""
        monkeypatch.setenv("LOG_FORMAT", "yaml-please")
        # Should not raise.
        configure_logging()

    def test_idempotent(self) -> None:
        """Two calls back-to-back are safe (no duplicate filters)."""
        configure_logging(fmt="console")
        configure_logging(fmt="console")
        # A handler must not have more than one SessionIdFilter.
        root = logging.getLogger()
        for handler in root.handlers:
            filters = [f for f in handler.filters if isinstance(f, SessionIdFilter)]
            assert len(filters) <= 1


class TestPrintAudit:
    """SC1 constraint: no `print()` in backend/ outside __main__ blocks.

    Enforced as a source scan rather than a runtime check. The 22
    `print()` sites we *do* have are all inside CLI output functions
    (``_print_table`` in ``certifier.py``, ``_cli`` in ``tracing.py``)
    that are reachable only via ``python -m backend.<module>``.
    """

    def test_no_print_outside_cli_entrypoints(self) -> None:
        import re
        from pathlib import Path

        repo_root = Path(__file__).resolve().parent.parent
        allowed_cli_modules = {
            # CLI output modules — ``print`` is the interface, logging
            # would be wrong here (operators want raw stdout).
            "backend/models/validation.py",
            "backend/infra/observability.py",
        }
        # Word-boundary match so substrings like ``_fingerprint(`` do
        # not false-positive on ``print(``. Also skip comment lines.
        print_re = re.compile(r"(?<![A-Za-z0-9_])print\s*\(")
        offenders: list[str] = []
        for py in (repo_root / "backend").rglob("*.py"):
            rel = py.relative_to(repo_root).as_posix()
            if rel in allowed_cli_modules:
                continue
            for i, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if print_re.search(stripped) and "# noqa: T201" not in stripped:
                    offenders.append(f"{rel}:{i}: {stripped}")
        assert not offenders, (
            "`print()` outside CLI entrypoints breaks the structured-"
            "logging contract. Convert to logger.* or mark the line "
            "with ``# noqa: T201`` with justification.\n  "
            + "\n  ".join(offenders)
        )

